"""G&C Chat をネイティブウィンドウのデスクトップアプリとして起動するエントリポイント。

Flaskサーバーをバックグラウンドスレッドで動かし、pywebviewでその画面を
ブラウザのURLバー等が無いネイティブウィンドウとして表示する。
Gemini取り込み用の専用Chromeは引き続き別ウィンドウ（launch_gemini_chrome.sh）。
"""
import socket
import threading
import time

import webview

from app import app, init_db

HOST = "127.0.0.1"
PORT = 5050


def _port_is_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _run_flask() -> None:
    # デスクトップ実行時はリローダー(debug)を無効化する。
    # リローダーはこのプロセス自体を再起動する仕組みで、pywebviewとは相性が悪い。
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False)


def main() -> None:
    init_db()

    server_thread = threading.Thread(target=_run_flask, daemon=True)
    server_thread.start()

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not _port_is_open(HOST, PORT):
        time.sleep(0.2)

    webview.create_window(
        "G&C Chat",
        f"http://{HOST}:{PORT}/",
        width=1100,
        height=800,
        min_size=(700, 500),
    )
    webview.start()


if __name__ == "__main__":
    main()
