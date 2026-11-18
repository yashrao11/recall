from pathlib import Path

import pytest

from app.db.database import get_connection, initialize_database
from app.ingestion import indexer
from app.ingestion.indexer import index_source
from app.ai.providers import LocalMetadataProvider
from app.config import Settings
from app.knowledge.discovery import discover_source_by_database, extract_candidates


class EmptyMetadataProvider:
    name = "empty-metadata"

    def describe(self, text, image_bytes, mime_type, content_hash, connection):
        return "", []


def _make_source(tmp_path: Path, content: str, title: str = "notes.md"):
    data_dir = tmp_path / "data"
    source_path = tmp_path / title
    source_path.write_text(content, encoding="utf-8")
    database_path = data_dir / "recall.sqlite3"
    initialize_database(database_path)
    with get_connection(database_path) as connection:
        mount_id = connection.execute(
            "INSERT INTO folder_mounts(path, label) VALUES (?, 'test')", (str(tmp_path),)
        ).lastrowid
        source_id = connection.execute(
            "INSERT INTO sources(mount_id, source_type, title, original_path, content_hash) "
            "VALUES (?, 'markdown', ?, ?, 'hash-1')",
            (mount_id, title, str(source_path)),
        ).lastrowid
    return Settings(data_dir=data_dir, allow_cloud_ai=False), source_path, int(source_id), int(mount_id)


def test_local_discovery_creates_entities_evidence_and_relationships(tmp_path: Path) -> None:
    settings, _, source_id, _ = _make_source(
        tmp_path,
        "# Neural Networks\nProject Aurora uses deep learning. Written by Alice Smith. "
        "Location: Paris. Conference Aurora 2026.",
        "Project Aurora Notes.md",
    )
    result = index_source(source_id, settings, None, EmptyMetadataProvider())

    assert result["status"] == "partial"
    assert result["entities_created"] >= 3
    assert result["entity_evidence_created"] >= 3
    assert result["relationships_created"] >= 1
    with get_connection(settings.database_path) as connection:
        objects = {
            (row["object_type"], row["title"])
            for row in connection.execute(
                "SELECT object_type, title FROM objects ORDER BY object_id"
            )
        }
        assert ("project", "Project Aurora") in objects
        assert ("concept", "Deep Learning") in objects
        assert ("person", "Alice Smith") in objects
        assert ("place", "Paris") in objects
        assert ("event", "Conference Aurora 2026") in objects
        assert connection.execute(
            "SELECT COUNT(*) FROM object_evidence WHERE unit_id IS NOT NULL"
        ).fetchone()[0] >= 3
        relationship = connection.execute(
            "SELECT relationship_type, is_symmetric, identity_a, identity_b, confidence, discovery_method "
            "FROM relationships"
        ).fetchone()
        assert tuple(relationship)[0:2] == ("related_to", 1)
        assert relationship["identity_a"] < relationship["identity_b"]
        assert relationship["confidence"] is None
        assert relationship["discovery_method"] == "cooccurrence"
        assert connection.execute("SELECT COUNT(*) FROM relationship_evidence").fetchone()[0] >= 1


def test_repeated_discovery_reuses_entities_and_evidence(tmp_path: Path) -> None:
    settings, _, source_id, _ = _make_source(
        tmp_path,
        "Deep learning uses neural network methods.",
    )
    first = index_source(source_id, settings, None, EmptyMetadataProvider())
    with get_connection(settings.database_path) as connection:
        first_counts = (
            connection.execute("SELECT COUNT(*) FROM objects").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM relationships").fetchone()[0],
        )
    second = index_source(source_id, settings, None, EmptyMetadataProvider())
    with get_connection(settings.database_path) as connection:
        second_counts = (
            connection.execute("SELECT COUNT(*) FROM objects").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM relationships").fetchone()[0],
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM object_evidence WHERE discovery_method <> 'manual'"
        ).fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM relationship_evidence").fetchone()[0] == 1
    assert first["entities_created"] == 2
    assert second["entities_created"] == 0
    assert second["entities_reused"] >= 2
    assert first_counts == second_counts


def test_alias_resolution_and_ambiguous_aliases_are_conservative(tmp_path: Path) -> None:
    settings, _, source_id, _ = _make_source(tmp_path, "Machine learning is discussed.")
    with get_connection(settings.database_path) as connection:
        connection.execute(
            "INSERT INTO objects(object_type, title, normalized_title, aliases_json, created_by, discovery_method) "
            "VALUES ('concept', 'ML', 'ml', '[\"machine learning\"]', 'user', 'manual')"
        )
    discover_source_by_database(settings.database_path, source_id)
    with get_connection(settings.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM objects WHERE object_type = 'concept'"
        ).fetchone()[0] == 1
    with get_connection(settings.database_path) as connection:
        connection.execute(
            "INSERT INTO objects(object_type, title, normalized_title, aliases_json, created_by, discovery_method) "
            "VALUES ('topic', 'First Topic', 'first topic', '[\"ambiguous\"]', 'user', 'manual')"
        )
        connection.execute(
            "INSERT INTO objects(object_type, title, normalized_title, aliases_json, created_by, discovery_method) "
            "VALUES ('topic', 'Second Topic', 'second topic', '[\"ambiguous\"]', 'user', 'manual')"
        )
        connection.execute(
            "INSERT INTO document_units(source_id, unit_type, extracted_text, short_comment, tags_json) "
            "VALUES (?, 'text', 'ambiguous', '', '[\"ambiguous\"]')",
            (source_id,),
        )
        new_unit_id = connection.execute("SELECT MAX(unit_id) FROM document_units").fetchone()[0]
    discover_source_by_database(settings.database_path, source_id)
    with get_connection(settings.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM objects WHERE normalized_title = 'ambiguous'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT object_id FROM object_evidence WHERE unit_id = ?", (new_unit_id,)
        ).fetchone() is not None


