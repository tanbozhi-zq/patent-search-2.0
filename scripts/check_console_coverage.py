import json
from pathlib import Path
import re
import subprocess
import sys
import textwrap

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.query.dsl_builder import build_search_dsl
from app.schemas.search import SearchRequest


CONSOLE_HTML = Path("app/static/console/index.html")

REQUIRED_QUERY_FIELDS = {
    "title",
    "ab",
    "tscd",
    "mainClaim",
    "claims",
    "description",
    "applicant",
    "currentAssignee",
    "agency",
    "agent",
    "type",
    "ipc",
    "mainIpc",
    "legalStatus",
    "applicationNumber",
    "documentNumber",
    "publicationNumber",
    "patentId",
    "ad",
    "documentYear",
}

REQUIRED_REQUEST_CONTROLS = {
    "ds",
    "sort",
    "page",
    "pageSize",
    "highlight",
}

REQUIRED_COLLAPSE_CONTROLS = {
    "searchBody",
    "searchToggle",
    "searchSummary",
    "requestBody",
    "requestToggle",
}

BUILDER_CONTRACT_CASES = [
    {
        "name": "nested text and ipc with exclusion",
        "tree": {
            "type": "group",
            "children": [
                {
                    "type": "group",
                    "connector": "AND",
                    "children": [
                        {
                            "type": "group",
                            "connector": "AND",
                            "children": [
                                {"type": "condition", "connector": "AND", "field": "title", "mode": "single", "value": "均衡"},
                                {"type": "condition", "connector": "OR", "field": "ab", "mode": "single", "value": "平衡"},
                            ],
                        },
                        {
                            "type": "group",
                            "connector": "AND",
                            "children": [
                                {"type": "condition", "connector": "AND", "field": "ipc", "mode": "single", "value": "H02M"},
                                {"type": "condition", "connector": "OR", "field": "ipc", "mode": "single", "value": "F16K"},
                            ],
                        },
                    ],
                },
                {"type": "condition", "connector": "AND NOT", "field": "description", "mode": "single", "value": "外观"},
            ],
        },
        "expected": "((title:(均衡) OR ab:(平衡)) AND (ipc:H02M OR ipc:F16K)) AND NOT description:(外观)",
    },
    {
        "name": "claims or terms with application date",
        "tree": {
            "type": "group",
            "children": [
                {"type": "condition", "connector": "AND", "field": "claims", "mode": "or", "values": ["均衡", "平衡"]},
                {
                    "type": "condition",
                    "connector": "AND",
                    "field": "ad",
                    "mode": "range",
                    "start": "2020-01-01",
                    "end": "2020-12-31",
                },
            ],
        },
        "expected": 'claims:("均衡" OR "平衡") AND ad:[2020-01-01 TO 2020-12-31]',
    },
    {
        "name": "identifier or group",
        "tree": {
            "type": "group",
            "children": [
                {
                    "type": "group",
                    "connector": "AND",
                    "children": [
                        {
                            "type": "condition",
                            "connector": "AND",
                            "field": "applicationNumber",
                            "mode": "single",
                            "value": "CN202411108082.1",
                        },
                        {
                            "type": "condition",
                            "connector": "OR",
                            "field": "documentNumber",
                            "mode": "single",
                            "value": "CN119188170B",
                        },
                    ],
                }
            ],
        },
        "expected": "(applicationNumber:CN202411108082.1 OR documentNumber:CN119188170B)",
    },
    {
        "name": "legal status custom value",
        "tree": {
            "type": "group",
            "children": [
                {
                    "type": "group",
                    "connector": "AND",
                    "children": [
                        {"type": "condition", "connector": "AND", "field": "legalStatus", "mode": "single", "value": "有效专利"},
                        {"type": "condition", "connector": "OR", "field": "legalStatus", "mode": "single", "value": "实质审查"},
                    ],
                }
            ],
        },
        "expected": "(legalStatus:(有效专利) OR legalStatus:(实质审查))",
    },
]


