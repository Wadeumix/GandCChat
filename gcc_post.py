#!/usr/bin/env python3
"""G&C Chatのルームに発言を1件追加するヘルパー。

Claude Codeセッションがこのルームにリンクされた際、
mdログへの直接追記（形式を推測する必要があり壊れやすい）ではなく、
このスクリプト経由でGCC ChatのAPIを叩くことで確実に記録する。

使い方:
    # 引数でテキストを渡す
    python3 gcc_post.py --room-id 1 --speaker Claude --text "本文"

    # 標準入力から渡す（複数行や引用符を含む場合に安全）
    echo "本文" | python3 gcc_post.py --room-id 1 --speaker Claude
    python3 gcc_post.py --room-id 1 --speaker Claude < reply.txt
"""
import argparse
import sys
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:5050"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--room-id", required=True, type=int, help="投稿先のルームID")
    parser.add_argument("--speaker", required=True, choices=["Gemini", "Claude"], help="発言者")
    parser.add_argument("--text", help="本文（省略時は標準入力から読む）")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="GCC ChatのURL")
    args = parser.parse_args()

    text = args.text
    if text is None:
        text = sys.stdin.read()
    text = text.strip()

    if not text:
        print("エラー: 本文が空です", file=sys.stderr)
        return 1

    url = f"{args.base_url}/rooms/{args.room_id}/add"
    data = urllib.parse.urlencode({"speaker": args.speaker, "body": text}).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as exc:
        print(f"エラー: GCC Chatへの投稿に失敗しました（{exc}）。起動していますか？", file=sys.stderr)
        return 1

    print(f"記録しました（room_id={args.room_id}, speaker={args.speaker}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
