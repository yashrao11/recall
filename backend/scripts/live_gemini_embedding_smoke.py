"""Manual, opt-in Gemini Embedding 2 smoke test.

Run from the repository root with:

    PYTHONPATH=backend .venv/bin/python backend/scripts/live_gemini_embedding_smoke.py

This script makes real API requests only when ALLOW_CLOUD_AI=true and
GEMINI_API_KEY is present. It creates synthetic images in a temporary folder,
uses a temporary SQLite/vector store, and never touches mounted user folders.
"""

from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import tempfile

from PIL import Image, ImageDraw

from app.ai.providers import build_embedding_provider
from app.config import Settings, load_project_environment
from app.db.database import get_connection, initialize_database
from app.retrieval.vector_store import FaissVectorStore
from app.retrieval.visual_search import visual_search


DIMENSION = 768
QUERY = "Find the neural network diagram showing convolution, max pooling, and a classifier."


def main() -> int:
    load_project_environment()
    api_key = os.getenv("GEMINI_API_KEY", "")
    if os.getenv("ALLOW_CLOUD_AI", "false").lower() not in {"1", "true", "yes"}:
        print("SKIPPED: set ALLOW_CLOUD_AI=true to run this live smoke test.")
        return 2
    if not api_key:
        print("SKIPPED: GEMINI_API_KEY is not configured.")
        return 2

    with tempfile.TemporaryDirectory(prefix="recall-gemini-smoke-") as temporary:
        root = Path(temporary)
        data_dir = root / "data"
        database_path = data_dir / "recall.sqlite3"
        initialize_database(database_path)
        settings = Settings(
            data_dir=data_dir,
            allow_cloud_ai=True,
            embedding_provider="gemini",
            embedding_dimension=DIMENSION,
            gemini_api_key=api_key,
            gemini_embedding_model=os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-2"),
        )

        try:
            provider = build_embedding_provider(settings)
            relevant = _synthetic_diagram(root / "neural-network.png")
            unrelated = _synthetic_control(root / "control.png")
            with get_connection(database_path) as connection:
                document_vector = provider.embed_text(
                    "A neural network diagram with convolution, max pooling, and a classifier.",
                    hashlib.sha256(b"synthetic-document").hexdigest(),
                    connection,
                )
                query_vector = provider.embed_query(
                    QUERY, hashlib.sha256(QUERY.encode()).hexdigest(), connection
                )
                relevant_vector = provider.embed_image(
                    relevant.read_bytes(), "image/png", _hash_file(relevant), connection
                )
                unrelated_vector = provider.embed_image(
                    unrelated.read_bytes(), "image/png", _hash_file(unrelated), connection
                )
                _assert_vector("document", document_vector)
                _assert_vector("query", query_vector)
                _assert_vector("relevant image", relevant_vector)
                _assert_vector("unrelated image", unrelated_vector)

                mount_id = connection.execute(
                    "INSERT INTO folder_mounts(path, label) VALUES (?, ?)", (str(root), "synthetic")
                ).lastrowid
                source_id = connection.execute(
                    "INSERT INTO sources(mount_id, source_type, title, original_path, content_hash) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (mount_id, "image", relevant.name, str(relevant), _hash_file(relevant)),
                ).lastrowid
                unit_id = connection.execute(
                    "INSERT INTO document_units(source_id, unit_type, extracted_text, short_comment) "
                    "VALUES (?, ?, ?, ?)",
                    (source_id, "image", "synthetic neural network diagram", "neural network diagram"),
                ).lastrowid
                connection.execute(
                    "UPDATE sources SET indexing_status = 'done' WHERE source_id = ?", (source_id,)
                )
                visual_store = FaissVectorStore(data_dir / "visual.index", DIMENSION)
                visual_store.add(int(unit_id), relevant_vector)
                visual_store.persist()
                query_relevant = _cosine(query_vector, relevant_vector)
                query_unrelated = _cosine(query_vector, unrelated_vector)
                results = visual_search(connection, settings, provider, QUERY, top_k=5)

            ranked_source = results[0]["source_id"] if results else None
            print(f"model: {provider.model}")
            print(f"dimension: {provider.dimension}")
            print(f"document_norm: {_norm(document_vector):.6f}")
            print(f"query_norm: {_norm(query_vector):.6f}")
            print(f"relevant_image_similarity: {query_relevant:.6f}")
            print(f"unrelated_image_similarity: {query_unrelated:.6f}")
            print(f"relevant_similarity_higher: {query_relevant > query_unrelated}")
            print(f"visual_search_top_source_id: {ranked_source}")
            print(f"visual_search_mapping_correct: {ranked_source == source_id}")
            print("api_key: [redacted]")
            return 0
        except Exception as exc:  # noqa: BLE001 - manual diagnostic boundary
            print(f"LIVE_SMOKE_ERROR: {_safe_error(exc, api_key)}")
            return 1


def _assert_vector(label: str, vector: list[float]) -> None:
    if len(vector) != DIMENSION:
        raise RuntimeError(f"{label} dimension is {len(vector)}, expected {DIMENSION}")
    if not all(math.isfinite(value) for value in vector):
        raise RuntimeError(f"{label} contains a non-finite value")
    if not math.isclose(_norm(vector), 1.0, rel_tol=1e-3, abs_tol=1e-3):
        raise RuntimeError(f"{label} is not normalized")


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right)) / (_norm(left) * _norm(right))


def _norm(vector: list[float]) -> float:
    return math.sqrt(sum(value * value for value in vector))


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _synthetic_diagram(path: Path) -> Path:
    image = Image.new("RGB", (1000, 600), "white")
    draw = ImageDraw.Draw(image)
    boxes = [(50, 220, 230, 380, "Input"), (300, 220, 480, 380, "Convolution"), (550, 220, 730, 380, "Max Pooling"), (800, 220, 950, 380, "Classifier")]
    for index, (left, top, right, bottom, label) in enumerate(boxes):
        draw.rounded_rectangle((left, top, right, bottom), radius=20, outline="#2455a4", width=6, fill="#dbeafe")
        draw.text((left + 25, top + 65), label, fill="#102a56")
        if index < len(boxes) - 1:
            draw.line((right, 300, boxes[index + 1][0], 300), fill="#d97706", width=8)
            draw.polygon([(boxes[index + 1][0], 300), (boxes[index + 1][0] - 20, 287), (boxes[index + 1][0] - 20, 313)], fill="#d97706")
    draw.text((340, 70), "Neural Network Architecture", fill="#102a56")
    image.save(path, format="PNG")
    return path


def _synthetic_control(path: Path) -> Path:
    image = Image.new("RGB", (1000, 600), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((120, 100, 880, 500), outline="#555", width=8, fill="#eeeeee")
    for x in range(160, 850, 80):
        draw.line((x, 130, x, 470), fill="#bbbbbb", width=2)
    for y in range(150, 470, 60):
        draw.line((140, y, 860, y), fill="#bbbbbb", width=2)
    draw.ellipse((420, 230, 580, 390), fill="#9ca3af", outline="#374151", width=5)
    draw.text((390, 60), "Control Pattern", fill="#374151")
    image.save(path, format="PNG")
    return path


def _safe_error(error: Exception, secret: str) -> str:
    return str(error).replace(secret, "[redacted]")


if __name__ == "__main__":
    raise SystemExit(main())
