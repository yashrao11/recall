from __future__ import annotations

from pathlib import Path


class VectorStoreUnavailable(RuntimeError):
    """Raised when neither FAISS nor the NumPy development fallback is available."""


class FaissVectorStore:
    """Persistent cosine-similarity index with explicit vector-ID deletion."""

    def __init__(self, path: Path, dimension: int):
        try:
            import faiss
        except ImportError:
            faiss = None
        try:
            import numpy as np
        except ImportError as exc:
            raise VectorStoreUnavailable("Install NumPy or the optional 'retrieval' extra") from exc
        self._faiss = faiss
        self._np = np
        self.path = path
        self.dimension = dimension
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._index = self._load_or_create()

    def _load_or_create(self):
        if self._faiss is None:
            fallback_path = self.path.with_suffix(".npz")
            if fallback_path.exists():
                data = self._np.load(fallback_path)
                return {"ids": data["ids"].astype("int64"), "vectors": data["vectors"].astype("float32")}
            return {"ids": self._np.empty(0, dtype="int64"), "vectors": self._np.empty((0, self.dimension), dtype="float32")}
        if self.path.exists():
            return self._faiss.read_index(str(self.path))
        return self._faiss.IndexIDMap2(self._faiss.IndexFlatIP(self.dimension))

    def add(self, unit_id: int, vector: list[float]) -> None:
        array = self._np.asarray([vector], dtype="float32")
        if self._faiss is None:
            self.remove(unit_id)
            norm = self._np.linalg.norm(array, axis=1, keepdims=True)
            array = array / self._np.maximum(norm, 1e-12)
            self._index["ids"] = self._np.concatenate([self._index["ids"], self._np.asarray([unit_id], dtype="int64")])
            self._index["vectors"] = self._np.concatenate([self._index["vectors"], array])
            return
        self._faiss.normalize_L2(array)
        ids = self._np.asarray([unit_id], dtype="int64")
        self.remove(unit_id)
        self._index.add_with_ids(array, ids)

    def remove(self, unit_id: int) -> None:
        if self._faiss is None:
            keep = self._index["ids"] != unit_id
            self._index["ids"] = self._index["ids"][keep]
            self._index["vectors"] = self._index["vectors"][keep]
        elif self._index.ntotal:
            ids = self._np.asarray([unit_id], dtype="int64")
            self._index.remove_ids(ids)

    def remove_many(self, unit_ids: list[int]) -> None:
        if self._faiss is None:
            if unit_ids:
                keep = ~self._np.isin(self._index["ids"], self._np.asarray(unit_ids, dtype="int64"))
                self._index["ids"] = self._index["ids"][keep]
                self._index["vectors"] = self._index["vectors"][keep]
        elif self._index.ntotal and unit_ids:
            ids = self._np.asarray(unit_ids, dtype="int64")
            self._index.remove_ids(ids)

    def search(self, vector: list[float], top_k: int = 50) -> list[tuple[int, float]]:
        if self._faiss is None:
            if not len(self._index["ids"]):
                return []
            query = self._np.asarray(vector, dtype="float32")
            query = query / max(float(self._np.linalg.norm(query)), 1e-12)
            scores = self._index["vectors"] @ query
            order = self._np.argsort(-scores)[:top_k]
            return [(int(self._index["ids"][i]), float(scores[i])) for i in order]
        if not self._index.ntotal:
            return []
        query = self._np.asarray([vector], dtype="float32")
        self._faiss.normalize_L2(query)
        scores, ids = self._index.search(query, min(top_k, self._index.ntotal))
        return [(int(unit_id), float(score)) for unit_id, score in zip(ids[0], scores[0]) if unit_id != -1]

    def persist(self) -> None:
        if self._faiss is None:
            self._np.savez(
                self.path.with_suffix(".npz"),
                ids=self._index["ids"],
                vectors=self._index["vectors"],
            )
        else:
            self._faiss.write_index(self._index, str(self.path))

