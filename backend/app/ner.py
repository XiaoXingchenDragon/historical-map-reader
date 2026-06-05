import re
from functools import lru_cache

from app.gazetteer import AliasIndex, normalize_name


LATIN_LETTERS = r"A-Za-z\u00c0-\u024f"
ENGLISH_PLACE_STOPWORDS = {
    "about",
    "after",
    "before",
    "book",
    "chapter",
    "history",
    "pocket",
    "press",
    "review",
    "university",
}


@lru_cache(maxsize=1)
def _load_spacy():
    try:
        import spacy

        return spacy.load("en_core_web_sm")
    except Exception:
        return None


def _find_all(text: str, needle: str) -> list[tuple[int, int]]:
    if not needle:
        return []
    is_ascii = needle.isascii()
    flags = re.IGNORECASE if is_ascii else 0
    if is_ascii:
        pattern = rf"(?<![A-Za-z]){re.escape(needle)}(?![A-Za-z])"
    else:
        pattern = re.escape(needle)
    return [(match.start(), match.end()) for match in re.finditer(pattern, text, flags)]


def normalize_parenthetical_english(value: str) -> str:
    value = re.sub(rf"(?<=[{LATIN_LETTERS}])-\s*(?=[{LATIN_LETTERS}])", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _trim_english_offsets(raw: str, start: int) -> tuple[str, int, int]:
    left_trimmed = raw.lstrip()
    trimmed_start = start + len(raw) - len(left_trimmed)
    trimmed = left_trimmed.rstrip()
    return normalize_parenthetical_english(trimmed), trimmed_start, trimmed_start + len(trimmed)


def extract_mentions(text: str, aliases: AliasIndex) -> list[dict]:
    found: dict[tuple[int, int, str], dict] = {}

    occupied: list[tuple[int, int]] = []
    for match in aliases.iter_matches(text):
        start = match["start_offset"]
        end = match["end_offset"]
        if any(start < used_end and end > used_start for used_start, used_end in occupied):
            continue
        found[(start, end, normalize_name(match["raw_name"]))] = match
        occupied.append((start, end))

    nlp = _load_spacy()
    if nlp and any("a" <= char.lower() <= "z" for char in text):
        doc = nlp(text)
        for ent in doc.ents:
            if ent.label_ not in {"GPE", "LOC"}:
                continue
            key = (ent.start_char, ent.end_char, normalize_name(ent.text))
            if key not in found:
                found[key] = {
                    "raw_name": ent.text,
                    "start_offset": ent.start_char,
                    "end_offset": ent.end_char,
                    "place": None,
                    "confidence": 0.65,
                }

    return sorted(found.values(), key=lambda item: (item["start_offset"], item["end_offset"]))


PLACE_SUFFIXES = [
    "\u7fa4\u5c9b",  # islands
    "\u6d77\u5cb8",  # coast
    "\u534a\u5c9b",  # peninsula
    "\u6d77\u5ce1",  # strait
    "\u738b\u56fd",  # kingdom
    "\u5e1d\u56fd",  # empire
    "\u8981\u585e",  # fortress
    "\u6e2f\u53e3",  # port
    "\u5730\u533a",  # region
    "\u6f5f\u6e56",  # lagoon
    "\u5c0f\u9547",  # town
    "\u57ce\u9547",  # town
    "\u5dde\u7701",  # province/state
    "\u5c9b",
    "\u6d77",
    "\u6e2f",
    "\u9547",
    "\u6cb3",
    "\u5e02",
    "\u7701",
]

STOP_CANDIDATES = {
    "\u4efb\u4f55\u963b\u6321",
    "\u7ecf\u6d4e\u635f\u5931",
    "\u4eba\u53e3\u4e0b\u964d",
    "\u6d77\u4e0a\u8d38\u6613",
    "\u8054\u5408\u884c\u52a8",
    "\u5927\u6d77",
    "\u8fd1\u6d77",
    "\u5916\u6d77",
    "\u51fa\u6d77",
    "\u4e0b\u6d77",
    "\u6d77\u5cb8",
    "\u6d77\u6e2f",
    "\u6e2f\u53e3",
    "\u8981\u585e",
    "\u5730\u4e2d\u6d77",
    "\u57ce\u5e02",
    "\u4e0a\u5e02",
    "\u96c6\u5e02",
    "\u591c\u5e02",
    "\u7701\u5e02",
    "\u5916\u7701",
    "\u672c\u7701",
    "\u8282\u7701",
    "\u53cd\u7701",
    "\u661f\u6cb3",
    "\u94f6\u6cb3",
}


def clean_candidate_raw(raw: str) -> str:
    value = raw.strip("\u201c\u201d\uff0c\u3002\uff1b\uff1a\u3001 ")
    for marker in [
        "\u9000\u5230",
        "\u8fdb\u653b",
        "\u6d17\u52ab",
        "\u63a0\u8d70",
        "\u64a4\u5230",
        "\u711a\u70e7\u4e86",
        "\u70e7\u6bc1\u4e86",
        "\u53d1\u73b0",
        "\u56f4\u56f0\u5728",
        "\u5230",
        "\u5728",
        "\u4e8e",
        "\u4ece",
        "\u5411",
        "\u5f80",
        "\u5c06",
        "\u628a",
    ]:
        if marker in value:
            value = value.rsplit(marker, 1)[-1]

    if "\u4e86" in value:
        value = value.rsplit("\u4e86", 1)[-1]

    return value.strip("\u201c\u201d\uff0c\u3002\uff1b\uff1a\u3001 ")


PARENTHETICAL_PLACE_PATTERN = re.compile(
    rf"(?P<zh>[\u4e00-\u9fff\u00b7]{{2,20}})[\u201d\u2019\"']?\s*[\uff08(]\s*"
    rf"[\u201c\u2018\"']?(?P<en>[{LATIN_LETTERS}][^\u201c\u201d\u2018\u2019\"'\uff08\uff09()\u4e00-\u9fff]{{1,100}})"
    rf"[\u201d\u2019\"']?\s*[\uff09)]"
)
QUOTED_ENGLISH_PATTERN = re.compile(
    rf"[\u201c\u2018\"']\s*(?P<en>[{LATIN_LETTERS}][{LATIN_LETTERS}\s'.-]{{1,80}}?)\s*[\u201d\u2019\"']"
)
REFERENCE_HEADING_PATTERN = re.compile(
    r"^\s*(?:"
    r"\u53c2\u8003\u6587\u732e|\u53c2\u8003\u4e66\u76ee|\u5f15\u7528\u6587\u732e|\u5f15\u6587|"
    r"\u6ce8\u91ca|\u5c3e\u6ce8|\u4e66\u76ee|\u7d22\u5f15|"
    r"Bibliography|References?|Works\s+Cited|Notes?|Endnotes?|Index"
    r")\s*[:：]?\s*$",
    re.IGNORECASE,
)
REFERENCE_PARAGRAPH_PATTERN = re.compile(
    r"^\s*(?:\[\d+\]|\d+[.)、]|[A-Z][A-Za-z\-]+,\s+[A-Z]|.+\(\d{4}\)|.+\d{4}[.,])"
)


def extract_place_candidates(
    text: str,
    mentions: list[dict],
    aliases: AliasIndex | None = None,
) -> list[dict]:
    occupied = [(mention["start_offset"], mention["end_offset"]) for mention in mentions]
    suffix_pattern = "|".join(re.escape(suffix) for suffix in PLACE_SUFFIXES)
    pattern = re.compile(rf"[\u4e00-\u9fff\u00b7]{{2,10}}(?:{suffix_pattern})")
    splitter = re.compile(r"[\uff0c\u3002\uff1b\uff1a\u3001\s]|\u548c|\u4e0e|\u53ca")
    candidates: list[dict] = []
    seen: set[tuple[int, int, str]] = set()
    candidates.extend(extract_parenthetical_place_candidates(text, occupied, aliases, seen))
    candidates.extend(extract_quoted_english_place_candidates(text, occupied, aliases, seen))

    cursor = 0
    for split in splitter.finditer(text + "\u3002"):
        clause = text[cursor : split.start()]
        clause_start = cursor
        cursor = split.end()
        for match in pattern.finditer(clause):
            start = clause_start + match.start()
            end = clause_start + match.end()
            raw = clean_candidate_raw(match.group(0))
            start = end - len(raw)
            if (
                len(raw) < 2
                or raw in STOP_CANDIDATES
                or raw.startswith("\u67d0")
                or "\u7684" in raw
                or "\u8fd9" in raw
                or "\u4e2a" in raw
                or _weak_suffix_candidate(raw)
            ):
                continue
            if any(start < used_end and end > used_start for used_start, used_end in occupied):
                continue
            key = (start, end, normalize_name(raw))
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "id": "",
                    "raw_name": raw,
                    "start_offset": start,
                    "end_offset": end,
                    "reason": "suffix-rule",
                }
            )

    return sorted(candidates, key=lambda item: (item["start_offset"], item["end_offset"]))


