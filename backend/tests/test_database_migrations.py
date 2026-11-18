from pathlib import Path
import sqlite3

import pytest

from app.db.database import SCHEMA, MigrationConflict, connect, initialize_database


def _legacy_database(path: Path, *, duplicate_objects: bool = False) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute(
            "INSERT INTO folder_mounts(path, label) VALUES (?, ?)",
            (str(path.parent), "Synthetic mount"),
        )
        mount_id = connection.execute("SELECT mount_id FROM folder_mounts").fetchone()[0]
        connection.execute(
            "INSERT INTO sources(mount_id, source_type, title, original_path) VALUES (?, ?, ?, ?)",
            (mount_id, "file", "Synthetic notes", str(path.parent / "notes.pdf")),
        )
        source_id = connection.execute("SELECT source_id FROM sources").fetchone()[0]
        connection.execute(
            "INSERT INTO document_units(source_id, unit_type, page_or_slide, extracted_text) "
            "VALUES (?, 'page', 3, 'Synthetic evidence')",
            (source_id,),
        )
        unit_id = connection.execute("SELECT unit_id FROM document_units").fetchone()[0]
        connection.execute(
            "INSERT INTO objects(object_id, object_type, title, description) "
            "VALUES (10, 'topic', 'Neural Networks', 'Legacy description')"
        )
        connection.execute(
            "INSERT INTO objects(object_id, object_type, title, description) "
            "VALUES (20, 'project', 'Aurora', 'Project description')"
        )
        if duplicate_objects:
            connection.execute(
                "INSERT INTO objects(object_id, object_type, title) VALUES (30, 'topic', ' neural   networks ')",
            )
        connection.execute(
            "INSERT INTO object_evidence(object_id, source_id, unit_id) VALUES (10, ?, ?)",
            (source_id, unit_id),
        )
        connection.execute(
            "INSERT INTO object_evidence(object_id, source_id, unit_id) VALUES (20, ?, NULL)",
            (source_id,),
        )
        connection.execute(
            "INSERT INTO relationships(relationship_id, from_object_id, to_object_id, relationship_type, confidence, created_by) "
            "VALUES (50, 10, 20, 'depends_on', 0.4, 'system')"
        )
        connection.execute(
            "INSERT INTO study_sessions(object_id, started_at) VALUES (10, '2026-11-18T00:00:00Z')"
        )
        connection.execute(
            "INSERT INTO memory_events(event_type, source_id, date_basis) VALUES ('file_imported', ?, 'imported_at')",
            (source_id,),
        )
        connection.commit()


def test_fresh_initialization_creates_versioned_hardened_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "fresh.sqlite3"
    initialize_database(database_path)

    with connect(database_path) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert [tuple(row) for row in connection.execute(
            "SELECT version FROM schema_versions ORDER BY version"
        ).fetchall()] == [(0,), (1,)]
        assert [tuple(row) for row in connection.execute(
            "SELECT name, is_symmetric FROM relationship_types ORDER BY name"
        ).fetchall()] == [
            ("created_by", 0),
            ("depends_on", 0),
            ("part_of", 0),
            ("related_to", 1),
            ("similar_to", 1),
        ]
        columns = {
            row[1]: row[3]
            for row in connection.execute("PRAGMA table_info(objects)")
        }
        assert columns["normalized_title"] == 1
        assert columns["updated_at"] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'relationship_evidence'"
        ).fetchone()[0] == 1


def test_legacy_migration_preserves_records_and_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    _legacy_database(database_path)

    initialize_database(database_path)
    with connect(database_path) as connection:
        assert [tuple(row) for row in connection.execute(
            "SELECT object_id, title, normalized_title, created_by, discovery_method FROM objects ORDER BY object_id"
        ).fetchall()] == [
            (10, "Neural Networks", "neural networks", "legacy", "legacy"),
            (20, "Aurora", "aurora", "legacy", "legacy"),
        ]
        relationship = connection.execute(
            "SELECT relationship_id, from_object_id, to_object_id, confidence, created_by FROM relationships"
        ).fetchone()
        assert tuple(relationship) == (50, 10, 20, 0.4, "system")
        assert connection.execute("SELECT COUNT(*) FROM object_evidence").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM study_sessions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM memory_events").fetchone()[0] == 1

    initialize_database(database_path)
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM schema_versions").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM objects").fetchone()[0] == 2


def test_migration_conflict_aborts_without_destructive_changes(tmp_path: Path) -> None:
    database_path = tmp_path / "conflict.sqlite3"
    _legacy_database(database_path, duplicate_objects=True)

    with pytest.raises(MigrationConflict, match="normalized entity identity collision"):
        initialize_database(database_path)

    with connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(objects)")}
        assert "normalized_title" not in columns
        assert connection.execute("SELECT COUNT(*) FROM objects").fetchone()[0] == 3
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_versions WHERE version = 1"
        ).fetchone()[0] == 0


