from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.db.database import get_connection, initialize_database
from app.main import create_app


def _build_connection_app(tmp_path: Path):
    data_dir = tmp_path / "data"
    database_path = data_dir / "recall.sqlite3"
    initialize_database(database_path)
    with get_connection(database_path) as connection:
        mount_id = connection.execute(
            "INSERT INTO folder_mounts(path, label) VALUES (?, 'connections')",
            (str(tmp_path),),
        ).lastrowid
        source_one = connection.execute(
            "INSERT INTO sources(mount_id, source_type, title, original_path, indexing_status) "
            "VALUES (?, 'pdf', 'Deep Learning Notes', ?, 'done')",
            (mount_id, str(tmp_path / "deep-learning.pdf")),
        ).lastrowid
        source_two = connection.execute(
            "INSERT INTO sources(mount_id, source_type, title, original_path, indexing_status) "
            "VALUES (?, 'markdown', 'System Design Notes', ?, 'done')",
            (mount_id, str(tmp_path / "system-design.md")),
        ).lastrowid
        connection.execute(
            "INSERT INTO document_units(source_id, unit_type, page_or_slide, extracted_text) "
            "VALUES (?, 'page', 7, 'Neural networks use attention.'), (?, 'text', NULL, 'Distributed systems.')",
            (source_one, source_two),
        )
        connection.execute(
            "INSERT INTO objects(object_type, title, normalized_title, aliases_json, description, created_by, discovery_method) "
            "VALUES ('concept', 'Neural Networks', 'neural networks', '[\"NN\"]', 'Model family', 'system', 'lexical')"
        )
        connection.execute(
            "INSERT INTO objects(object_type, title, normalized_title, description, created_by, discovery_method) "
            "VALUES ('topic', 'Attention', 'attention', 'A mechanism', 'system', 'lexical')"
        )
        connection.execute(
            "INSERT INTO objects(object_type, title, normalized_title, description, created_by, discovery_method) "
            "VALUES ('project', 'System Design', 'system design', NULL, 'user', 'manual')"
        )
        connection.execute(
            "INSERT INTO objects(object_type, title, normalized_title, description, created_by, discovery_method) "
            "VALUES ('topic', 'Distributed Systems', 'distributed systems', NULL, 'system', 'lexical')"
        )
        connection.execute(
            "INSERT INTO object_evidence(object_id, source_id, unit_id, discovery_method, confidence) "
            "VALUES (1, ?, 1, 'lexical', NULL), (2, ?, 1, 'lexical', NULL), "
            "(3, ?, 2, 'manual', NULL), (4, ?, 2, 'lexical', NULL)",
            (source_one, source_one, source_two, source_two),
        )
        connection.execute(
            "INSERT INTO relationships(from_object_id, to_object_id, relationship_type, is_symmetric, "
            "identity_a, identity_b, created_by, discovery_method, reviewed_status, explanation) "
            "VALUES (1, 2, 'related_to', 1, 1, 2, 'system', 'cooccurrence', 'unreviewed', 'Same page')"
        )
        auto_relationship_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        connection.execute(
            "INSERT INTO relationship_evidence(relationship_id, source_id, unit_id, discovery_method) "
            "VALUES (?, ?, 1, 'cooccurrence')",
            (auto_relationship_id, source_one),
        )
        connection.execute(
            "INSERT INTO relationships(from_object_id, to_object_id, relationship_type, is_symmetric, "
            "identity_a, identity_b, created_by, discovery_method, reviewed_status) "
            "VALUES (2, 1, 'depends_on', 0, 2, 1, 'system', 'lexical', 'unsupported')"
        )
        unsupported_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        connection.execute(
            "INSERT INTO relationship_evidence(relationship_id, source_id, unit_id, discovery_method) "
            "VALUES (?, ?, 1, 'lexical')",
            (unsupported_id, source_one),
        )
        connection.execute(
            "INSERT INTO relationships(from_object_id, to_object_id, relationship_type, is_symmetric, "
            "identity_a, identity_b, created_by, discovery_method, reviewed_status) "
            "VALUES (3, 4, 'part_of', 0, 3, 4, 'system', 'lexical', 'unreviewed')"
        )
        manualless_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
    return create_app(Settings(data_dir=data_dir)), int(source_one), int(source_two), int(auto_relationship_id), int(unsupported_id), int(manualless_id)


def test_entity_listing_search_aliases_filters_pagination_and_empty_results(tmp_path: Path) -> None:
    app, *_ = _build_connection_app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/connections/entities", params={"q": "NN"})
        assert response.status_code == 200
        assert response.json()["entities"][0]["title"] == "Neural Networks"
        assert response.json()["entities"][0]["relationship_count"] == 1
        page = client.get("/connections/entities", params={"limit": 1, "offset": 1}).json()
        assert page["total"] == 4
        assert page["has_more"] is True
        assert client.get("/connections/entities", params={"type": "person"}).json()["total"] == 0
        assert client.get("/connections/entities", params={"status": "invalid"}).status_code == 422
        assert client.get("/connections/entities", params={"q": "does-not-exist"}).json() == {
            "entities": [], "total": 0, "limit": 50, "offset": 0, "has_more": False
        }


