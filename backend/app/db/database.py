from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
import sqlite3
import json
from pathlib import Path
import unicodedata


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


SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_versions (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

INITIAL_RELATIONSHIP_TYPES = {
    "related_to": (1, "A general symmetric association between two entities."),
    "similar_to": (1, "Two entities are similar in meaning or role."),
    "part_of": (0, "The source entity is a component or member of the target entity."),
    "depends_on": (0, "The source entity requires or relies on the target entity."),
    "created_by": (0, "The source entity was created by the target entity."),
}

MIGRATIONS = (
    (1, "knowledge_connections_schema_hardening", "_migration_knowledge_connections_v1"),
    (2, "relationship_unsupported_review_status", "_migration_relationship_unsupported_v2"),
)


def connect(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(database_path: Path) -> None:
    with connect(database_path) as connection:
        connection.executescript(SCHEMA)
        connection.execute(SCHEMA_VERSION_TABLE)
        _migrate_legacy_schema(connection)
        _bootstrap_schema_version(connection)
        # The bootstrap marker is committed before a numbered migration
        # starts. SQLite implicitly opens a transaction for the INSERT above;
        # leaving it open would make the migration's explicit transaction
        # fail. Numbered migrations themselves remain atomic.
        connection.commit()
        _apply_pending_migrations(connection)


def _bootstrap_schema_version(connection: sqlite3.Connection) -> None:
    """Record the pre-versioned schema only after legacy normalization succeeds."""
    if connection.execute("SELECT 1 FROM schema_versions LIMIT 1").fetchone() is not None:
        return
    connection.execute(
        "INSERT INTO schema_versions(version, name) VALUES (0, 'legacy-foundation')"
    )


def _apply_pending_migrations(connection: sqlite3.Connection) -> None:
    current = connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_versions").fetchone()[0]
    for version, name, function_name in MIGRATIONS:
        if int(version) <= int(current):
            continue
        migration = globals()[function_name]
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        try:
            migration(connection)
            connection.execute(
                "INSERT INTO schema_versions(version, name) VALUES (?, ?)", (version, name)
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        current = version


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


class MigrationConflict(RuntimeError):
    """Raised when existing data cannot be migrated without guessing or loss."""


def _migration_knowledge_connections_v1(connection: sqlite3.Connection) -> None:
    """Harden the pre-versioned connection tables in one explicit transaction."""
    _assert_no_unexpected_dependencies(connection)
    object_rows = connection.execute(
        "SELECT object_id, object_type, title, description, created_at "
        "FROM objects ORDER BY object_id"
    ).fetchall()
    relationship_rows = connection.execute(
        "SELECT relationship_id, from_object_id, to_object_id, relationship_type, confidence, "
        "created_by, reviewed_status FROM relationships ORDER BY relationship_id"
    ).fetchall()
    evidence_rows = connection.execute(
        "SELECT object_id, source_id, unit_id FROM object_evidence "
        "ORDER BY object_id, source_id, unit_id"
    ).fetchall()
    study_rows = connection.execute(
        "SELECT session_id, object_id, started_at, completed_at, questions_attempted, answers_correct "
        "FROM study_sessions ORDER BY session_id"
    ).fetchall()

    object_ids = {int(row["object_id"]) for row in object_rows}
    source_ids = {
        int(row["source_id"])
        for row in connection.execute("SELECT source_id FROM sources")
    }
    unit_sources = {
        int(row["unit_id"]): int(row["source_id"])
        for row in connection.execute("SELECT unit_id, source_id FROM document_units")
    }

    object_records: list[dict[str, object]] = []
    identity_ids: dict[tuple[str, str], int] = {}
    for row in object_rows:
        object_type = str(row["object_type"])
        title = str(row["title"])
        normalized_title = _normalize_entity_title(title)
        key = (object_type, normalized_title)
        if key in identity_ids:
            raise MigrationConflict(
                "normalized entity identity collision for object IDs "
                f"{identity_ids[key]} and {int(row['object_id'])}"
            )
        identity_ids[key] = int(row["object_id"])
        object_records.append(
            {
                "object_id": int(row["object_id"]),
                "object_type": object_type,
                "title": title,
                "normalized_title": normalized_title,
                "description": row["description"],
                "created_at": row["created_at"],
            }
        )

    evidence_keys: set[tuple[int, int, int | None]] = set()
    source_evidence_keys: set[tuple[int, int]] = set()
    for row in evidence_rows:
        object_id = int(row["object_id"])
        source_id = int(row["source_id"])
        unit_id = None if row["unit_id"] is None else int(row["unit_id"])
        if object_id not in object_ids:
            raise MigrationConflict(f"object evidence references missing object ID {object_id}")
        if source_id not in source_ids:
            raise MigrationConflict(f"object evidence references missing source ID {source_id}")
        if unit_id is not None and unit_sources.get(unit_id) != source_id:
            raise MigrationConflict(
                f"object evidence unit/source mismatch for unit ID {unit_id} and source ID {source_id}"
            )
        key = (object_id, source_id, unit_id)
        if key in evidence_keys or (unit_id is None and (object_id, source_id) in source_evidence_keys):
            raise MigrationConflict(
                f"duplicate object evidence for object ID {object_id} and source ID {source_id}"
            )
        evidence_keys.add(key)
        if unit_id is None:
            source_evidence_keys.add((object_id, source_id))

    relationship_keys: set[tuple[int, int, str]] = set()
    relationship_records: list[dict[str, object]] = []
    known_relationship_types = set(INITIAL_RELATIONSHIP_TYPES)
    relationship_columns = {row[1] for row in connection.execute("PRAGMA table_info(relationships)")}
    for row in relationship_rows:
        from_object_id = int(row["from_object_id"])
        to_object_id = int(row["to_object_id"])
        relationship_type = str(row["relationship_type"])
        if from_object_id not in object_ids or to_object_id not in object_ids:
            raise MigrationConflict(
                f"relationship {int(row['relationship_id'])} references a missing object"
            )
        if from_object_id == to_object_id:
            raise MigrationConflict(
                f"relationship {int(row['relationship_id'])} is a self-relationship"
            )
        is_symmetric = (
            int(row["is_symmetric"])
            if "is_symmetric" in relationship_columns
            else 0
        )
        identity_a = min(from_object_id, to_object_id) if is_symmetric else from_object_id
        identity_b = max(from_object_id, to_object_id) if is_symmetric else to_object_id
        key = (identity_a, identity_b, relationship_type)
        if key in relationship_keys:
            raise MigrationConflict(
                f"duplicate relationship identity for object IDs {identity_a} and {identity_b} "
                f"with type '{relationship_type}'"
            )
        relationship_keys.add(key)
        if relationship_type not in known_relationship_types:
            known_relationship_types.add(relationship_type)
        relationship_records.append(
            {
                "relationship_id": int(row["relationship_id"]),
                "from_object_id": from_object_id,
                "to_object_id": to_object_id,
                "relationship_type": relationship_type,
                "is_symmetric": is_symmetric,
                "identity_a": identity_a,
                "identity_b": identity_b,
                "confidence": row["confidence"],
                "created_by": row["created_by"],
                "reviewed_status": row["reviewed_status"],
            }
        )

    connection.execute(
        "CREATE TABLE relationship_types ("
        "name TEXT NOT NULL, "
        "is_symmetric INTEGER NOT NULL CHECK (is_symmetric IN (0, 1)), "
        "description TEXT NOT NULL, "
        "is_legacy INTEGER NOT NULL DEFAULT 0 CHECK (is_legacy IN (0, 1)), "
        "PRIMARY KEY (name, is_symmetric)"
        ")"
    )
    for name, (is_symmetric, description) in INITIAL_RELATIONSHIP_TYPES.items():
        connection.execute(
            "INSERT INTO relationship_types(name, is_symmetric, description) VALUES (?, ?, ?)",
            (name, is_symmetric, description),
        )
    for record in relationship_records:
        relationship_type = str(record["relationship_type"])
        if connection.execute(
            "SELECT 1 FROM relationship_types WHERE name = ? AND is_symmetric = ?",
            (relationship_type, record["is_symmetric"]),
        ).fetchone() is None:
            connection.execute(
                "INSERT INTO relationship_types(name, is_symmetric, description, is_legacy) "
                "VALUES (?, 0, ?, 1)",
                (relationship_type, f"Legacy directed relationship type: {relationship_type}"),
            )

    connection.execute(
        "CREATE UNIQUE INDEX ux_document_units_unit_source "
        "ON document_units(unit_id, source_id)"
    )
    _create_migration_tables(connection)
    for record in object_records:
        connection.execute(
            "INSERT INTO objects__new(object_id, object_type, title, normalized_title, aliases_json, "
            "description, created_by, discovery_method, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, '[]', ?, 'legacy', 'legacy', 'active', ?, ?)",
            (
                record["object_id"], record["object_type"], record["title"],
                record["normalized_title"], record["description"], record["created_at"],
                record["created_at"],
            ),
        )
    for row in evidence_rows:
        connection.execute(
            "INSERT INTO object_evidence__new(object_id, source_id, unit_id, discovery_method) "
            "VALUES (?, ?, ?, 'legacy')",
            (row["object_id"], row["source_id"], row["unit_id"]),
        )
    for record in relationship_records:
        connection.execute(
            "INSERT INTO relationships__new(relationship_id, from_object_id, to_object_id, "
            "relationship_type, is_symmetric, identity_a, identity_b, confidence, created_by, "
            "discovery_method, reviewed_status, explanation, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'legacy', ?, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (
                record["relationship_id"], record["from_object_id"], record["to_object_id"],
                record["relationship_type"], record["is_symmetric"], record["identity_a"],
                record["identity_b"], record["confidence"], record["created_by"],
                record["reviewed_status"],
            ),
        )
    for row in study_rows:
        connection.execute(
            "INSERT INTO study_sessions__new(session_id, object_id, started_at, completed_at, "
            "questions_attempted, answers_correct) VALUES (?, ?, ?, ?, ?, ?)",
            tuple(row),
        )

    connection.execute("DROP TABLE object_evidence")
    connection.execute("DROP TABLE relationships")
    connection.execute("DROP TABLE study_sessions")
    connection.execute("DROP TABLE objects")
    connection.execute("ALTER TABLE objects__new RENAME TO objects")
    connection.execute("ALTER TABLE object_evidence__new RENAME TO object_evidence")
    connection.execute("ALTER TABLE relationships__new RENAME TO relationships")
    connection.execute("ALTER TABLE relationship_evidence__new RENAME TO relationship_evidence")
    connection.execute("ALTER TABLE study_sessions__new RENAME TO study_sessions")
    _create_connection_indexes(connection)
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise MigrationConflict(f"foreign-key check failed with {len(violations)} violation(s)")


def _migration_relationship_unsupported_v2(connection: sqlite3.Connection) -> None:
    """Allow automatic relationships to retain an explicit unsupported state."""
    relationship_rows = connection.execute(
        "SELECT relationship_id, from_object_id, to_object_id, relationship_type, is_symmetric, "
        "identity_a, identity_b, confidence, created_by, discovery_method, reviewed_status, "
        "explanation, created_at, updated_at FROM relationships ORDER BY relationship_id"
    ).fetchall()
    evidence_rows = connection.execute(
        "SELECT evidence_id, relationship_id, source_id, unit_id, discovery_method, confidence, created_at "
        "FROM relationship_evidence ORDER BY evidence_id"
    ).fetchall()
    connection.execute(
        "CREATE TABLE relationships__v2 ("
        "relationship_id INTEGER PRIMARY KEY AUTOINCREMENT, from_object_id INTEGER NOT NULL, "
        "to_object_id INTEGER NOT NULL, relationship_type TEXT NOT NULL, "
        "is_symmetric INTEGER NOT NULL CHECK (is_symmetric IN (0, 1)), "
        "identity_a INTEGER NOT NULL, identity_b INTEGER NOT NULL, "
        "confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)), "
        "created_by TEXT NOT NULL CHECK (created_by IN ('user', 'system', 'llm', 'legacy')), "
        "discovery_method TEXT NOT NULL CHECK (discovery_method IN "
        "('manual', 'cooccurrence', 'metadata', 'lexical', 'semantic', 'visual', 'llm', 'legacy')), "
        "reviewed_status TEXT NOT NULL DEFAULT 'unreviewed' CHECK "
        "(reviewed_status IN ('unreviewed', 'confirmed', 'rejected', 'unsupported')), "
        "explanation TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "FOREIGN KEY(from_object_id) REFERENCES objects(object_id) ON DELETE CASCADE, "
        "FOREIGN KEY(to_object_id) REFERENCES objects(object_id) ON DELETE CASCADE, "
        "FOREIGN KEY(identity_a) REFERENCES objects(object_id) ON DELETE CASCADE, "
        "FOREIGN KEY(identity_b) REFERENCES objects(object_id) ON DELETE CASCADE, "
        "FOREIGN KEY(relationship_type, is_symmetric) REFERENCES relationship_types(name, is_symmetric), "
        "CHECK(from_object_id <> to_object_id), "
        "CHECK((is_symmetric = 0 AND identity_a = from_object_id AND identity_b = to_object_id) OR "
        "(is_symmetric = 1 AND ((from_object_id < to_object_id AND identity_a = from_object_id AND identity_b = to_object_id) OR "
        "(to_object_id < from_object_id AND identity_a = to_object_id AND identity_b = from_object_id)))), "
        "UNIQUE(identity_a, identity_b, relationship_type)"
        ")"
    )
    connection.execute(
        "CREATE TABLE relationship_evidence__v2 ("
        "evidence_id INTEGER PRIMARY KEY AUTOINCREMENT, relationship_id INTEGER NOT NULL, "
        "source_id INTEGER NOT NULL, unit_id INTEGER, "
        "discovery_method TEXT NOT NULL CHECK (discovery_method IN "
        "('manual', 'cooccurrence', 'metadata', 'lexical', 'semantic', 'visual', 'llm', 'legacy')), "
        "confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)), "
        "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "FOREIGN KEY(relationship_id) REFERENCES relationships__v2(relationship_id) ON DELETE CASCADE, "
        "FOREIGN KEY(source_id) REFERENCES sources(source_id) ON DELETE CASCADE, "
        "FOREIGN KEY(unit_id) REFERENCES document_units(unit_id) ON DELETE CASCADE, "
        "FOREIGN KEY(unit_id, source_id) REFERENCES document_units(unit_id, source_id) ON DELETE CASCADE"
        ")"
    )
    for row in relationship_rows:
        connection.execute(
            "INSERT INTO relationships__v2(relationship_id, from_object_id, to_object_id, relationship_type, "
            "is_symmetric, identity_a, identity_b, confidence, created_by, discovery_method, reviewed_status, "
            "explanation, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            tuple(row),
        )
    for row in evidence_rows:
        connection.execute(
            "INSERT INTO relationship_evidence__v2(evidence_id, relationship_id, source_id, unit_id, "
            "discovery_method, confidence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            tuple(row),
        )
    connection.execute("DROP TABLE relationship_evidence")
    connection.execute("DROP TABLE relationships")
    connection.execute("ALTER TABLE relationships__v2 RENAME TO relationships")
    connection.execute("ALTER TABLE relationship_evidence__v2 RENAME TO relationship_evidence")
    _create_connection_indexes(connection)
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise MigrationConflict(f"foreign-key check failed with {len(violations)} violation(s)")


def _create_migration_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE objects__new ("
        "object_id INTEGER PRIMARY KEY AUTOINCREMENT, object_type TEXT NOT NULL, "
        "title TEXT NOT NULL CHECK (length(trim(title)) > 0), "
        "normalized_title TEXT NOT NULL CHECK (length(trim(normalized_title)) > 0), "
        "aliases_json TEXT NOT NULL DEFAULT '[]', description TEXT, "
        "created_by TEXT NOT NULL CHECK (created_by IN ('user', 'system', 'llm', 'legacy')), "
        "discovery_method TEXT NOT NULL CHECK (discovery_method IN "
        "('manual', 'title', 'tag', 'lexical', 'semantic', 'visual', 'llm', 'legacy')), "
        "status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'needs-review')), "
        "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "UNIQUE(object_type, normalized_title)"
        ")"
    )
    connection.execute(
        "CREATE TABLE object_evidence__new ("
        "evidence_id INTEGER PRIMARY KEY AUTOINCREMENT, object_id INTEGER NOT NULL, "
        "source_id INTEGER NOT NULL, unit_id INTEGER, "
        "discovery_method TEXT NOT NULL CHECK (discovery_method IN "
        "('manual', 'title', 'tag', 'lexical', 'semantic', 'visual', 'llm', 'legacy')), "
        "confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)), "
        "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "FOREIGN KEY(object_id) REFERENCES objects__new(object_id) ON DELETE CASCADE, "
        "FOREIGN KEY(source_id) REFERENCES sources(source_id) ON DELETE CASCADE, "
        "FOREIGN KEY(unit_id) REFERENCES document_units(unit_id) ON DELETE CASCADE, "
        "FOREIGN KEY(unit_id, source_id) REFERENCES document_units(unit_id, source_id) ON DELETE CASCADE"
        ")"
    )
    connection.execute(
        "CREATE TABLE relationships__new ("
        "relationship_id INTEGER PRIMARY KEY AUTOINCREMENT, from_object_id INTEGER NOT NULL, "
        "to_object_id INTEGER NOT NULL, relationship_type TEXT NOT NULL, "
        "is_symmetric INTEGER NOT NULL CHECK (is_symmetric IN (0, 1)), "
        "identity_a INTEGER NOT NULL, identity_b INTEGER NOT NULL, "
        "confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)), "
        "created_by TEXT NOT NULL CHECK (created_by IN ('user', 'system', 'llm', 'legacy')), "
        "discovery_method TEXT NOT NULL CHECK (discovery_method IN "
        "('manual', 'cooccurrence', 'metadata', 'lexical', 'semantic', 'visual', 'llm', 'legacy')), "
        "reviewed_status TEXT NOT NULL DEFAULT 'unreviewed' CHECK "
        "(reviewed_status IN ('unreviewed', 'confirmed', 'rejected')), "
        "explanation TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "FOREIGN KEY(from_object_id) REFERENCES objects__new(object_id) ON DELETE CASCADE, "
        "FOREIGN KEY(to_object_id) REFERENCES objects__new(object_id) ON DELETE CASCADE, "
        "FOREIGN KEY(identity_a) REFERENCES objects__new(object_id) ON DELETE CASCADE, "
        "FOREIGN KEY(identity_b) REFERENCES objects__new(object_id) ON DELETE CASCADE, "
        "FOREIGN KEY(relationship_type, is_symmetric) REFERENCES relationship_types(name, is_symmetric), "
        "CHECK(from_object_id <> to_object_id), "
        "CHECK((is_symmetric = 0 AND identity_a = from_object_id AND identity_b = to_object_id) OR "
        "(is_symmetric = 1 AND ((from_object_id < to_object_id AND identity_a = from_object_id AND identity_b = to_object_id) OR "
        "(to_object_id < from_object_id AND identity_a = to_object_id AND identity_b = from_object_id)))), "
        "UNIQUE(identity_a, identity_b, relationship_type)"
        ")"
    )
    connection.execute(
        "CREATE TABLE relationship_evidence__new ("
        "evidence_id INTEGER PRIMARY KEY AUTOINCREMENT, relationship_id INTEGER NOT NULL, "
        "source_id INTEGER NOT NULL, unit_id INTEGER, "
        "discovery_method TEXT NOT NULL CHECK (discovery_method IN "
        "('manual', 'cooccurrence', 'metadata', 'lexical', 'semantic', 'visual', 'llm', 'legacy')), "
        "confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)), "
        "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "FOREIGN KEY(relationship_id) REFERENCES relationships__new(relationship_id) ON DELETE CASCADE, "
        "FOREIGN KEY(source_id) REFERENCES sources(source_id) ON DELETE CASCADE, "
        "FOREIGN KEY(unit_id) REFERENCES document_units(unit_id) ON DELETE CASCADE, "
        "FOREIGN KEY(unit_id, source_id) REFERENCES document_units(unit_id, source_id) ON DELETE CASCADE"
        ")"
    )
    connection.execute(
        "CREATE TABLE study_sessions__new ("
        "session_id INTEGER PRIMARY KEY AUTOINCREMENT, object_id INTEGER REFERENCES objects__new(object_id) ON DELETE SET NULL, "
        "started_at TEXT NOT NULL, completed_at TEXT, questions_attempted INTEGER NOT NULL DEFAULT 0, "
        "answers_correct INTEGER NOT NULL DEFAULT 0"
        ")"
    )


def _create_connection_indexes(connection: sqlite3.Connection) -> None:
    statements = (
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_object_evidence_unit ON object_evidence(object_id, source_id, unit_id) WHERE unit_id IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_object_evidence_source ON object_evidence(object_id, source_id) WHERE unit_id IS NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_relationship_evidence_unit ON relationship_evidence(relationship_id, source_id, unit_id) WHERE unit_id IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_relationship_evidence_source ON relationship_evidence(relationship_id, source_id) WHERE unit_id IS NULL",
        "CREATE INDEX IF NOT EXISTS idx_objects_status_type ON objects(status, object_type)",
        "CREATE INDEX IF NOT EXISTS idx_object_evidence_object_source ON object_evidence(object_id, source_id)",
        "CREATE INDEX IF NOT EXISTS idx_object_evidence_source ON object_evidence(source_id)",
        "CREATE INDEX IF NOT EXISTS idx_object_evidence_unit ON object_evidence(unit_id)",
        "CREATE INDEX IF NOT EXISTS idx_relationships_from ON relationships(from_object_id)",
        "CREATE INDEX IF NOT EXISTS idx_relationships_to ON relationships(to_object_id)",
        "CREATE INDEX IF NOT EXISTS idx_relationships_type_review ON relationships(relationship_type, reviewed_status)",
        "CREATE INDEX IF NOT EXISTS idx_relationship_evidence_relationship ON relationship_evidence(relationship_id)",
        "CREATE INDEX IF NOT EXISTS idx_relationship_evidence_source ON relationship_evidence(source_id)",
        "CREATE INDEX IF NOT EXISTS idx_relationship_evidence_unit ON relationship_evidence(unit_id)",
    )
    for statement in statements:
        connection.execute(statement)


def _normalize_entity_title(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title).casefold()
    return " ".join(normalized.strip().split())


def _assert_no_unexpected_dependencies(connection: sqlite3.Connection) -> None:
    expected = {
        "object_evidence": {"objects"},
        "relationships": {"objects"},
        "study_sessions": {"objects"},
    }
    unexpected: list[str] = []
    tables = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )]
    for table in tables:
        for row in connection.execute(f"PRAGMA foreign_key_list({table})"):
            parent = row[2]
            if parent in {"objects", "relationships"} and table not in expected:
                unexpected.append(f"{table}->{parent}")
    for row in connection.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE type IN ('trigger', 'view')"
    ):
        sql = (row[2] or "").lower()
        if "objects" in sql or "relationships" in sql:
            unexpected.append(f"{row[0]}:{row[1]}")
    if unexpected:
        raise MigrationConflict("unexpected dependencies: " + ", ".join(sorted(unexpected)))


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

