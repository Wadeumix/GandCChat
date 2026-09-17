from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, abort, g, jsonify

CDP_URL = os.environ.get("GCC_CHROME_CDP_URL", "http://localhost:9222")


def extract_latest_gemini_message() -> dict:
    """専用Chrome（デバッグポート接続）から、Geminiタブの最新のAI発言を抽出する。

    戻り値: {"ok": True, "text": ..., "source_url": ...} または {"ok": False, "error": ...}
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"ok": False, "error": "playwrightがインストールされていません（pip install playwright）"}

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.connect_over_cdp(CDP_URL)
            except Exception:
                return {
                    "ok": False,
                    "error": (
                        "専用Chromeに接続できません。launch_gemini_chrome.sh で"
                        "デバッグポート付きのChromeを起動してから試してください。"
                    ),
                }
            gemini_pages = [
                pg for ctx in browser.contexts for pg in ctx.pages if "gemini.google.com" in pg.url
            ]
            if not gemini_pages:
                browser.close()
                return {"ok": False, "error": "Geminiのタブが見つかりません。専用Chromeでgemini.google.comを開いてください。"}

            page = gemini_pages[0]
            model_msgs = page.query_selector_all("model-response")
            if not model_msgs:
                browser.close()
                return {"ok": False, "error": "AI発言のブロックが見つかりませんでした。ページ構造が変わった可能性があります。"}

            last = model_msgs[-1]
            content = last.query_selector("message-content")
            text = (content.inner_text() if content else last.inner_text()).strip()
            source_url = page.url
            browser.close()

            if not text:
                return {"ok": False, "error": "抽出したテキストが空でした。"}
            return {"ok": True, "text": text, "source_url": source_url}
    except Exception as exc:
        return {"ok": False, "error": f"抽出中にエラーが発生しました: {exc}"}

app = Flask(__name__)

BASE_DIR = Path(__file__).parent

# DBの保存先。環境変数 GCC_DB_PATH で明示的に指定できる。未指定ならプロジェクト直下。
DB_PATH = Path(os.environ.get("GCC_DB_PATH", BASE_DIR / "gcc_chat.db")).expanduser()

# ルームごとの会話ログ（追記専用md）の保存先ディレクトリ。
LOGS_DIR = Path(os.environ.get("GCC_LOGS_DIR", BASE_DIR / "logs")).expanduser()

SLUG_RE = re.compile(r"[^a-zA-Z0-9\-_]+")

# 削除したルームをゴミ箱に保管しておく日数。この日数を過ぎると自動で完全削除される。
ARCHIVE_DAYS = 30
TIMESTAMP_FMT = "%Y-%m-%d %H:%M"

# 相手のAIに「これはGCC Chat経由の中継である」と伝えるための注釈。
# ルームごとに初回の相手向けメッセージにのみ自動で付与する。
GCC_EXPLAINER = (
    "（この会話はG&C Chatというローカルログアプリを介して、"
    "別のAI/セッションとの間で人間が手動中継しています。"
    "これは会話の一部として記録・共有されることを前提にしています。）\n\n"
)


def make_room_slug(room_id: int, name: str) -> str:
    ascii_part = SLUG_RE.sub("-", name.strip()).strip("-").lower()
    return f"room-{room_id}-{ascii_part}" if ascii_part else f"room-{room_id}"


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.before_request
def run_purge():
    purge_expired_rooms(get_db())


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            deleted_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
            speaker TEXT NOT NULL CHECK (speaker IN ('Gemini', 'Claude')),
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            source_url TEXT,
            imported INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    # 既存DBへのカラム追加（旧バージョンからの移行）
    room_cols = {row[1] for row in conn.execute("PRAGMA table_info(rooms)")}
    if "deleted_at" not in room_cols:
        conn.execute("ALTER TABLE rooms ADD COLUMN deleted_at TEXT")
    if "last_gemini_url" not in room_cols:
        conn.execute("ALTER TABLE rooms ADD COLUMN last_gemini_url TEXT")
    if "explainer_sent" not in room_cols:
        conn.execute("ALTER TABLE rooms ADD COLUMN explainer_sent INTEGER NOT NULL DEFAULT 0")

    msg_cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    if "source_url" not in msg_cols:
        conn.execute("ALTER TABLE messages ADD COLUMN source_url TEXT")
    if "imported" not in msg_cols:
        conn.execute("ALTER TABLE messages ADD COLUMN imported INTEGER NOT NULL DEFAULT 0")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS handoffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
            target_label TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            consumed_at TEXT
        )
        """
    )
    conn.commit()

    cur = conn.execute("SELECT COUNT(*) FROM rooms")
    if cur.fetchone()[0] == 0:
        now = datetime.now().strftime(TIMESTAMP_FMT)
        room_cur = conn.execute(
            "INSERT INTO rooms (name, slug, created_at) VALUES (?, ?, ?)",
            ("雑談", "placeholder", now),
        )
        conn.execute(
            "UPDATE rooms SET slug = ? WHERE id = ?",
            (make_room_slug(room_cur.lastrowid, "雑談"), room_cur.lastrowid),
        )
        conn.commit()
    conn.close()