def _extract_console_builder_output(html: str) -> dict:
    scripts = "\n".join(re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.S))
    runner = textwrap.dedent(
        """
        const vm = require('node:vm');
        const fs = require('node:fs');

        const source = fs.readFileSync(process.argv[1], 'utf8');
        const context = {
          console,
          setTimeout,
          clearTimeout,
          performance: { now: () => 0 },
          navigator: { clipboard: { writeText: () => Promise.resolve() } },
          localStorage: { getItem: () => null, setItem: () => undefined },
          alert: () => undefined,
          fetch: async () => ({ ok: true, status: 200, json: async () => ({}) }),
          event: { currentTarget: null },
        };

        function makeElement(id) {
          const classes = new Set();
          const attributes = {};
          return {
            id,
            value: '',
            innerHTML: '',
            textContent: '',
            style: {},
            attributes,
            selectionStart: 0,
            selectionEnd: 0,
            rows: 0,
            classList: {
              add(...names) { names.forEach((name) => classes.add(name)); },
              remove(...names) { names.forEach((name) => classes.delete(name)); },
              toggle(name, force) {
                if (force === undefined) {
                  if (classes.has(name)) {
                    classes.delete(name);
                    return false;
                  }
                  classes.add(name);
                  return true;
                }
                if (force) {
                  classes.add(name);
                  return true;
                }
                classes.delete(name);
                return false;
              },
              contains(name) { return classes.has(name); },
              toString() { return Array.from(classes).join(' '); },
            },
            focus() {},
            addEventListener() {},
            setAttribute(name, value) {
              attributes[name] = String(value);
            },
            setSelectionRange(start, end) {
              this.selectionStart = start;
              this.selectionEnd = end;
            },
          };
        }

        const elements = new Map();
        context.document = {
          getElementById(id) {
            if (!elements.has(id)) elements.set(id, makeElement(id));
            return elements.get(id);
          },
          querySelectorAll() { return []; },
          querySelector() { return null; },
          createElement() {
            return {
              textContent: '',
              get innerHTML() {
                return String(this.textContent)
                  .replace(/&/g, '&amp;')
                  .replace(/</g, '&lt;')
                  .replace(/>/g, '&gt;')
                  .replace(/"/g, '&quot;');
              },
            };
          },
        };
        context.window = context;
        context.globalThis = context;

        vm.createContext(context);
        vm.runInContext(source, context, { filename: 'console-index.html' });

        const builder = context.window.consoleQueryBuilder;
        if (!builder) {
          throw new Error('window.consoleQueryBuilder is missing');
        }

        const cases = JSON.parse(process.argv[2]);
        const outputs = cases.map((item) => {
          const result = builder.buildQueryFromTree(item.tree);
          return {
            name: item.name,
            q: typeof result === 'string' ? result : result.q,
            error: typeof result === 'object' ? result.error || null : null,
          };
        });

        const searchBody = context.document.getElementById('searchBody');
        const searchToggle = context.document.getElementById('searchToggle');
        const searchSummary = context.document.getElementById('searchSummary');
        const initialSearchCollapsed = searchBody.classList.contains('collapsed');
        const initialSearchToggle = searchToggle.textContent;
        const initialSearchSummaryHidden = searchSummary.style.display === 'none';

        if (typeof context.toggleSearchPanel === 'function') {
          context.toggleSearchPanel();
        }
        const toggledSearchExpanded = !searchBody.classList.contains('collapsed');
        const toggledSearchToggle = searchToggle.textContent;

        if (typeof context.setRequestInfo === 'function') {
          context.setRequestInfo('probe request');
        }
        const requestBody = context.document.getElementById('requestBody');
        const requestToggle = context.document.getElementById('requestToggle');
        const requestPanel = context.document.getElementById('requestPanel');
        const requestCollapsedAfterInfo = requestBody.classList.contains('collapsed');
        const requestToggleAfterInfo = requestToggle.textContent;

        console.log(JSON.stringify({
          fields: builder.fields.map((field) => field.value),
          outputs,
          ui: {
            initialSearchCollapsed,
            initialSearchToggle,
            initialSearchSummaryHidden,
            toggledSearchExpanded,
            toggledSearchToggle,
            requestPanelDisplayAfterInfo: requestPanel.style.display,
            requestCollapsedAfterInfo,
            requestToggleAfterInfo,
          },
        }));
        """
    )
    completed = subprocess.run(
        ["node", "-e", runner, "/dev/stdin", json.dumps(BUILDER_CONTRACT_CASES, ensure_ascii=False)],
        input=scripts,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr.strip() or completed.stdout.strip())
    return json.loads(completed.stdout)


