import json
import os
from pathlib import Path
from typing import Any

import httpx


BACKEND_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BACKEND_DIR / ".env"


def load_local_env() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _extract_json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = value.find("{")
    end = value.rfind("}")
    if start >= 0 and end > start:
        parsed = json.loads(value[start : end + 1])
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("DeepSeek response did not contain a JSON object.")


def classify_place_candidate(
    *,
    book_title: str,
    raw_name: str,
    sentence: str,
    paragraph: str | None = None,
    all_sentences: list[str] | None = None,
) -> dict[str, Any]:
    load_local_env()
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured. Add it to backend/.env or the shell environment.")

    api_base = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com").rstrip("/")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    context_level = "expanded" if paragraph or all_sentences else "sentence"

    context = {
        "book_title": book_title,
        "candidate": raw_name,
        "sentence": sentence,
        "paragraph": paragraph or "",
        "other_sentences_containing_candidate": all_sentences or [],
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You resolve possible historical place names in Chinese history-book text. "
                "Return only valid JSON. Prefer a modern canonical place name that can match GeoNames. "
                "If the text is not a place, say not_place. If ambiguous, say ambiguous and set needs_more_context."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "Resolve this possible place candidate.",
                    "context": context,
                    "output_schema": {
                        "status": "resolved | ambiguous | not_place",
                        "raw_name": raw_name,
                        "canonical_name": "modern or historical canonical name, if known",
                        "aliases": ["Chinese or English aliases"],
                        "place_type": "city | island | coast | region | sea | strait | other",
                        "country_or_region": "short region hint",
                        "confidence": "0 to 1",
                        "needs_more_context": "true if sentence is insufficient",
                        "reason": "brief reason",
                        "llm_lat": "number or null",
                        "llm_lng": "number or null",
                    },
                },
                ensure_ascii=False,
            ),
        },
    ]
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }

    with httpx.Client(timeout=40) as client:
        response = client.post(
            f"{api_base}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    result = _extract_json_object(content)
    result["_context_level"] = context_level
    result["_request_context"] = json.dumps(context, ensure_ascii=False)
    return result