def purge_expired_rooms(db: sqlite3.Connection) -> None:
    cutoff = (datetime.now() - timedelta(days=ARCHIVE_DAYS)).strftime(TIMESTAMP_FMT)
    expired = db.execute(
        "SELECT * FROM rooms WHERE deleted_at IS NOT NULL AND deleted_at < ?", (cutoff,)
    ).fetchall()
    for room in expired:
        log_path = room_log_path(room["slug"])
        if log_path.exists():
            log_path.unlink()
        db.execute("DELETE FROM rooms WHERE id = ?", (room["id"],))
    if expired:
        db.commit()


def room_log_path(slug: str) -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR / f"{slug}.md"


def append_to_room_log(room_name: str, slug: str, speaker: str, body: str, timestamp: str) -> None:
    path = room_log_path(slug)
    if not path.exists():
        path.write_text(
            f"# {room_name}\n\nこのファイルは追記専用です。既存の内容は手動で編集しないでください。\n\n",
            encoding="utf-8",
        )
    entry = f"## {speaker}（{timestamp}）\n\n{body.strip()}\n\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(entry)


def get_rooms(db: sqlite3.Connection):
    return db.execute(
        "SELECT * FROM rooms WHERE deleted_at IS NULL ORDER BY created_at ASC"
    ).fetchall()


def get_archived_rooms(db: sqlite3.Connection):
    rows = db.execute(
        "SELECT * FROM rooms WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
    ).fetchall()
    now = datetime.now()
    result = []
    for row in rows:
        deleted_at = datetime.strptime(row["deleted_at"], TIMESTAMP_FMT)
        days_left = ARCHIVE_DAYS - (now - deleted_at).days
        result.append({**dict(row), "days_left": max(days_left, 0)})
    return result


def get_room_or_404(db: sqlite3.Connection, room_id: int):
    room = db.execute(
        "SELECT * FROM rooms WHERE id = ? AND deleted_at IS NULL", (room_id,)
    ).fetchone()
    if room is None:
        abort(404)
    return room


def active_name_taken(db: sqlite3.Connection, name: str, exclude_room_id: int | None = None) -> bool:
    query = "SELECT 1 FROM rooms WHERE name = ? AND deleted_at IS NULL"
    params = [name]
    if exclude_room_id is not None:
        query += " AND id != ?"
        params.append(exclude_room_id)
    return db.execute(query, params).fetchone() is not None


@app.route("/")
def root():
    db = get_db()
    rooms = get_rooms(db)
    first_room = rooms[0]["id"] if rooms else None
    if first_room is None:
        return redirect(url_for("rooms_index"))
    return redirect(url_for("room_view", room_id=first_room))


@app.route("/rooms")
def rooms_index():
    db = get_db()
    rooms = get_rooms(db)
    archived_rooms = get_archived_rooms(db)
    return render_template(
        "index.html", rooms=rooms, archived_rooms=archived_rooms, current_room=None, messages=[]
    )


