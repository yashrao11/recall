from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
import sqlite3
import json
from pathlib import Path


MEMORY_EVENT_TYPES = {
    "file_imported",
    "file_modified",
    "webpage_saved",
    "note_created",
    "screenshot_captured",
    "study_session_completed",
    "memory_revisited",
}


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS folder_mounts (
    mount_id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    label TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sources (
    source_id INTEGER PRIMARY KEY AUTOINCREMENT,
    mount_id INTEGER REFERENCES folder_mounts(mount_id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,
    title TEXT,
    original_path TEXT NOT NULL UNIQUE,
    mime_type TEXT,
    content_hash TEXT,
    created_at TEXT,
    modified_at TEXT,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    captured_at TEXT,
    indexing_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (indexing_status IN ('pending', 'processing', 'done', 'partial', 'error')),
    indexing_error TEXT,
    excluded INTEGER NOT NULL DEFAULT 0 CHECK (excluded IN (0, 1))
);

CREATE TABLE IF NOT EXISTS document_units (
    unit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    unit_type TEXT NOT NULL,
    page_or_slide INTEGER,
    timestamp_sec REAL,
    extracted_text TEXT NOT NULL DEFAULT '',
    short_comment TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    thumbnail_path TEXT,
    text_embedding_ref TEXT,
    visual_embedding_ref TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS fts_units USING fts5(
    unit_id UNINDEXED,
    source_id UNINDEXED,
    extracted_text,
    short_comment,
    tags,
    tokenize='porter ascii'
);

CREATE TABLE IF NOT EXISTS api_cache (
    cache_key TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    provider TEXT NOT NULL,
    operation TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_api_cache_content ON api_cache(content_hash);

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT 'New conversation',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    citations_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at, message_id);

CREATE TABLE IF NOT EXISTS memory_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    source_id INTEGER REFERENCES sources(source_id) ON DELETE CASCADE,
    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    date_basis TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS objects (
    object_id INTEGER PRIMARY KEY AUTOINCREMENT,
    object_type TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS object_evidence (
    object_id INTEGER NOT NULL REFERENCES objects(object_id) ON DELETE CASCADE,
    source_id INTEGER NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    unit_id INTEGER REFERENCES document_units(unit_id) ON DELETE CASCADE,
    PRIMARY KEY (object_id, source_id, unit_id)
);

CREATE TABLE IF NOT EXISTS relationships (
    relationship_id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_object_id INTEGER NOT NULL REFERENCES objects(object_id) ON DELETE CASCADE,
    to_object_id INTEGER NOT NULL REFERENCES objects(object_id) ON DELETE CASCADE,
    relationship_type TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    created_by TEXT NOT NULL CHECK (created_by IN ('llm', 'system', 'user')),
    reviewed_status TEXT NOT NULL DEFAULT 'unreviewed'
        CHECK (reviewed_status IN ('unreviewed', 'confirmed', 'rejected'))
);

CREATE TABLE IF NOT EXISTS study_sessions (
    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
    object_id INTEGER REFERENCES objects(object_id) ON DELETE SET NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    questions_attempted INTEGER NOT NULL DEFAULT 0,
    answers_correct INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS flashcards (
    card_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER REFERENCES sources(source_id) ON DELETE CASCADE,
    unit_id INTEGER REFERENCES document_units(unit_id) ON DELETE SET NULL,
    card_type TEXT NOT NULL,
    front TEXT NOT NULL,
    back TEXT NOT NULL,
    options_json TEXT,
    next_review TEXT,
    ease_factor REAL NOT NULL DEFAULT 2.5,
    interval_days INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_sources_mount ON sources(mount_id);
CREATE INDEX IF NOT EXISTS idx_sources_status ON sources(indexing_status);
CREATE INDEX IF NOT EXISTS idx_units_source ON document_units(source_id);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON memory_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_flashcards_review ON flashcards(next_review);
"""


def connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(database_path: Path) -> None:
    with connect(database_path) as connection:
        connection.executescript(SCHEMA)
        _migrate_legacy_schema(connection)


def _migrate_legacy_schema(connection: sqlite3.Connection) -> None:
    """Upgrade the initial foundation schema without touching source originals."""
    source_schema_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'sources'"
    ).fetchone()
    source_schema = source_schema_row[0] if source_schema_row else None
    if source_schema and "'skipped'" in source_schema:
        # SQLite does not support altering a CHECK constraint. Update only the
        # legacy sources-table definition, then invalidate SQLite's schema
        # cache so existing installations can persist ``partial`` statuses.
        schema_version = connection.execute("PRAGMA schema_version").fetchone()[0]
        connection.execute("PRAGMA writable_schema = ON")
        connection.execute(
            "UPDATE sqlite_master SET sql = ? WHERE type = 'table' AND name = 'sources'",
            (source_schema.replace("'skipped'", "'partial'"),),
        )
        connection.execute("PRAGMA writable_schema = OFF")
        connection.execute(f"PRAGMA schema_version = {int(schema_version) + 1}")

    columns = {row["name"] for row in connection.execute("PRAGMA table_info(document_units)")}
    if "short_comment" not in columns:
        connection.execute("ALTER TABLE document_units ADD COLUMN short_comment TEXT NOT NULL DEFAULT ''")
    if "tags_json" not in columns:
        connection.execute("ALTER TABLE document_units ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'")

    event_columns = {row["name"] for row in connection.execute("PRAGMA table_info(memory_events)")}
    if "metadata_json" not in event_columns:
        connection.execute("ALTER TABLE memory_events ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'")
    if "created_at" not in event_columns:
        connection.execute(
            "ALTER TABLE memory_events ADD COLUMN created_at TEXT NOT NULL DEFAULT ''"
        )
        connection.execute(
            "UPDATE memory_events SET created_at = CURRENT_TIMESTAMP WHERE created_at = ''"
        )

    # ``skipped`` was used by the initial scanner implementation. It is now
    # represented as pending so the status vocabulary remains finite and the
    # source can be retried if the mounted file returns.
    connection.execute(
        "UPDATE sources SET indexing_status = 'pending', indexing_error = 'file no longer exists' "
        "WHERE indexing_status = 'skipped'"
    )

    fts_columns = {row["name"] for row in connection.execute("PRAGMA table_info(fts_units)")}
    if "short_comment" not in fts_columns or "tags" not in fts_columns:
        connection.execute("DROP TABLE IF EXISTS fts_units")
        connection.execute(
            "CREATE VIRTUAL TABLE fts_units USING fts5(" 
            "unit_id UNINDEXED, source_id UNINDEXED, extracted_text, short_comment, tags, "
            "tokenize='porter ascii')"
        )


def cache_key(provider: str, operation: str, content_hash: str) -> str:
    return f"{provider}:{operation}:{content_hash}"


def get_cached_response(connection: sqlite3.Connection, key: str) -> object | None:
    row = connection.execute(
        "SELECT response_json FROM api_cache WHERE cache_key = ?", (key,)
    ).fetchone()
    return None if row is None else json.loads(row["response_json"])


def put_cached_response(
    connection: sqlite3.Connection,
    key: str,
    content_hash: str,
    provider: str,
    operation: str,
    response: object,
) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO api_cache "
        "(cache_key, content_hash, provider, operation, response_json) VALUES (?, ?, ?, ?, ?)",
        (key, content_hash, provider, operation, json.dumps(response)),
    )


def record_memory_event(
    connection: sqlite3.Connection,
    event_type: str,
    source_id: int | None = None,
    timestamp: datetime | str | None = None,
    date_basis: str = "event_at",
    metadata: dict[str, object] | None = None,
    created_at: datetime | str | None = None,
) -> int:
    """Persist one genuine event using UTC timestamps and structured metadata."""
    if event_type not in MEMORY_EVENT_TYPES:
        raise ValueError(f"unsupported memory event type: {event_type}")
    event_timestamp = _utc_iso(timestamp)
    event_created_at = _utc_iso(created_at) if created_at is not None else None
    cursor = connection.execute(
        "INSERT INTO memory_events "
        "(event_type, source_id, timestamp, date_basis, metadata_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))",
        (event_type, source_id, event_timestamp, date_basis, json.dumps(metadata or {}), event_created_at),
    )
    return int(cursor.lastrowid)


def _utc_iso(value: datetime | str | None) -> str:
    if value is None:
        parsed = datetime.now(UTC)
    elif isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=UTC)
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


@contextmanager
def get_connection(database_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(database_path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

