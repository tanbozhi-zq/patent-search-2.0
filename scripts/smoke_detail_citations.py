import json
import sys
import urllib.error
import urllib.request
from typing import Optional, Tuple


def _request(url: str, token: Optional[str] = None) -> Tuple[int, dict]:
    headers = {}
    if token:
        headers["X-API-Key"] = token
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return exc.code, json.loads(body)


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: smoke_detail_citations.py BASE_URL PATENT_ID [API_TOKEN]")
        return 2

    base_url = sys.argv[1].rstrip("/")
    patent_id = sys.argv[2]
    token = sys.argv[3] if len(sys.argv) > 3 else None

    checks = [
        ("detail", f"{base_url}/api/patent/detail/{patent_id}"),
        ("detail_description", f"{base_url}/api/patent/detail/{patent_id}?include_description=true"),
        ("citations", f"{base_url}/api/patent/citations/{patent_id}"),
    ]

    failed = False
    for name, url in checks:
        status, data = _request(url, token)
        keys = sorted(data.keys()) if isinstance(data, dict) else []
        print(json.dumps({"name": name, "status": status, "keys": keys}, ensure_ascii=False))
        if status != 200:
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())