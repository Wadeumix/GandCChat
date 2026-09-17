import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, abort, g

app = Flask(__name__)

BASE_DIR = Path(__file__).parent

# DBの保存先。環境変数 GCC_DB_PATH で明示的に指定できる。未指定ならプロジェクト直下。
DB_PATH = Path(os.environ.get("GCC_DB_PATH", BASE_DIR / "gcc_chat.db")).expanduser()

# ルームごとの会話ログ（追記専用md）の保存先ディレクトリ。
LOGS_DIR = Path(os.environ.get("GCC_LOGS_DIR", BASE_DIR / "logs")).expanduser()

SLUG_RE = re.compile(r"[^a-zA-Z0-9\-_]+")


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


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            slug TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
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
    conn.commit()

    cur = conn.execute("SELECT COUNT(*) FROM rooms")
    if cur.fetchone()[0] == 0:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
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
    return db.execute("SELECT * FROM rooms ORDER BY created_at ASC").fetchall()


def get_room_or_404(db: sqlite3.Connection, room_id: int):
    room = db.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
    if room is None:
        abort(404)
    return room


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
    return render_template("index.html", rooms=rooms, current_room=None, messages=[])


@app.route("/rooms/<int:room_id>")
def room_view(room_id: int):
    db = get_db()
    rooms = get_rooms(db)
    current_room = get_room_or_404(db, room_id)
    messages = db.execute(
        "SELECT * FROM messages WHERE room_id = ? ORDER BY id ASC", (room_id,)
    ).fetchall()
    return render_template(
        "index.html", rooms=rooms, current_room=current_room, messages=messages
    )


@app.route("/rooms/create", methods=["POST"])
def create_room():
    db = get_db()
    name = request.form.get("name", "").strip()
    if not name:
        return redirect(url_for("rooms_index"))
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
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


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5050)