@app.route("/rooms/<int:room_id>")
def room_view(room_id: int):
    db = get_db()
    rooms = get_rooms(db)
    archived_rooms = get_archived_rooms(db)
    current_room = get_room_or_404(db, room_id)
    messages = db.execute(
        "SELECT * FROM messages WHERE room_id = ? ORDER BY id ASC", (room_id,)
    ).fetchall()
    return render_template(
        "index.html",
        rooms=rooms,
        archived_rooms=archived_rooms,
        current_room=current_room,
        messages=messages,
    )


@app.route("/rooms/create", methods=["POST"])
def create_room():
    db = get_db()
    name = request.form.get("name", "").strip()
    if not name:
        return redirect(url_for("rooms_index"))
    if active_name_taken(db, name):
        return redirect(url_for("rooms_index"))
    now = datetime.now().strftime(TIMESTAMP_FMT)
    cur = db.execute(
        "INSERT INTO rooms (name, slug, created_at) VALUES (?, ?, ?)",
        (name, "placeholder", now),
    )
    room_id = cur.lastrowid
    db.execute(
        "UPDATE rooms SET slug = ? WHERE id = ?",
        (make_room_slug(room_id, name), room_id),
    )
    db.commit()
    return redirect(url_for("room_view", room_id=room_id))


@app.route("/rooms/<int:room_id>/add", methods=["POST"])
def add_message(room_id: int):
    db = get_db()
    room = get_room_or_404(db, room_id)
    speaker = request.form.get("speaker")
    body = request.form.get("body", "")
    if speaker in ("Gemini", "Claude") and body.strip():
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        db.execute(
            "INSERT INTO messages (room_id, speaker, body, created_at) VALUES (?, ?, ?, ?)",
            (room_id, speaker, body.strip(), timestamp),
        )
        db.commit()
        append_to_room_log(room["name"], room["slug"], speaker, body, timestamp)
    return redirect(url_for("room_view", room_id=room_id))


@app.route("/rooms/<int:room_id>/rename", methods=["POST"])
def rename_room(room_id: int):
    db = get_db()
    get_room_or_404(db, room_id)
    new_name = (request.form.get("name") or (request.json or {}).get("name") or "").strip()
    if not new_name:
        return jsonify({"ok": False, "error": "名前が空です"}), 400
    if active_name_taken(db, new_name, exclude_room_id=room_id):
        return jsonify({"ok": False, "error": "同じ名前のルームが既にあります"}), 400
    db.execute("UPDATE rooms SET name = ? WHERE id = ?", (new_name, room_id))
    db.commit()
    return jsonify({"ok": True, "name": new_name})


@app.route("/rooms/<int:room_id>/delete", methods=["POST"])
def delete_room(room_id: int):
    """即時削除はせず、ゴミ箱（アーカイブ）に移す。ARCHIVE_DAYS日後に自動で完全削除される。"""
    db = get_db()
    get_room_or_404(db, room_id)
    now = datetime.now().strftime(TIMESTAMP_FMT)
    db.execute("UPDATE rooms SET deleted_at = ? WHERE id = ?", (now, room_id))
    db.commit()
    remaining = get_rooms(db)
    next_room_id = remaining[0]["id"] if remaining else None
    return jsonify({"ok": True, "next_room_id": next_room_id})


@app.route("/rooms/<int:room_id>/restore", methods=["POST"])
def restore_room(room_id: int):
    db = get_db()
    room = db.execute(
        "SELECT * FROM rooms WHERE id = ? AND deleted_at IS NOT NULL", (room_id,)
    ).fetchone()
    if room is None:
        return jsonify({"ok": False, "error": "アーカイブに見つかりません"}), 404
    if active_name_taken(db, room["name"]):
        return jsonify(
            {"ok": False, "error": "同じ名前のルームが既にあります。先にそちらの名前を変更してください"}
        ), 400
    db.execute("UPDATE rooms SET deleted_at = NULL WHERE id = ?", (room_id,))
    db.commit()
    return jsonify({"ok": True, "room_id": room_id})