def extract_parenthetical_place_candidates(
    text: str,
    occupied: list[tuple[int, int]],
    aliases: AliasIndex | None,
    seen: set[tuple[int, int, str]],
) -> list[dict]:
    candidates: list[dict] = []
    if aliases is None:
        return candidates

    for match in PARENTHETICAL_PLACE_PATTERN.finditer(text):
        raw_zh = match.group("zh")
        raw_en = normalize_parenthetical_english(match.group("en"))
        zh_start, zh_end = match.start("zh"), match.end("zh")
        if any(zh_start < used_end and zh_end > used_start for used_start, used_end in occupied):
            continue

        if aliases.lookup(raw_en):
            continue
        if not is_strong_parenthetical_token_match(raw_en, aliases):
            continue

        key = (zh_start, zh_end, normalize_name(raw_zh))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "id": "",
                "raw_name": raw_zh,
                "start_offset": zh_start,
                "end_offset": zh_end,
                "reason": "parenthetical-alias-token",
            }
        )

    return candidates


def extract_quoted_english_place_candidates(
    text: str,
    occupied: list[tuple[int, int]],
    aliases: AliasIndex | None,
    seen: set[tuple[int, int, str]],
) -> list[dict]:
    candidates: list[dict] = []
    if aliases is None:
        return candidates

    for match in QUOTED_ENGLISH_PATTERN.finditer(text):
        raw, start, end = _trim_english_offsets(match.group("en"), match.start("en"))
        has_parenthetical_zh_context = _has_parenthetical_zh_context(text, match.start())
        is_known_alias = aliases.lookup(raw) is not None
        if len(raw) < 3 or not (is_known_alias or has_parenthetical_zh_context):
            continue
        if any(start < used_end and end > used_start for used_start, used_end in occupied):
            continue
        key = (start, end, normalize_name(raw))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "id": "",
                "raw_name": raw,
                "start_offset": start,
                "end_offset": end,
                "reason": "quoted-english-alias" if is_known_alias else "parenthetical-quoted-english",
            }
        )

    return candidates