def test_entity_and_relationship_identity_constraints(tmp_path: Path) -> None:
    database_path = tmp_path / "constraints.sqlite3"
    initialize_database(database_path)
    with connect(database_path) as connection:
        connection.execute("INSERT INTO objects(object_type, title, normalized_title, created_by, discovery_method) VALUES ('topic', 'A', 'a', 'user', 'manual')")
        connection.execute("INSERT INTO objects(object_type, title, normalized_title, created_by, discovery_method) VALUES ('topic', 'B', 'b', 'user', 'manual')")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO objects(object_type, title, normalized_title, created_by, discovery_method) VALUES ('topic', 'Other', 'a', 'user', 'manual')"
            )
        connection.execute(
            "INSERT INTO relationships(from_object_id, to_object_id, relationship_type, is_symmetric, identity_a, identity_b, created_by, discovery_method) "
            "VALUES (1, 2, 'related_to', 1, 1, 2, 'user', 'manual')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO relationships(from_object_id, to_object_id, relationship_type, is_symmetric, identity_a, identity_b, created_by, discovery_method) "
                "VALUES (2, 1, 'related_to', 1, 1, 2, 'user', 'manual')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO relationships(from_object_id, to_object_id, relationship_type, is_symmetric, identity_a, identity_b, created_by, discovery_method) "
                "VALUES (1, 2, 'related_to', 0, 1, 2, 'user', 'manual')"
            )
        connection.execute(
            "INSERT INTO relationships(from_object_id, to_object_id, relationship_type, is_symmetric, identity_a, identity_b, created_by, discovery_method) "
            "VALUES (2, 1, 'depends_on', 0, 2, 1, 'user', 'manual')"
        )
        connection.execute(
            "INSERT INTO relationships(from_object_id, to_object_id, relationship_type, is_symmetric, identity_a, identity_b, created_by, discovery_method, confidence) "
            "VALUES (1, 2, 'similar_to', 1, 1, 2, 'user', 'manual', NULL)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO relationships(from_object_id, to_object_id, relationship_type, is_symmetric, identity_a, identity_b, created_by, discovery_method) "
                "VALUES (1, 1, 'depends_on', 0, 1, 1, 'system', 'lexical')"
            )


def test_evidence_integrity_uniqueness_and_cascades(tmp_path: Path) -> None:
    database_path = tmp_path / "evidence.sqlite3"
    initialize_database(database_path)
    with connect(database_path) as connection:
        connection.execute("INSERT INTO sources(source_type, original_path) VALUES ('file', '/tmp/a.pdf')")
        connection.execute("INSERT INTO sources(source_type, original_path) VALUES ('file', '/tmp/b.pdf')")
        connection.execute("INSERT INTO document_units(source_id, unit_type) VALUES (1, 'page')")
        connection.execute("INSERT INTO document_units(source_id, unit_type) VALUES (2, 'page')")
        connection.execute("INSERT INTO objects(object_type, title, normalized_title, created_by, discovery_method) VALUES ('topic', 'A', 'a', 'user', 'manual')")
        connection.execute(
            "INSERT INTO object_evidence(object_id, source_id, unit_id, discovery_method) VALUES (1, 1, NULL, 'manual')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO object_evidence(object_id, source_id, unit_id, discovery_method) VALUES (1, 1, NULL, 'manual')"
            )
        connection.execute(
            "INSERT INTO object_evidence(object_id, source_id, unit_id, discovery_method) VALUES (1, 1, 1, 'lexical')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO object_evidence(object_id, source_id, unit_id, discovery_method) VALUES (1, 2, 1, 'lexical')"
            )
        connection.execute("INSERT INTO objects(object_type, title, normalized_title, created_by, discovery_method) VALUES ('topic', 'B', 'b', 'user', 'manual')")
        connection.execute(
            "INSERT INTO relationships(from_object_id, to_object_id, relationship_type, is_symmetric, identity_a, identity_b, created_by, discovery_method) "
            "VALUES (1, 2, 'related_to', 1, 1, 2, 'user', 'manual')"
        )
        connection.execute(
            "INSERT INTO relationship_evidence(relationship_id, source_id, unit_id, discovery_method) VALUES (1, 1, 1, 'manual')"
        )
        connection.execute("DELETE FROM sources WHERE source_id = 1")
        assert connection.execute("SELECT COUNT(*) FROM object_evidence").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM relationship_evidence").fetchone()[0] == 0


def test_user_relationship_survives_without_confidence_and_entity_delete_cascades(tmp_path: Path) -> None:
    database_path = tmp_path / "lifecycle.sqlite3"
    initialize_database(database_path)
    with connect(database_path) as connection:
        connection.execute("INSERT INTO objects(object_type, title, normalized_title, created_by, discovery_method) VALUES ('topic', 'A', 'a', 'user', 'manual')")
        connection.execute("INSERT INTO objects(object_type, title, normalized_title, created_by, discovery_method) VALUES ('topic', 'B', 'b', 'user', 'manual')")
        connection.execute(
            "INSERT INTO relationships(from_object_id, to_object_id, relationship_type, is_symmetric, identity_a, identity_b, created_by, discovery_method, confidence) "
            "VALUES (1, 2, 'part_of', 0, 1, 2, 'user', 'manual', NULL)"
        )
        assert tuple(connection.execute("SELECT confidence, created_by FROM relationships").fetchone()) == (None, "user")
        connection.execute("DELETE FROM objects WHERE object_id = 1")
        assert connection.execute("SELECT COUNT(*) FROM relationships").fetchone()[0] == 0
