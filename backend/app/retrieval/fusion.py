from collections import defaultdict


def reciprocal_rank_fusion(result_lists: list[list[dict]], k: int = 60) -> list[dict]:
    merged: dict[int, dict] = {}
    scores = defaultdict(float)
    match_types: dict[int, set[str]] = defaultdict(set)
    for results in result_lists:
        for rank, result in enumerate(results):
            unit_id = int(result["unit_id"])
            scores[unit_id] += 1.0 / (k + rank + 1)
            match_types[unit_id].update(result["match_type"])
            merged.setdefault(unit_id, result.copy())
    output = []
    for unit_id, result in merged.items():
        result["score"] = scores[unit_id]
        result["match_type"] = sorted(match_types[unit_id])
        result["visual_match"] = "visual" in match_types[unit_id]
        output.append(result)
    return sorted(output, key=lambda item: item["score"], reverse=True)

