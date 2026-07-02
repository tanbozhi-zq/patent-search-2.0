import sys

import httpx


def main() -> int:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    api_token = sys.argv[2] if len(sys.argv) > 2 else ""
    headers = {"X-API-Key": api_token} if api_token else {}
    payload = {"q": "阀门", "ds": "cn", "sort": "relation", "page": 1, "page_size": 1, "highlight": 0}

    response = httpx.post(f"{base_url.rstrip('/')}/api/patent/search", json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    data = response.json()
    if "total" not in data or "records" not in data:
        raise RuntimeError(f"unexpected search payload: {data}")
    print(f"search ok total={data['total']} records={len(data['records'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