def test_candidate_extraction_rejects_generic_boilerplate(tmp_path: Path) -> None:
    settings, _, source_id, _ = _make_source(tmp_path, "Contents and Introduction")
    with get_connection(settings.database_path) as connection:
        units = connection.execute(
            "SELECT unit_id, extracted_text, short_comment, tags_json FROM document_units WHERE source_id = ?",
            (source_id,),
        ).fetchall()
    candidates = extract_candidates(source_id, "Document Notes", "notes.md", units)
    assert all(candidate.title.casefold() not in {"contents", "introduction", "notes"} for candidate in candidates)
    assert settings.allow_cloud_ai is False


def test_reindex_removes_old_automatic_support_but_preserves_manual_knowledge(tmp_path: Path) -> None:
    settings, source_path, source_id, mount_id = _make_source(
        tmp_path, "Deep learning and neural network methods."
    )
    index_source(source_id, settings, None, EmptyMetadataProvider())
    with get_connection(settings.database_path) as connection:
        connection.execute(
            "INSERT INTO objects(object_type, title, normalized_title, created_by, discovery_method) "
            "VALUES ('topic', 'Manual Topic', 'manual topic', 'user', 'manual')"
        )
        manual_object_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        other_object_id = connection.execute(
            "SELECT object_id FROM objects WHERE object_id <> ? ORDER BY object_id LIMIT 1",
            (manual_object_id,),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO relationships(from_object_id, to_object_id, relationship_type, is_symmetric, "
            "identity_a, identity_b, created_by, discovery_method, confidence) "
            "VALUES (?, ?, 'related_to', 1, ?, ?, 'user', 'manual', NULL)",
            (manual_object_id, other_object_id, min(manual_object_id, other_object_id), max(manual_object_id, other_object_id)),
        )
        old_auto_ids = [
            int(row["object_id"])
            for row in connection.execute(
                "SELECT object_id FROM objects WHERE created_by = 'system'"
            )
        ]

    source_path.write_text("ordinary prose without indexed concepts", encoding="utf-8")
    with get_connection(settings.database_path) as connection:
        connection.execute(
            "UPDATE sources SET content_hash = 'hash-2', indexing_status = 'pending' WHERE source_id = ?",
            (source_id,),
        )
    index_source(source_id, settings, None, EmptyMetadataProvider())
    with get_connection(settings.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM relationships WHERE created_by = 'system' "
            "AND reviewed_status <> 'unsupported'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM relationships WHERE created_by = 'system' "
            "AND reviewed_status = 'unsupported'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM relationships WHERE created_by = 'user'"
        ).fetchone()[0] == 1
        for object_id in old_auto_ids:
            assert connection.execute(
                "SELECT status FROM objects WHERE object_id = ?", (object_id,)
            ).fetchone()[0] == "inactive"
        assert connection.execute(
            "SELECT status FROM objects WHERE normalized_title = 'manual topic'"
        ).fetchone()[0] == "active"


def test_discovery_failure_rolls_back_units_and_persists_error(tmp_path: Path, monkeypatch) -> None:
    settings, _, source_id, _ = _make_source(tmp_path, "Deep learning.")
    monkeypatch.setattr(indexer, "discover_source", lambda connection, source_id: (_ for _ in ()).throw(RuntimeError("discovery failed")))

    with pytest.raises(RuntimeError, match="discovery failed"):
        index_source(source_id, settings, None, LocalMetadataProvider())
    with get_connection(settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM document_units").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM objects").fetchone()[0] == 0
        row = connection.execute(
            "SELECT indexing_status, indexing_error FROM sources WHERE source_id = ?", (source_id,)
        ).fetchone()
        assert row["indexing_status"] == "error"
        assert "discovery failed" in row["indexing_error"]


def test_source_deletion_removes_support_and_deactivates_only_automatic_entities(tmp_path: Path) -> None:
    settings, _, source_id, _ = _make_source(tmp_path, "Deep learning and neural network methods.")
    index_source(source_id, settings, None, EmptyMetadataProvider())
    with get_connection(settings.database_path) as connection:
        connection.execute(
            "INSERT INTO objects(object_type, title, normalized_title, created_by, discovery_method) "
            "VALUES ('topic', 'Manual Topic', 'manual topic', 'user', 'manual')"
        )
        manual_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        auto_id = connection.execute(
            "SELECT object_id FROM objects WHERE created_by = 'system' LIMIT 1"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO object_evidence(object_id, source_id, discovery_method) "
            "VALUES (?, ?, 'manual')",
            (manual_id, source_id),
        )
        connection.execute("DELETE FROM sources WHERE source_id = ?", (source_id,))
        from app.knowledge.discovery import reconcile_automatic_knowledge
        reconcile_automatic_knowledge(connection)
        assert connection.execute("SELECT COUNT(*) FROM relationship_evidence").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM relationships WHERE reviewed_status <> 'unsupported'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM relationships WHERE reviewed_status = 'unsupported'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT status FROM objects WHERE object_id = ?", (auto_id,)
        ).fetchone()[0] == "inactive"
        assert connection.execute(
            "SELECT status FROM objects WHERE object_id = ?", (manual_id,)
        ).fetchone()[0] == "active"
