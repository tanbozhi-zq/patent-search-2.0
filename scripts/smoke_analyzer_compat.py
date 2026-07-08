import sys

import httpx


QUERIES = [
    "tscd:(口腔数字印模仪图像采集方法)",
    "tscd:(图像采集方法)",
    "title:(口腔数字印模仪)",
    "ab:(药物组合物)",
    "type:(发明专利)",
    "applicant:(华为技术有限公司)",
    "currentAssignee:(华为技术有限公司)",
    "agency:(北京风雅颂专利代理有限公司)",
    "ipc:H02M",
]


def search(base_url: str, token: str, q: str, mode: str) -> "tuple[int, int]":
    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}/api/patent/search",
            json={
                "q": q,
                "index_analyzer_mode": mode,
                "page": 1,
                "page_size": 1,
                "ds": "cn",
                "sort": "relation",
                "highlight": 0,
            },
            headers={"X-API-Key": token} if token else {},
            timeout=60,
        )
    except httpx.HTTPError as exc:
        print(f"search network error q={q} mode={mode} error={exc}")
        return -1, -1
    if response.status_code != 200:
        return response.status_code, -1
    return response.status_code, response.json().get("total", -1)


def main() -> int:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    token = sys.argv[2] if len(sys.argv) > 2 else ""
    failures = 0

    for q in QUERIES:
        normal_status, normal_total = search(base_url, token, q, "normal")
        compat_status, compat_total = search(base_url, token, q, "compat")
        print(
            f"q={q} normal_status={normal_status} normal_total={normal_total} "
            f"compat_status={compat_status} compat_total={compat_total}"
        )
        if normal_status != 200 or compat_status != 200:
            failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