@app.route("/rooms/<int:room_id>/messages/<int:message_id>/copy-text", methods=["POST"])
def copy_text_for_gemini(room_id: int, message_id: int):
    """Claudeの発言をGeminiへの貼り付け用に整形して返す。

    このルームでまだ相手に一度もGCC経由だと伝えていなければ、
    GCC_EXPLAINERを本文の先頭に自動で付け、以後は付けない。
    """
    db = get_db()
    room = get_room_or_404(db, room_id)
    message = db.execute(
        "SELECT * FROM messages WHERE id = ? AND room_id = ? AND speaker = 'Claude'",
        (message_id, room_id),
    ).fetchone()
    if message is None:
        return jsonify({"ok": False, "error": "メッセージが見つかりません"}), 404

    text = message["body"]
    if not room["explainer_sent"]:
        text = GCC_EXPLAINER + text
        db.execute("UPDATE rooms SET explainer_sent = 1 WHERE id = ?", (room_id,))
        db.commit()

    return jsonify({"ok": True, "text": text})


def do_capture(room_id: int, text: str, source_url: str | None, force: bool) -> tuple[dict, int]:
    """captureの中核ロジック。(レスポンス用dict, HTTPステータス)を返す。

    重複（前回と同一本文）や別セッションの疑い（source_urlが前回と違う）を検知し、
    force=Trueが来ない限りは確認を求めるレスポンスを返す。
    """
    db = get_db()
    room = get_room_or_404(db, room_id)
    text = (text or "").strip()
    source_url = (source_url or "").strip() or None

    if not text:
        return {"ok": False, "error": "取得したテキストが空です"}, 400

    last_gemini = db.execute(
        "SELECT * FROM messages WHERE room_id = ? AND speaker = 'Gemini' "
        "ORDER BY id DESC LIMIT 1",
        (room_id,),
    ).fetchone()

    if not force and last_gemini and last_gemini["body"].strip() == text:
        return {
            "ok": False,
            "code": "duplicate",
            "error": "前回記録したGemini発言と同じ内容です。すでに取り込み済みの可能性があります。",
        }, 409

    if (
        not force
        and source_url
        and room["last_gemini_url"]
        and room["last_gemini_url"] != source_url
    ):
        return {
            "ok": False,
            "code": "different_session",
            "error": (
                "前回このルームに記録した時と、GeminiのURL（会話ID）が異なります。"
                "別のGemini会話から取得しようとしている可能性があります。"
            ),
        }, 409

    timestamp = datetime.now().strftime(TIMESTAMP_FMT)
    db.execute(
        "INSERT INTO messages (room_id, speaker, body, created_at, source_url) "
        "VALUES (?, 'Gemini', ?, ?, ?)",
        (room_id, text, timestamp, source_url),
    )
    if source_url:
        db.execute("UPDATE rooms SET last_gemini_url = ? WHERE id = ?", (source_url, room_id))
    db.commit()
    append_to_room_log(room["name"], room["slug"], "Gemini", text, timestamp)
    return {"ok": True}, 200


@app.route("/rooms/<int:room_id>/capture", methods=["POST"])
def capture_message(room_id: int):
    """ブラウザ拡張/クライアント側ですでに抽出済みのテキストを取り込む。"""
    payload = request.get_json(silent=True) or {}
    result, status = do_capture(
        room_id, payload.get("text"), payload.get("source_url"), bool(payload.get("force"))
    )
    return jsonify(result), status


@app.route("/rooms/<int:room_id>/capture-now", methods=["POST"])
def capture_now(room_id: int):
    """専用Chromeから今すぐGeminiの最新発言を抽出し、そのまま/captureのロジックに渡す。"""
    get_room_or_404(get_db(), room_id)
    force = bool((request.get_json(silent=True) or {}).get("force"))

    extracted = extract_latest_gemini_message()
    if not extracted["ok"]:
        return jsonify(extracted), 400

    result, status = do_capture(room_id, extracted["text"], extracted["source_url"], force)
    return jsonify(result), status


