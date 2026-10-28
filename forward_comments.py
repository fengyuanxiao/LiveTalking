"""Forward JSONL comments from a platform adapter to LiveTalking.

This is a protocol bridge, not a platform login/comment collector. Each stdin
line is {"text": "...", "username": "...", "event_id": "..."}.
"""

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", required=True, choices=["pinduoduo", "douyin", "xiaohongshu", "kuaishou", "bilibili", "weixin", "taobao"])
    parser.add_argument("--room", required=True)
    parser.add_argument("--server", default="http://127.0.0.1:8010")
    args = parser.parse_args()
    token = os.getenv("LIVE_INGEST_TOKEN", "")
    if not token:
        parser.error("请配置 LIVE_INGEST_TOKEN，并在服务端使用相同密钥")
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            comment = json.loads(line)
            if not isinstance(comment, dict) or not isinstance(comment.get("text"), str):
                raise ValueError("每行必须为包含text的JSON对象")
            data = {"platform": args.platform, "room_id": args.room, "text": comment["text"],
                    "username": comment.get("username", "观众"), "event_id": comment.get("event_id", "")}
            request = Request(args.server.rstrip("/") + "/api/live/ingest",
                              data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
                              headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
            with urlopen(request, timeout=10) as response:
                result = json.load(response)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except (ValueError, HTTPError, URLError, TimeoutError) as exc:
            print(f"转发失败：{exc}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