def main() -> int:
    html = CONSOLE_HTML.read_text(encoding="utf-8")

    missing_fields = [field for field in sorted(REQUIRED_QUERY_FIELDS) if f"value: '{field}'" not in html]
    if missing_fields:
        raise AssertionError(f"console missing query fields: {missing_fields}")

    missing_controls = [control for control in sorted(REQUIRED_REQUEST_CONTROLS) if f'id="{control}"' not in html]
    if missing_controls:
        raise AssertionError(f"console missing request controls: {missing_controls}")
    if 'id="indexAnalyzerMode"' in html or "index_analyzer_mode" in html:
        raise AssertionError("console must not expose the removed analyzer mode")

    missing_collapse_controls = [
        control for control in sorted(REQUIRED_COLLAPSE_CONTROLS) if f'id="{control}"' not in html
    ]
    if missing_collapse_controls:
        raise AssertionError(f"console missing collapse controls: {missing_collapse_controls}")

    builder_output = _extract_console_builder_output(html)
    missing_builder_fields = [
        field for field in sorted(REQUIRED_QUERY_FIELDS) if field not in set(builder_output["fields"])
    ]
    if missing_builder_fields:
        raise AssertionError(f"console query builder missing fields: {missing_builder_fields}")

    outputs = {item["name"]: item for item in builder_output["outputs"]}
    for item in BUILDER_CONTRACT_CASES:
        actual = outputs[item["name"]]
        if actual.get("error"):
            raise AssertionError(f"builder case {item['name']} failed: {actual['error']}")
        if actual["q"] != item["expected"]:
            raise AssertionError(
                f"builder case {item['name']} generated wrong q\n"
                f"expected={item['expected']!r}\nactual={actual['q']!r}"
            )
        build_search_dsl(SearchRequest(q=actual["q"]))

    ui = builder_output["ui"]
    if not ui["initialSearchCollapsed"]:
        raise AssertionError("console search controls should be collapsed by default")
    if ui["initialSearchToggle"] != "展开查询":
        raise AssertionError(f"console search toggle should say 展开查询 by default, got {ui['initialSearchToggle']!r}")
    if ui["initialSearchSummaryHidden"]:
        raise AssertionError("console search summary should be visible while controls are collapsed")
    if not ui["toggledSearchExpanded"]:
        raise AssertionError("console search controls should expand after clicking the toggle")
    if ui["toggledSearchToggle"] != "收起查询":
        raise AssertionError(f"console search toggle should say 收起查询 after expansion, got {ui['toggledSearchToggle']!r}")
    if ui["requestPanelDisplayAfterInfo"] != "block":
        raise AssertionError("request panel header should appear after request info is written")
    if not ui["requestCollapsedAfterInfo"]:
        raise AssertionError("request info body should remain collapsed by default after request info is written")
    if ui["requestToggleAfterInfo"] != "▶":
        raise AssertionError(
            f"request info toggle should show collapsed state by default, got {ui['requestToggleAfterInfo']!r}"
        )

    print("console coverage checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
