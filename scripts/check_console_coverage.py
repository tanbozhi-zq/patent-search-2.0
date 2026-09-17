"""离线校验检索控制台的字段、交互控件与查询构建契约没有遗漏。"""

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
    "independentClaims",
    "dependentClaims",
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
    "mode",
    "semanticText",
    "vectorFields",
    "topK",
    "ds",
    "sort",
    "page",
    "pageSize",
    "highlight",
}

REQUIRED_INTERACTION_CONTROLS = {
    "searchForm",
    "searchButton",
    "queryStatus",
    "advancedPanel",
    "advancedToggle",
    "requestBody",
    "requestToggle",
    "targetForm",
    "targetIdentifier",
    "targetResult",
    "targetPanel",
    "patentPanel",
    "logPanel",
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
        "name": "independent and dependent claims",
        "tree": {
            "type": "group",
            "children": [
                {
                    "type": "condition",
                    "connector": "AND",
                    "field": "independentClaims",
                    "mode": "phrase",
                    "value": "电路模块",
                },
                {
                    "type": "condition",
                    "connector": "AND",
                    "field": "dependentClaims",
                    "mode": "or",
                    "values": ["控制器", "传感器"],
                },
            ],
        },
        "expected": 'independentClaims:"电路模块" AND dependentClaims:("控制器" OR "传感器")',
    },
    {
        "name": "strict phrase normalizes Chinese quotes",
        "tree": {
            "type": "group",
            "children": [
                {
                    "type": "condition",
                    "connector": "AND",
                    "field": "ab",
                    "mode": "phrase",
                    "value": "“口腔数字印模仪器”",
                }
            ],
        },
        "expected": 'ab:"口腔数字印模仪器"',
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
        const fetchCalls = [];
        const storageWrites = [];
        const storageRemovals = [];
        const legacyStorageKey = 'patentConsole:lastSearch';
        const legacyStorageValue = JSON.stringify({
          q: 'CONFIDENTIAL-LEGACY-QUERY',
          response: { records: [{ title: 'CONFIDENTIAL-LEGACY-TITLE', abstract: 'CONFIDENTIAL-LEGACY-ABSTRACT' }] },
        });
        const localStorageValues = new Map([[legacyStorageKey, legacyStorageValue]]);
        const sessionStorageValues = new Map([[legacyStorageKey, legacyStorageValue]]);
        function storageAdapter(name, values) {
          return {
            getItem(key) { return values.has(key) ? values.get(key) : null; },
            setItem(key, value) {
              values.set(key, String(value));
              storageWrites.push({ name, key, value: String(value) });
            },
            removeItem(key) {
              values.delete(key);
              storageRemovals.push({ name, key });
            },
          };
        }
        const context = {
          console,
          setTimeout,
          clearTimeout,
          performance: { now: () => 0 },
          navigator: { clipboard: { writeText: () => Promise.resolve() } },
          localStorage: storageAdapter('localStorage', localStorageValues),
          sessionStorage: storageAdapter('sessionStorage', sessionStorageValues),
          alert: () => undefined,
          fetch: async (url, options) => {
            fetchCalls.push({ url, options });
            const isSearch = url.endsWith('/console-api/search');
            return {
              ok: true,
              status: 200,
              json: async () => isSearch ? ({
                total: 1,
                page: 1,
                page_size: 10,
                total_pages: 1,
                accessible_pages: 1,
                next_page: null,
                took_ms: 1,
                records: [{
                  id: 'patent-sensitive',
                  title: 'CONFIDENTIAL-RESULT-TITLE',
                  abstract: 'CONFIDENTIAL-RESULT-ABSTRACT',
                }],
              }) : ({
                  status: 'matched',
                  in_results: true,
                  rank: 4,
                  tied_count: 2,
                  sort_value: 1.5,
                  target: { patent_id: 'patent-1', documentNumber: 'CN100B', title: '目标专利' },
                }),
            };
          },
          event: { currentTarget: null },
        };

        function makeElement(id) {
          const classes = new Set();
          const attributes = {};
          const children = [];
          const textOf = (value) => {
            if (typeof value === 'string') return value;
            if (!value) return '';
            return value.textContent || '';
          };
          return {
            id,
            value: '',
            selectedOptions: [],
            innerHTML: '',
            textContent: '',
            children,
            dataset: {},
            style: {},
            className: '',
            hidden: false,
            disabled: false,
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
            append(...items) {
              children.push(...items);
              this.textContent += items.map(textOf).join('');
            },
            replaceChildren(...items) {
              children.splice(0, children.length, ...items);
              this.innerHTML = '';
              this.textContent = items.map(textOf).join('');
            },
            closest() { return null; },
            hasAttribute(name) {
              return Object.prototype.hasOwnProperty.call(attributes, name);
            },
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
          createElement(tagName) {
            return makeElement(tagName);
          },
          createTextNode(value) {
            return { textContent: String(value) };
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

        const advancedPanel = context.document.getElementById('advancedPanel');
        const advancedToggle = context.document.getElementById('advancedToggle');
        advancedPanel.hidden = true;
        if (typeof context.toggleAdvancedPanel === 'function') {
          context.toggleAdvancedPanel();
        }
        const advancedExpanded = !advancedPanel.hidden;
        const advancedAriaExpanded = advancedToggle.attributes['aria-expanded'];

        if (typeof context.setRequestInfo === 'function') {
          context.setRequestInfo('probe request');
        }
        const requestBody = context.document.getElementById('requestBody');
        const requestToggle = context.document.getElementById('requestToggle');
        const requestPanel = context.document.getElementById('requestPanel');
        const requestCollapsedAfterInfo = requestBody.hidden;
        const requestToggleAfterInfo = requestToggle.attributes['aria-expanded'];

        async function probeTargetValidation() {
          const mode = context.document.getElementById('mode');
          const q = context.document.getElementById('q');
          const ds = context.document.getElementById('ds');
          const sort = context.document.getElementById('sort');
          const page = context.document.getElementById('page');
          const pageSize = context.document.getElementById('pageSize');
          const highlight = context.document.getElementById('highlight');
          const target = context.document.getElementById('targetIdentifier');
          mode.value = 'boolean';
          q.value = '阀门';
          ds.value = 'cn';
          sort.value = 'relation';
          page.value = '1';
          pageSize.value = '10';
          highlight.value = '0';
          target.value = '';
          await context.testTargetRank();
          const callsWithoutTarget = fetchCalls.length;

          target.value = 'CN100B';
          await context.testTargetRank();
          const rankCall = fetchCalls[fetchCalls.length - 1];
          const rankPayload = JSON.parse(rankCall.options.body);
          const result = context.document.getElementById('targetResult');
          const matchedResult = result.textContent;

          page.value = '2';
          context.invalidateTargetValidation();
          const retainedAfterPage = result.textContent === matchedResult;

          q.value = '阀门 AND ipc:H02M';
          context.invalidateTargetValidation();
          const staleAfterQuery = result.textContent.includes('结论已过期');
          return { callsWithoutTarget, rankUrl: rankCall.url, rankPayload, retainedAfterPage, staleAfterQuery };
        }

        async function probeSearchModes() {
          const mode = context.document.getElementById('mode');
          const q = context.document.getElementById('q');
          const semanticText = context.document.getElementById('semanticText');
          const vectorFields = context.document.getElementById('vectorFields');
          const topK = context.document.getElementById('topK');
          const ds = context.document.getElementById('ds');
          const sort = context.document.getElementById('sort');
          const page = context.document.getElementById('page');
          const pageSize = context.document.getElementById('pageSize');
          const highlight = context.document.getElementById('highlight');
          ds.value = 'cn';
          page.value = '2';
          pageSize.value = '10';
          highlight.value = '0';

          const run = async (currentMode) => {
            mode.value = currentMode;
            context.updateModeControls();
            const before = fetchCalls.length;
            await context.doSearch();
            const call = fetchCalls[fetchCalls.length - 1];
            return {
              callCount: fetchCalls.length - before,
              payload: JSON.parse(call.options.body),
              booleanHidden: context.document.getElementById('booleanQueryControls').hidden,
              semanticHidden: context.document.getElementById('semanticQueryControls').hidden,
            };
          };

          q.value = 'ipc:F16K';
          semanticText.value = '';
          vectorFields.selectedOptions = [];
          topK.value = '100';
          sort.value = 'relation';
          const boolean = await run('boolean');

          q.value = 'MUST-NOT-BE-SENT';
          semanticText.value = '流体控制阀';
          vectorFields.selectedOptions = [{ value: 'abstract' }, { value: 'main_claim' }];
          topK.value = '40';
          sort.value = 'applicationDate';
          const vector = await run('vector');

          q.value = 'ipc:F16K';
          semanticText.value = '流体控制阀';
          vectorFields.selectedOptions = [{ value: 'abstract' }];
          topK.value = '100';
          sort.value = '!documentDate';
          const hybrid = await run('hybrid');

          const beforeInvalid = fetchCalls.length;
          mode.value = 'vector';
          vectorFields.selectedOptions = [];
          await context.doSearch();
          const emptyFieldsBlocked = fetchCalls.length === beforeInvalid;

          vectorFields.selectedOptions = [{ value: 'abstract' }];
          topK.value = '0';
          const beforeInvalidTopK = fetchCalls.length;
          await context.doSearch();
          const invalidTopKBlocked = fetchCalls.length === beforeInvalidTopK;
          topK.value = '100';
          const target = context.document.getElementById('targetIdentifier');
          target.value = 'CN100B';
          const beforeTarget = fetchCalls.length;
          await context.testTargetRank();
          const semanticTargetBlocked = fetchCalls.length === beforeTarget
            && context.document.getElementById('targetResult').textContent.includes('仅支持布尔模式');

          return {
            boolean,
            vector,
            hybrid,
            emptyFieldsBlocked,
            invalidTopKBlocked,
            semanticTargetBlocked,
          };
        }

        function probeScoreRendering() {
          const render = (score) => {
            context.renderResults({
              total: 1,
              page: 1,
              page_size: 10,
              total_pages: 1,
              accessible_pages: 1,
              next_page: null,
              took_ms: 1,
              records: [{ id: 'p1', title: '标题', score }],
            });
            return context.document.getElementById('records').textContent;
          };
          return { nullScore: render(null), zeroScore: render(0) };
        }

        function probePagination() {
          const pagination = context.document.getElementById('pagination');
          const render = (data) => {
            context.renderPagination(data);
            return pagination.children.map((child) => child.textContent);
          };
          return {
            beforeBoundary: render({
              page: 999,
              total_pages: 15482,
              accessible_pages: 1000,
              next_page: 1000,
            }),
            atBoundary: render({
              page: 1000,
              total_pages: 15482,
              accessible_pages: 1000,
              next_page: null,
            }),
            serverStopsEarly: render({
              page: 999,
              total_pages: 15482,
              accessible_pages: 1000,
              next_page: null,
            }),
          };
        }

        async function probeSensitiveStorage() {
          const mode = context.document.getElementById('mode');
          const q = context.document.getElementById('q');
          const page = context.document.getElementById('page');
          mode.value = 'boolean';
          q.value = 'CONFIDENTIAL-CURRENT-QUERY';
          page.value = '1';
          await context.doSearch();
          return {
            writes: storageWrites,
            removals: storageRemovals,
            localHasLegacy: localStorageValues.has(legacyStorageKey),
            sessionHasLegacy: sessionStorageValues.has(legacyStorageKey),
            localValues: Array.from(localStorageValues.values()),
            sessionValues: Array.from(sessionStorageValues.values()),
          };
        }

        probeTargetValidation().then(async (targetValidation) => {
          const searchModes = await probeSearchModes();
          const sensitiveStorage = await probeSensitiveStorage();
          console.log(JSON.stringify({
            fields: builder.fields.map((field) => field.value),
            outputs,
            ui: {
              advancedExpanded,
              advancedAriaExpanded,
              requestPanelHiddenAfterInfo: requestPanel.hidden,
              requestCollapsedAfterInfo,
              requestToggleAfterInfo,
            },
            pagination: probePagination(),
            targetValidation,
            searchModes,
            scoreRendering: probeScoreRendering(),
            sensitiveStorage,
          }));
        }).catch((error) => {
          console.error(error);
          process.exitCode = 1;
        });
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

    missing_interaction_controls = [
        control for control in sorted(REQUIRED_INTERACTION_CONTROLS) if f'id="{control}"' not in html
    ]
    if missing_interaction_controls:
        raise AssertionError(f"console missing interaction controls: {missing_interaction_controls}")
    if 'onclick="doSearch()"' in html or '生成并检索' in html:
        raise AssertionError("console must expose one primary search action")
    if 'role="status"' not in html or 'aria-live="polite"' not in html:
        raise AssertionError("console must expose an immediate live query status")
    if 'role="tablist"' not in html or 'role="tab"' not in html:
        raise AssertionError("console detail tabs must use semantic tab controls")
    if "/test/target-rank" not in html:
        raise AssertionError("console must call the target rank endpoint")
    if "短语检索" not in html or "normalizePhraseQuotes" not in html:
        raise AssertionError("console must make phrase matching discoverable and normalize Chinese quotes")

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
    if not ui["advancedExpanded"] or ui["advancedAriaExpanded"] != "true":
        raise AssertionError("advanced controls must expose their expanded state")
    if ui["requestPanelHiddenAfterInfo"]:
        raise AssertionError("request panel should appear after request info is written")
    if ui["requestCollapsedAfterInfo"]:
        raise AssertionError("request log should remain visible inside its dedicated inspector tab")
    if ui["requestToggleAfterInfo"] != "true":
        raise AssertionError(f"request info toggle should expose expanded state, got {ui['requestToggleAfterInfo']!r}")

    target = builder_output["targetValidation"]
    if target["callsWithoutTarget"] != 0:
        raise AssertionError("target validation must not call the endpoint without an identifier")
    if not target["rankUrl"].endswith("/console-api/test/target-rank"):
        raise AssertionError(f"target validation called wrong endpoint: {target['rankUrl']!r}")
    if target["rankPayload"] != {
        "q": "阀门",
        "ds": "cn",
        "sort": "relation",
        "target_identifier": "CN100B",
    }:
        raise AssertionError(f"target validation submitted wrong payload: {target['rankPayload']!r}")
    if not target["retainedAfterPage"]:
        raise AssertionError("ordinary pagination must retain the current target rank conclusion")
    if not target["staleAfterQuery"]:
        raise AssertionError("query changes must invalidate the current target rank conclusion")

    search_modes = builder_output["searchModes"]
    expected_mode_payloads = {
        "boolean": {
            "mode": "boolean",
            "q": "ipc:F16K",
            "ds": "cn",
            "sort": "relation",
            "page": 2,
            "page_size": 10,
            "highlight": 0,
        },
        "vector": {
            "mode": "vector",
            "semantic_text": "流体控制阀",
            "vector_fields": ["abstract", "main_claim"],
            "top_k": 40,
            "ds": "cn",
            "sort": "applicationDate",
            "page": 2,
            "page_size": 10,
            "highlight": 0,
        },
        "hybrid": {
            "mode": "hybrid",
            "q": "ipc:F16K",
            "semantic_text": "流体控制阀",
            "vector_fields": ["abstract"],
            "top_k": 100,
            "ds": "cn",
            "sort": "!documentDate",
            "page": 2,
            "page_size": 10,
            "highlight": 0,
        },
    }
    for mode, expected_payload in expected_mode_payloads.items():
        probe = search_modes[mode]
        if probe["callCount"] != 1 or probe["payload"] != expected_payload:
            raise AssertionError(f"console submitted wrong {mode} payload: {probe!r}")
    if search_modes["boolean"]["booleanHidden"] or not search_modes["boolean"]["semanticHidden"]:
        raise AssertionError("boolean mode must show q and hide semantic controls")
    if not search_modes["vector"]["booleanHidden"] or search_modes["vector"]["semanticHidden"]:
        raise AssertionError("vector mode must hide q and show semantic controls")
    if search_modes["hybrid"]["booleanHidden"] or search_modes["hybrid"]["semanticHidden"]:
        raise AssertionError("hybrid mode must show q and semantic controls")
    if not search_modes["emptyFieldsBlocked"]:
        raise AssertionError("semantic search must not submit without a vector field")
    if not search_modes["invalidTopKBlocked"]:
        raise AssertionError("semantic search must not submit an invalid top_k")
    if not search_modes["semanticTargetBlocked"]:
        raise AssertionError("target rank must not silently ignore semantic conditions")

    scores = builder_output["scoreRendering"]
    if "得分: 0" in scores["nullScore"]:
        raise AssertionError("null score must render as an empty value")
    if "得分: 0" not in scores["zeroScore"]:
        raise AssertionError("a real zero score must not be erased")

    pagination = builder_output["pagination"]
    if "下一页" not in pagination["beforeBoundary"]:
        raise AssertionError("the page before the result-window boundary must expose next_page")
    if "下一页" in pagination["atBoundary"]:
        raise AssertionError("the last accessible page must not expose an invalid next-page action")
    if "下一页" in pagination["serverStopsEarly"]:
        raise AssertionError("the Console must follow next_page instead of inferring navigation")
    boundary_label = " ".join(pagination["atBoundary"])
    if "第 1000 / 1000 可浏览页" not in boundary_label or "15482" not in boundary_label:
        raise AssertionError(
            f"the boundary label must distinguish accessible and logical pages: {boundary_label!r}"
        )

    sensitive_storage = builder_output["sensitiveStorage"]
    if sensitive_storage["writes"]:
        raise AssertionError(
            f"console must not persist search data: {sensitive_storage['writes']!r}"
        )
    expected_removals = {
        ("localStorage", "patentConsole:lastSearch"),
        ("sessionStorage", "patentConsole:lastSearch"),
    }
    actual_removals = {
        (item["name"], item["key"])
        for item in sensitive_storage["removals"]
    }
    if not expected_removals <= actual_removals:
        raise AssertionError(
            f"console did not clear legacy sensitive state: {actual_removals!r}"
        )
    if sensitive_storage["localHasLegacy"] or sensitive_storage["sessionHasLegacy"]:
        raise AssertionError("legacy Console search state must be removed at startup")
    persisted_values = sensitive_storage["localValues"] + sensitive_storage["sessionValues"]
    if persisted_values:
        raise AssertionError(
            f"Console search must remain memory-only, found storage values: {persisted_values!r}"
        )

    print("console coverage checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