@app.route("/rooms/<int:room_id>/import-share", methods=["POST"])
def import_share(room_id: int):
    """GCC導入前の会話を、共有リンクの中身（貼り付けたテキスト）で一括インポートする。

    Gemini/claude.aiの共有ページはクライアント側で描画されるSPAのため、
    サーバー側から自動取得しても中身が空になることが多い。
    そのため確実性を優先し、ユーザーが共有ページを開いて全文コピペしたテキストを
    そのまま「導入前の文脈」として1件にまとめて記録する。
    """
    db = get_db()
    room = get_room_or_404(db, room_id)
    text = (request.get_json(silent=True) or {}).get("text", "").strip()
    if not text:
        return jsonify({"ok": False, "error": "インポートするテキストが空です"}), 400

    timestamp = datetime.now().strftime(TIMESTAMP_FMT)
    body = "【GCC導入前の会話をインポート】\n\n" + text
    db.execute(
        "INSERT INTO messages (room_id, speaker, body, created_at, imported) "
        "VALUES (?, 'Gemini', ?, ?, 1)",
        (room_id, body, timestamp),
    )
    db.commit()
    append_to_room_log(room["name"], room["slug"], "Gemini", body, timestamp)
    return jsonify({"ok": True})


@app.route("/rooms/<int:room_id>/handoffs", methods=["POST"])
def create_handoff(room_id: int):
    """このルームの内容を、指定したラベルのセッション宛てにメールボックスへ積む。

    target_labelは自由文字列（例: "このセッション", "trading-bot-cli"）。
    contentが指定されなければ、ルームの全ログを渡す。
    """
    db = get_db()
    room = get_room_or_404(db, room_id)
    payload = request.get_json(silent=True) or {}
    target_label = (payload.get("target_label") or "").strip()
    content = (payload.get("content") or "").strip()

    if not target_label:
        return jsonify({"ok": False, "error": "送信先ラベルが空です"}), 400

    if not content:
        messages = db.execute(
            "SELECT * FROM messages WHERE room_id = ? ORDER BY id ASC", (room_id,)
        ).fetchall()
        lines = [f"# {room['name']}（G&C Chatより中継）\n"]
        for m in messages:
            lines.append(f"## {m['speaker']}（{m['created_at']}）\n\n{m['body']}\n")
        content = "\n".join(lines)

    timestamp = datetime.now().strftime(TIMESTAMP_FMT)
    db.execute(
        "INSERT INTO handoffs (room_id, target_label, content, created_at) VALUES (?, ?, ?, ?)",
        (room_id, target_label, content, timestamp),
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/handoffs", methods=["GET"])
def list_handoffs():
    """target指定で自分宛の未受信分を取得する。status=allで受信済みも含める。"""
    db = get_db()
    target = request.args.get("target", "").strip()
    status = request.args.get("status", "pending")
    if not target:
        return jsonify({"ok": False, "error": "targetクエリパラメータが必要です"}), 400

    query = "SELECT h.*, r.name AS room_name FROM handoffs h JOIN rooms r ON r.id = h.room_id WHERE h.target_label = ?"
    params = [target]
    if status == "pending":
        query += " AND h.consumed_at IS NULL"
    query += " ORDER BY h.id ASC"

    rows = db.execute(query, params).fetchall()
    return jsonify({"ok": True, "handoffs": [dict(r) for r in rows]})


@app.route("/handoffs/<int:handoff_id>/ack", methods=["POST"])
def ack_handoff(handoff_id: int):
    """受信済みにマークする。"""
    db = get_db()
    handoff = db.execute("SELECT * FROM handoffs WHERE id = ?", (handoff_id,)).fetchone()
    if handoff is None:
        return jsonify({"ok": False, "error": "見つかりません"}), 404
    timestamp = datetime.now().strftime(TIMESTAMP_FMT)
    db.execute("UPDATE handoffs SET consumed_at = ? WHERE id = ?", (timestamp, handoff_id))
    db.commit()
    return jsonify({"ok": True})


@app.route("/rooms/<int:room_id>/purge", methods=["POST"])
def purge_room_now(room_id: int):
    """ゴミ箱の中身をユーザーの意思で即座に完全削除する。"""
    db = get_db()
    room = db.execute(
        "SELECT * FROM rooms WHERE id = ? AND deleted_at IS NOT NULL", (room_id,)
    ).fetchone()
    if room is None:
        return jsonify({"ok": False, "error": "アーカイブに見つかりません"}), 404
    log_path = room_log_path(room["slug"])
    if log_path.exists():
        log_path.unlink()
    db.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
    db.commit()
    return jsonify({"ok": True})


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5050)