def test_source_connections_and_entity_detail_return_traceable_evidence(tmp_path: Path) -> None:
    app, source_one, source_two, auto_id, unsupported_id, _ = _build_connection_app(tmp_path)
    with get_connection(app.state.settings.database_path) as connection:
        extra_unit = connection.execute(
            "INSERT INTO document_units(source_id, unit_type, page_or_slide, extracted_text) VALUES (?, 'page', 8, 'Another supported passage.')",
            (source_one,),
        ).lastrowid
        connection.execute(
            "INSERT INTO relationship_evidence(relationship_id, source_id, unit_id, discovery_method) VALUES (?, ?, ?, 'cooccurrence')",
            (auto_id, source_one, extra_unit),
        )
    with TestClient(app) as client:
        source = client.get(f"/connections/sources/{source_one}").json()
        assert source["source"]["filename"] == "deep-learning.pdf"
        assert "/" not in source["source"]["filename"]
        assert len(source["entities"]) == 2
        assert source["entities"][0]["evidence"][0]["page"] == 7
        assert source["relationships"][0]["relationship_id"] == auto_id
        assert source["relationships"][0]["supported"] is True
        assert source["relationships"][0]["evidence"][0]["source_id"] == source_one
        assert len(source["relationships"][0]["evidence"]) == 1
        assert source["relationships"][0]["evidence"][0]["passage_count"] == 2
        assert all("original_path" not in item for item in source["entities"])

        detail = client.get("/connections/entities/1").json()
        assert detail["entity"]["aliases"] == ["NN"]
        assert detail["evidence"][0]["source_id"] == source_one
        assert detail["relationships"][0]["direction"] == "symmetric"
        assert detail["relationships"][0]["supported"] is True
        assert client.get(f"/connections/sources/{source_two}").json()["relationships"] == []
        assert client.get("/connections/entities/99999").status_code == 404
        assert client.get("/connections/sources/99999").status_code == 404
        hidden = client.get(f"/connections/entities/2").json()
        assert all(item["relationship_id"] != unsupported_id for item in hidden["relationships"])
        visible = client.get(
            "/connections/entities/2", params={"include_unsupported": True}
        ).json()
        assert any(item["relationship_id"] == unsupported_id for item in visible["relationships"])
        assert any(item["reviewed_status"] == "unsupported" for item in visible["relationships"])


def test_manual_relationship_creation_validates_registry_direction_and_duplicates(tmp_path: Path) -> None:
    app, _, _, _, _, _ = _build_connection_app(tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/connections/relationships",
            json={"from_object_id": 1, "to_object_id": 3, "relationship_type": "similar_to"},
        )
        assert created.status_code == 201
        assert created.json()["created_by"] == "user"
        assert created.json()["discovery_method"] == "manual"
        assert created.json()["reviewed_status"] == "confirmed"
        assert created.json()["confidence"] is None
        assert client.post(
            "/connections/relationships",
            json={"from_object_id": 3, "to_object_id": 1, "relationship_type": "similar_to"},
        ).status_code == 409
        assert client.post(
            "/connections/relationships",
            json={"from_object_id": 1, "to_object_id": 1, "relationship_type": "part_of"},
        ).status_code == 422
        assert client.post(
            "/connections/relationships",
            json={"from_object_id": 1, "to_object_id": 3, "relationship_type": "unknown"},
        ).status_code == 422
        assert client.post(
            "/connections/relationships",
            json={"from_object_id": 1, "to_object_id": 9999, "relationship_type": "part_of"},
        ).status_code == 404


def test_review_unsupported_and_safe_deletion_workflows(tmp_path: Path) -> None:
    app, _, _, auto_id, _, manualless_id = _build_connection_app(tmp_path)
    with TestClient(app) as client:
        confirmed = client.patch(
            f"/connections/relationships/{auto_id}",
            json={"reviewed_status": "confirmed"},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["reviewed_status"] == "confirmed"
        assert confirmed.json()["evidence"]
        assert client.patch(
            f"/connections/relationships/{auto_id}",
            json={"reviewed_status": "rejected"},
        ).status_code == 409
        assert client.patch(
            f"/connections/relationships/{manualless_id}",
            json={"reviewed_status": "unsupported"},
        ).status_code == 200
        assert client.delete(f"/connections/relationships/{auto_id}").status_code == 409

        manual = client.post(
            "/connections/relationships",
            json={"from_object_id": 1, "to_object_id": 3, "relationship_type": "part_of"},
        ).json()
        assert client.delete(f"/connections/relationships/{manual['relationship_id']}").status_code == 204
        assert client.delete("/connections/relationships/99999").status_code == 404
