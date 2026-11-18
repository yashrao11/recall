import re


def rerank(query: str, results: list[dict], limit: int = 10) -> list[dict]:
    """Use token overlap as a deterministic CPU fallback for a cross-encoder."""
    query_tokens = set(re.findall(r"[a-z0-9]+", query.lower()))
    rescored = []
    for result in results:
        haystack = " ".join(
            [result.get("title", ""), result.get("snippet", ""), result.get("short_comment", ""), *result.get("tags", [])]
        ).lower()
        result = result.copy()
        overlap = len(query_tokens & set(re.findall(r"[a-z0-9]+", haystack))) / max(len(query_tokens), 1)
        result["score"] = 0.7 * overlap + 0.3 * float(result.get("score", 0.0))
        rescored.append(result)
    return sorted(rescored, key=lambda item: item["score"], reverse=True)[:limit]

