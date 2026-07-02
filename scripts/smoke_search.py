import sys

import httpx


STAGE_6_QUERIES = [
    "tscd:(均衡)",
    "ipc:H02M AND tscd:(均衡)",
    "applicant:(华为技术有限公司)",
    "currentAssignee:(华为技术有限公司)",
    "legalStatus:(有效专利)",
    "documentYear:[2020 TO 2024]",
    "ipc:H02M AND NOT tscd:(均衡)",
    "NOT title:(外观)",
]


def main() -> int:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    api_token = sys.argv[2] if len(sys.argv) > 2 else ""
    headers = {"X-API-Key": api_token} if api_token else {}

    failures = 0
    for q in STAGE_6_QUERIES:
        payload = {
            "q": q,
            "ds": "cn",
            "sort": "relation",
            "page": 1,
            "page_size": 1,
            "highlight": 0,
        }
        try:
            response = httpx.post(
                f"{base_url.rstrip('/')}/api/patent/search",
                json=payload,
                headers=headers,
                timeout=30,
            )
        except httpx.HTTPError as exc:
            print(f"search fail q={q} status=network error={exc}")
            failures += 1
            continue

        if response.status_code != 200:
            print(f"search fail q={q} status={response.status_code}")
            failures += 1
            continue

        data = response.json()
        if "total" not in data or "records" not in data:
            print(f"search fail q={q} status=unexpected payload")
            failures += 1
            continue

        print(
            f"search ok q={q} total={data['total']} records={len(data['records'])}"
        )

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
