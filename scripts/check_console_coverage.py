from pathlib import Path


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
    "indexAnalyzerMode",
}


def main() -> int:
    html = CONSOLE_HTML.read_text(encoding="utf-8")

    missing_fields = [field for field in sorted(REQUIRED_QUERY_FIELDS) if f"value: '{field}'" not in html]
    if missing_fields:
        raise AssertionError(f"console missing query fields: {missing_fields}")

    missing_controls = [control for control in sorted(REQUIRED_REQUEST_CONTROLS) if f'id="{control}"' not in html]
    if missing_controls:
        raise AssertionError(f"console missing request controls: {missing_controls}")

    print("console coverage checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
