"""请求服务健康端点并验证其固定的轻量成功契约。"""

import sys

import httpx


def main() -> int:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    response = httpx.get(f"{base_url.rstrip('/')}/health", timeout=10)
    response.raise_for_status()
    payload = response.json()
    if payload.get("data", {}).get("status") != "healthy":
        raise RuntimeError(f"unexpected health payload: {payload}")
    print("health ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