def _has_parenthetical_zh_context(text: str, quote_start: int) -> bool:
    left = text[:quote_start].rstrip()
    if not left.endswith(("(", "\uff08")):
        return False
    before_paren = left[:-1].rstrip("\u201d\u2019\"' ")
    return re.search(r"[\u4e00-\u9fff\u00b7]{2,20}$", before_paren) is not None


def is_strong_parenthetical_token_match(raw_en: str, aliases: AliasIndex) -> bool:
    if aliases.lookup(raw_en):
        return True
    tokens = re.findall(rf"[{LATIN_LETTERS}][{LATIN_LETTERS}'.-]*", raw_en)
    if len(tokens) != 1:
        return False
    token = tokens[0].strip("'.-")
    if len(token) < 4 or token.lower() in ENGLISH_PLACE_STOPWORDS:
        return False
    return aliases.lookup_english_tokens(token) is not None


def should_suppress_parenthetical_token_mention(
    text: str,
    start_offset: int,
    end_offset: int,
    aliases: AliasIndex,
) -> bool:
    for match in PARENTHETICAL_PLACE_PATTERN.finditer(text):
        raw_en = normalize_parenthetical_english(match.group("en"))
        tokens = re.findall(rf"[{LATIN_LETTERS}][{LATIN_LETTERS}'.-]*", raw_en)
        if len(tokens) <= 1 or aliases.lookup(raw_en):
            continue
        if start_offset >= match.start("en") and end_offset <= match.end("en"):
            return True
    return False


def _weak_suffix_candidate(raw: str) -> bool:
    if raw.endswith(("\u5e02", "\u7701")) and len(raw) <= 2:
        return True
    if raw.endswith("\u6cb3") and len(raw) <= 2:
        return True
    if raw.endswith(("\u5e02", "\u7701")) and raw[-3:] in STOP_CANDIDATES:
        return True
    return False


def filter_repeated_place_candidates(paragraph_candidates: list[tuple[int, int, dict]]) -> dict[int, list[dict]]:
    by_name: dict[str, list[tuple[int, int, dict]]] = {}
    for paragraph_id, paragraph_order, candidate in paragraph_candidates:
        by_name.setdefault(normalize_name(candidate["raw_name"]), []).append((paragraph_id, paragraph_order, candidate))

    filtered: dict[int, list[dict]] = {}
    for occurrences in by_name.values():
        if any(item[2].get("reason", "").startswith(("parenthetical-", "quoted-english-")) for item in occurrences):
            for paragraph_id, _paragraph_order, candidate in occurrences:
                filtered.setdefault(paragraph_id, []).append(candidate)
            continue

        if len(occurrences) < 2:
            continue

        for paragraph_id, _paragraph_order, candidate in occurrences:
            filtered.setdefault(paragraph_id, []).append(candidate)

    for candidates in filtered.values():
        candidates.sort(key=lambda item: (item["start_offset"], item["end_offset"]))
    return filtered
