from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, abort, g, jsonify

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
            created_at TEXT NOT NULL
        )
        """
    )
    # 既存DBに deleted_at カラムがなければ追加する（旧バージョンからの移行）
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(rooms)")}
    if "deleted_at" not in existing_cols:
        conn.execute("ALTER TABLE rooms ADD COLUMN deleted_at TEXT")
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
