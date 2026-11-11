"""Check a running Sourced deployment end to end, using only the standard library.

    python deploy/smoke_test.py http://localhost:8000
    python deploy/smoke_test.py https://your-service.onrender.com

It checks the API, the dashboard page, the demo page and its widget script, the hosted chat page, and then asks
the demo widget a question until the handbook has been indexed and the answer cites it. A free host that is
asleep can take a minute to answer the first request.
"""

import json
import re
import sys
import time
import urllib.error
import urllib.request

QUESTION = "How many days of annual leave do I get?"


def fetch(url, *, data=None, headers=None, timeout=90):
    request = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, {k.lower(): v for k, v in error.headers.items()}, error.read().decode("utf-8", "replace")


def check(condition, message):
    if not condition:
        raise SystemExit(f"FAILED: {message}")
    print(f"ok   {message}")


def main(origin: str, wait_seconds: int) -> None:
    origin = origin.rstrip("/")
    status, _, body = fetch(origin + "/api/v1/health")
    check(status == 200 and json.loads(body) == {"status": "ok"}, "API health")

    status, headers, body = fetch(origin + "/")
    check(status == 200 and 'id="root"' in body, "dashboard page")
    check("frame-ancestors 'none'" in headers.get("content-security-policy", ""), "dashboard security headers")

    status, _, body = fetch(origin + "/demo/")
    check(status == 200, "demo page (needs SOURCED_DEMO_ADMIN_EMAIL and _PASSWORD)")
    key = re.search(r'data-site-key="(pk_[A-Za-z0-9_-]{32})"', body)
    script = re.search(r'<script src="([^"]+/widget\.js)"', body)
    check(key and script, "demo page embeds the widget")
    key = key.group(1)
    status, _, _ = fetch(script.group(1))
    check(status == 200, "widget script")
    status, _, _ = fetch(f"{origin}/c/{key}")
    check(status == 200, "hosted chat page")

    deadline = time.monotonic() + wait_seconds
    body = {"question": QUESTION, "history": []}
    while True:
        status, _, text = fetch(f"{origin}/api/v1/public/chat?site_key={key}", data=json.dumps(body).encode(), headers={
            "Content-Type": "application/json", "X-Sourced-Key": key, "Origin": origin,
        })
        answer = json.loads(text) if text.startswith("{") else {}
        if status == 200 and answer.get("status") == "answered":
            break
        if time.monotonic() > deadline:
            raise SystemExit(f"FAILED: no cited answer after {wait_seconds}s (last: HTTP {status} {text[:200]})")
        time.sleep(5)  # the worker may still be indexing the handbook
    citation = answer["citations"][0]
    check(citation["page"] >= 1 and citation["verified"], f"demo answer cites page {citation['page']}")
    print("All checks passed.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 180)
