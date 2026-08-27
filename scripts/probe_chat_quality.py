from __future__ import annotations

import json
import os
import urllib.request

os.environ.setdefault("PYTHONUTF8", "1")

QUERIES = [
    "Quyền lợi BHYT khi khám trái tuyến là gì?",
    "Khám chữa bệnh trái tuyến được hưởng BHYT như thế nào?",
    "Nếu tôi khám không đúng nơi đăng ký ban đầu thì BHYT chi trả ra sao?",
]

for query in QUERIES:
    data = json.dumps({"message": query}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "http://localhost:8000/api/v1/chat",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        raw = urllib.request.urlopen(req, timeout=180).read().decode("utf-8")
        payload = json.loads(raw)
        print("=" * 80)
        print("Q:", query)
        print("citations:", len(payload.get("citations") or []))
        print("response:", (payload.get("response") or "")[:500])
        if payload.get("citations"):
            first = payload["citations"][0]
            print("first_cite:", first.get("title"), first.get("section_title"))
    except Exception as exc:
        print("=" * 80)
        print("Q:", query)
        print("ERROR:", exc)
