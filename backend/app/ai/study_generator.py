from __future__ import annotations

import json


STUDY_SYSTEM_INSTRUCTION = """You are Recall's grounded Study Mode generator.
Create learning material only from the supplied evidence. Do not use outside knowledge.
Every item must include source_refs containing only the supplied integer reference numbers.
Return JSON only with exactly these keys: flashcards, mcqs, short_answers.
Generate 3-5 flashcards, 2-3 multiple-choice questions with four options, and 1-2 short-answer questions.
Each flashcard has front, back, source_refs. Each MCQ has question, options, answer, explanation, source_refs.
Each short answer has question, answer, source_refs. If evidence cannot support an item, omit it.
"""


def study_prompt(context: str, title: str) -> str:
    return f"""Study target: {title}

Evidence:
{context}

Build a concise study set grounded in this evidence. Preserve page references through source_refs.
"""


def parse_study_generation(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].rstrip()
    payload = json.loads(cleaned)
    if not isinstance(payload, dict):
        raise ValueError("study generation must be a JSON object")
    return payload


def sm2_next_review(ease_factor: float, interval_days: int, quality: int) -> tuple[int, float]:
    if quality < 0 or quality > 5:
        raise ValueError("quality must be between 0 and 5")
    if quality < 3:
        interval = 1
    elif interval_days <= 1:
        interval = 6
    else:
        interval = round(interval_days * ease_factor)
    updated_ease = max(
        1.3,
        ease_factor + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02),
    )
    return max(1, interval), updated_ease
