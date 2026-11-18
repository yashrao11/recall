from __future__ import annotations

from datetime import UTC, datetime
import json
import sqlite3


def create_conversation(connection: sqlite3.Connection, title: str = "New conversation") -> int:
    cursor = connection.execute("INSERT INTO conversations(title) VALUES (?)", (title[:200],))
    return int(cursor.lastrowid)


def conversation_exists(connection: sqlite3.Connection, conversation_id: int) -> bool:
    return connection.execute(
        "SELECT 1 FROM conversations WHERE conversation_id = ?", (conversation_id,)
    ).fetchone() is not None


def append_message(
    connection: sqlite3.Connection,
    conversation_id: int,
    role: str,
    content: str,
    citations: list[dict] | None = None,
) -> int:
    if role not in {"user", "assistant", "system"}:
        raise ValueError(f"unsupported message role: {role}")
    cursor = connection.execute(
        "INSERT INTO messages(conversation_id, role, content, citations_json) VALUES (?, ?, ?, ?)",
        (conversation_id, role, content, json.dumps(citations or [])),
    )
    connection.execute(
        "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
        (_utc_now(), conversation_id),
    )
    return int(cursor.lastrowid)


def list_conversations(connection: sqlite3.Connection, limit: int, offset: int) -> list[dict]:
    rows = connection.execute(
        "SELECT c.conversation_id, c.title, c.created_at, c.updated_at, "
        "COUNT(m.message_id) AS message_count "
        "FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.conversation_id "
        "GROUP BY c.conversation_id ORDER BY c.updated_at DESC, c.conversation_id DESC LIMIT ? OFFSET ?",
        (min(max(limit, 1), 100), max(offset, 0)),
    ).fetchall()
    return [dict(row) for row in rows]


def get_conversation(connection: sqlite3.Connection, conversation_id: int) -> dict | None:
    row = connection.execute(
        "SELECT conversation_id, title, created_at, updated_at FROM conversations WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    return None if row is None else dict(row)


def list_messages(connection: sqlite3.Connection, conversation_id: int, limit: int, offset: int) -> list[dict]:
    rows = connection.execute(
        "SELECT message_id, conversation_id, role, content, citations_json, created_at "
        "FROM messages WHERE conversation_id = ? ORDER BY created_at, message_id LIMIT ? OFFSET ?",
        (conversation_id, min(max(limit, 1), 100), max(offset, 0)),
    ).fetchall()
    messages = []
    for row in rows:
        item = dict(row)
        item["citations"] = json.loads(item.pop("citations_json") or "[]")
        messages.append(item)
    return messages


def history_for_prompt(connection: sqlite3.Connection, conversation_id: int, limit: int = 12) -> list[dict]:
    rows = connection.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? "
        "ORDER BY created_at DESC, message_id DESC LIMIT ?",
        (conversation_id, limit),
    ).fetchall()
    return [dict(row) for row in reversed(rows)]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
