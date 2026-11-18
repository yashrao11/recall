from collections.abc import Iterator
from contextlib import contextmanager
import sqlite3
from pathlib import Path


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
        CHECK (indexing_status IN ('pending', 'processing', 'done', 'error', 'skipped')),
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
    thumbnail_path TEXT,
    text_embedding_ref TEXT,
    visual_embedding_ref TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS fts_units USING fts5(
    unit_id UNINDEXED,
    source_id UNINDEXED,
    extracted_text,
    tokenize='porter ascii'
);

CREATE TABLE IF NOT EXISTS memory_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    source_id INTEGER REFERENCES sources(source_id) ON DELETE CASCADE,
    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    date_basis TEXT NOT NULL
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

