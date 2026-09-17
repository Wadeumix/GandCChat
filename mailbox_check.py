#!/usr/bin/env python3
"""G&C Chatのメールボックスを確認するヘルパー。

このセッション以外のClaude Codeセッション（別のCLIプロセス等）から、
自分宛に届いている受け渡し（handoff）を取得するために使う。

使い方:
    python3 mailbox_check.py --target "trading-bot-cli"
    python3 mailbox_check.py --target "trading-bot-cli" --ack   # 取得後に既読にする

常時ポーリングはしない。必要な時に都度実行する。
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:5050"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="自分の送信先ラベル（GCC側で指定したもの）")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="GCC ChatのURL")
    parser.add_argument("--ack", action="store_true", help="取得した分をその場で既読にする")
    parser.add_argument("--all", action="store_true", help="既読済みも含めて全部表示する")
    args = parser.parse_args()

    status = "all" if args.all else "pending"
    query = urllib.parse.urlencode({"target": args.target, "status": status})
    url = f"{args.base_url}/handoffs?{query}"

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.load(resp)
    except Exception as exc:
        print(f"エラー: GCC Chatに接続できませんでした（{exc}）。起動していますか？", file=sys.stderr)
        return 1

    if not data.get("ok"):
        print(f"エラー: {data.get('error')}", file=sys.stderr)
        return 1

    handoffs = data.get("handoffs", [])
    if not handoffs:
        print(f"「{args.target}」宛の未読はありません。")
        return 0

    for h in handoffs:
        print(f"=== [{h['id']}] room: {h['room_name']} / {h['created_at']} ===")
        print(h["content"])
        print()
        if args.ack:
            ack_url = f"{args.base_url}/handoffs/{h['id']}/ack"
            try:
                urllib.request.urlopen(urllib.request.Request(ack_url, method="POST"), timeout=10)
            except Exception as exc:
                print(f"警告: id={h['id']} の既読化に失敗しました（{exc}）", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
