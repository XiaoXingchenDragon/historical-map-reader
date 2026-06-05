import json
import re
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ManualCorrection, Place, PlaceAlias


GAZETTEER_DIR = Path(__file__).resolve().parent.parent / "data" / "gazetteer"
GAZETTEER_PATH = GAZETTEER_DIR / "places.json"
GEONAMES_PATH = GAZETTEER_DIR / "geonames_cities15000_zh_en.json"
GEONAMES_GLOBAL_PATH = GAZETTEER_DIR / "geonames_global_zh_en.json"
HISTORICAL_ALIASES_PATH = GAZETTEER_DIR / "historical_aliases.json"

FEATURE_PRIORITY = {
    "PCLI": 100,
    "ADM1": 90,
    "ADM2": 75,
    "country": 100,
    "admin_region": 85,
    "PPLC": 80,
    "PPLA": 75,
    "PPLA2": 70,
    "PPLA3": 65,
    "PPL": 58,
    "PPLX": 50,
    "ARCH": 72,
    "RUIN": 65,
    "HSTS": 65,
    "STM": 55,
    "STMI": 55,
    "LK": 55,
    "LKS": 55,
    "ISL": 55,
    "ISLS": 55,
    "MT": 45,
    "MTS": 45,
    "RGN": 45,
    "AREA": 42,
    "BAY": 45,
    "GULF": 45,
    "PRK": 45,
    "VAL": 45,
}

EN_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]*")
LOW_PRIORITY_NATURAL_CODES = {"MT", "MTS", "STM", "STMI", "LK", "LKS", "ISL", "ISLS", "RGN", "AREA", "BAY", "GULF", "PRK", "VAL"}
POPULATED_PLACE_CODES = {"ADM2", "PPLC", "PPLA", "PPLA2", "PPLA3", "PPL", "PPLX"}
ARCHAEOLOGY_CODES = {"ARCH", "RUIN", "HSTS"}
GLOBAL_SOURCE_PREFIX = "geonames_global"
ENGLISH_SINGLE_TOKEN_STOPWORDS = {
    "and",
    "bar",
    "central",
    "county",
    "early",
    "east",
    "great",
    "king",
    "kings",
    "new",
    "north",
    "of",
    "old",
    "people",
    "republic",
    "saint",
    "south",
    "state",
    "states",
    "the",
    "university",
    "west",
}


def normalize_name(value: str) -> str:
    return " ".join(value.strip().split()).casefold()


def has_chinese(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def feature_priority(place: Place) -> int:
    category = place.category or ""
    if category in FEATURE_PRIORITY:
        return FEATURE_PRIORITY[category]
    for key, score in FEATURE_PRIORITY.items():
        if key and key in category:
            return score
    return 20


def place_population(place: Place) -> int:
    if not place.note:
        return 0
    match = re.search(r"population=(\d+)", place.note)
    return int(match.group(1)) if match else 0


def place_score(place: Place) -> tuple[int, int]:
    return (feature_priority(place), place_population(place))


def high_priority_place(place: Place) -> bool:
    return feature_priority(place) >= 70


def is_global_place(place: Place) -> bool:
    return (place.source or "").startswith(GLOBAL_SOURCE_PREFIX)


class AliasIndex:
    def __init__(self) -> None:
        self.exact: dict[str, Place] = {}
        self.alias_text: dict[str, str] = {}
        self.zh_by_first: dict[str, list[tuple[str, str, Place]]] = defaultdict(list)
        self.en_by_first: dict[str, set[str]] = defaultdict(set)
        self.conflicts: dict[str, list[int]] = defaultdict(list)

    def add_alias(self, alias: str, place: Place) -> None:
        alias = alias.strip()
        normalized = normalize_name(alias)
        if not normalized or len(normalized) < 2:
            return
        if self._skip_alias(alias, place):
            return

        existing = self.exact.get(normalized)
        if existing and existing.id != place.id:
            self.conflicts[normalized].append(place.id)
            if place_score(existing) >= place_score(place):
                return

        self.exact[normalized] = place
        self.alias_text[normalized] = alias

    def finalize(self) -> None:
        self.zh_by_first.clear()
        self.en_by_first.clear()
        for normalized, place in self.exact.items():
            alias = self.alias_text[normalized]
            if has_chinese(alias):
                self.zh_by_first[alias[0]].append((alias, normalized, place))
                continue
            first = self._first_english_token(alias)
            if first:
                self.en_by_first[first].add(normalized)

        for first in list(self.zh_by_first):
            self.zh_by_first[first].sort(key=lambda item: len(item[0]), reverse=True)

    def lookup(self, value: str) -> Place | None:
        return self.exact.get(normalize_name(value))

    def lookup_english_tokens(self, value: str) -> Place | None:
        for token in EN_TOKEN_RE.findall(value):
            place = self.lookup(token)
            if place:
                return place
        return None

    def lookup_parenthetical_english_tokens(self, value: str) -> Place | None:
        tokens = EN_TOKEN_RE.findall(value)
        if not tokens:
            return None

        matched_places: list[Place] = []
        for token in tokens:
            normalized = normalize_name(token)
            if normalized in ENGLISH_SINGLE_TOKEN_STOPWORDS:
                continue
            place = self.lookup(token)
            if not place:
                if len(tokens) == 1:
                    return None
                return None
            matched_places.append(place)

        if not matched_places:
            return None
        first_place = matched_places[0]
        if any(place.id != first_place.id for place in matched_places):
            return None
        return first_place

    def iter_matches(self, text: str) -> list[dict]:
        matches = []
        matches.extend(self._iter_chinese_matches(text))
        if any(char.isascii() and char.isalpha() for char in text):
            matches.extend(self._iter_english_matches(text))
        return sorted(matches, key=lambda item: (item["start_offset"], item["end_offset"]))

    def _iter_chinese_matches(self, text: str) -> list[dict]:
        matches = []
        for start, char in enumerate(text):
            if char not in self.zh_by_first:
                continue
            for alias, _normalized, place in self.zh_by_first[char]:
                end = start + len(alias)
                if text.startswith(alias, start):
                    matches.append(self._mention(text[start:end], start, end, place))
        return matches

    def _iter_english_matches(self, text: str) -> list[dict]:
        tokens = [(match.group(0), match.start(), match.end()) for match in EN_TOKEN_RE.finditer(text)]
        matches = []
        max_words = 6
        for index, (token, _start, _end) in enumerate(tokens):
            first = normalize_name(token)
            if first not in self.en_by_first:
                continue
            for size in range(min(max_words, len(tokens) - index), 0, -1):
                phrase = text[tokens[index][1] : tokens[index + size - 1][2]]
                normalized = normalize_name(phrase)
                if normalized not in self.en_by_first[first]:
                    continue
                place = self.exact.get(normalized)
                if place:
                    matches.append(self._mention(phrase, tokens[index][1], tokens[index + size - 1][2], place))
                    break
        return matches

    def _mention(self, raw: str, start: int, end: int, place: Place) -> dict:
        return {
            "raw_name": raw,
            "start_offset": start,
            "end_offset": end,
            "place": place,
            "confidence": 0.95 if high_priority_place(place) else 0.78,
        }

    def _skip_alias(self, alias: str, place: Place) -> bool:
        priority = feature_priority(place)
        population = place_population(place)
        is_global = is_global_place(place)
        category = place.category or ""
        if has_chinese(alias):
            if len(alias) <= 1:
                return True
            if len(alias) == 2 and priority < 70:
                return True
            if is_global:
                if priority >= 90:
                    return False
                if category in ARCHAEOLOGY_CODES:
                    return len(alias) < 3
                if category in LOW_PRIORITY_NATURAL_CODES and len(alias) < 4:
                    return True
                if category in POPULATED_PLACE_CODES:
                    return len(alias) < 3 or population < 200000
                if priority < 55 and len(alias) < 4 and population < 200000:
                    return True
            return False
        tokens = EN_TOKEN_RE.findall(alias)
        if not tokens:
            return True
        normalized_tokens = {normalize_name(token) for token in tokens}
        if is_global and normalized_tokens & {"university", "college", "institute", "school"}:
            return True
        if len(tokens) == 1:
            token = tokens[0]
            normalized_token = normalize_name(token)
            if normalized_token in ENGLISH_SINGLE_TOKEN_STOPWORDS:
                return True
            if is_global and priority < 90:
                return True
            if len(token) <= 3 and priority < 70:
                return True
            if is_global and priority < 70 and population < 100000 and len(token) < 6:
                return True
            if is_global and category in LOW_PRIORITY_NATURAL_CODES and len(token) < 7:
                return True
        return False

    def _first_english_token(self, value: str) -> str:
        match = EN_TOKEN_RE.search(value)
        return normalize_name(match.group(0)) if match else ""


def _read_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _record_key(item: dict) -> tuple[str, str]:
    geonameid = str(item.get("geonameid", "") or "")
    canonical = normalize_name(item["canonical_name"])
    return (geonameid, canonical)


def _merge_aliases(target: dict, aliases: list[str]) -> None:
    existing = {normalize_name(alias) for alias in target.setdefault("aliases", [])}
    for alias in aliases:
        if not alias:
            continue
        normalized = normalize_name(alias)
        if normalized and normalized not in existing:
            target["aliases"].append(alias)
            existing.add(normalized)


def record_feature_priority(record: dict) -> int:
    if record.get("source") == "seed":
        return 120
    category = record.get("category") or record.get("feature_code") or ""
    if category in FEATURE_PRIORITY:
        return FEATURE_PRIORITY[category]
    for key, score in FEATURE_PRIORITY.items():
        if key and key in category:
            return score
    return 20


def record_score(record: dict) -> tuple[int, int]:
    return (record_feature_priority(record), record.get("population", 0) or 0)


def load_gazetteer() -> list[dict]:
    records: list[dict] = []
    by_geonameid: dict[str, dict] = {}
    by_canonical: dict[str, dict] = {}

    def add_or_merge(item: dict) -> dict:
        item = dict(item)
        item.setdefault("aliases", [])
        item.setdefault("population", 0)
        item.setdefault("source", "gazetteer")
        item.setdefault("note", "")
        item.setdefault("category", item.get("feature_code", ""))
        item.setdefault("period", "")
        item.setdefault("region", item.get("country_code", ""))
        item.setdefault("context_keywords", [])

        geonameid = str(item.get("geonameid", "") or "")
        canonical = normalize_name(item["canonical_name"])
        existing = by_geonameid.get(geonameid) if geonameid else None
        if not existing and not geonameid:
            canonical_match = by_canonical.get(canonical)
            if canonical_match and not canonical_match.get("geonameid"):
                existing = canonical_match
        if existing:
            _merge_aliases(existing, [item["canonical_name"], item.get("ascii_name", ""), *item.get("aliases", [])])
            existing["population"] = max(existing.get("population", 0), item.get("population", 0) or 0)
            return existing

        _merge_aliases(item, [item["canonical_name"], item.get("ascii_name", "")])
        records.append(item)
        if geonameid:
            by_geonameid[geonameid] = item
        by_canonical[canonical] = item
        return item

    for path in [GEONAMES_GLOBAL_PATH, GEONAMES_PATH]:
        for item in _read_json(path, []):
            add_or_merge(item)
    for item in _read_json(GAZETTEER_PATH, []):
        add_or_merge(item)

    for patch in _read_json(HISTORICAL_ALIASES_PATH, []):
        target = by_canonical.get(normalize_name(patch["canonical_name"]))
        if target:
            _merge_aliases(target, patch.get("aliases", []))

    alias_owner: dict[str, dict] = {}
    for item in sorted(records, key=record_score, reverse=True):
        kept_aliases = []
        for alias in [item["canonical_name"], *item.get("aliases", [])]:
            normalized = normalize_name(alias)
            if not normalized:
                continue
            owner = alias_owner.get(normalized)
            if owner and owner is not item:
                continue
            alias_owner[normalized] = item
            kept_aliases.append(alias)
        item["aliases"] = kept_aliases

    return records


def guess_language(value: str) -> str:
    if any("\u4e00" <= char <= "\u9fff" for char in value):
        return "zh"
    if value.isascii():
        return "en"
    return ""


def upsert_alias(session: Session, place: Place, alias: str, source: str = "gazetteer") -> PlaceAlias:
    normalized = normalize_name(alias)
    existing = session.scalar(select(PlaceAlias).where(PlaceAlias.normalized_name == normalized))
    if existing:
        existing.place_id = place.id
        existing.alias = alias
        existing.source = source
        existing.language = existing.language or guess_language(alias)
        return existing

    place_alias = PlaceAlias(
        place_id=place.id,
        alias=alias,
        normalized_name=normalized,
        language=guess_language(alias),
        source=source,
    )
    session.add(place_alias)
    return place_alias


def seed_places(session: Session) -> None:
    existing_places = {
        place.canonical_name: place
        for place in session.scalars(select(Place)).all()
    }
    existing_aliases = {
        alias.normalized_name: alias
        for alias in session.scalars(select(PlaceAlias)).all()
    }

    for item in load_gazetteer():
        existing = existing_places.get(item["canonical_name"])
        if existing:
            place = existing
            place.lat = item["lat"]
            place.lng = item["lng"]
            place.source = item.get("source", place.source)
            place.note = item.get("note", place.note)
            place.category = item.get("category", place.category or "")
            place.period = item.get("period", place.period or "")
            place.region = item.get("region", place.region or "")
            context_keywords = item.get("context_keywords", [])
            place.context_keywords = ",".join(context_keywords) if isinstance(context_keywords, list) else str(context_keywords)
        else:
            context_keywords = item.get("context_keywords", [])
            note = item.get("note", "")
            if item.get("geonameid"):
                note = f"{note}; GeoNames geonameid={item['geonameid']}; population={item.get('population', 0)}".strip("; ")
            place = Place(
                canonical_name=item["canonical_name"],
                lat=item["lat"],
                lng=item["lng"],
                source=item.get("source", "gazetteer"),
                note=note,
                category=item.get("category", ""),
                period=item.get("period", ""),
                region=item.get("region", ""),
                context_keywords=",".join(context_keywords) if isinstance(context_keywords, list) else str(context_keywords),
            )
            session.add(place)
            session.flush()
            existing_places[place.canonical_name] = place

        seen_aliases: set[str] = set()
        for alias in [item["canonical_name"], *item.get("aliases", [])]:
            if alias:
                normalized_alias = normalize_name(alias)
                if normalized_alias in seen_aliases:
                    continue
                seen_aliases.add(normalized_alias)
                upsert_alias_cached(session, place, alias, item.get("source", "gazetteer"), existing_aliases)
    session.commit()


def upsert_alias_cached(
    session: Session,
    place: Place,
    alias: str,
    source: str,
    existing_aliases: dict[str, PlaceAlias],
) -> PlaceAlias:
    normalized = normalize_name(alias)
    existing = existing_aliases.get(normalized)
    if existing:
        existing.place_id = place.id
        existing.alias = alias
        existing.source = source
        existing.language = existing.language or guess_language(alias)
        return existing

    place_alias = PlaceAlias(
        place_id=place.id,
        alias=alias,
        normalized_name=normalized,
        language=guess_language(alias),
        source=source,
    )
    session.add(place_alias)
    existing_aliases[normalized] = place_alias
    return place_alias


def aliases_by_place(session: Session) -> AliasIndex:
    rows = session.execute(select(PlaceAlias, Place).join(Place, Place.id == PlaceAlias.place_id)).all()
    index = AliasIndex()
    for alias, place in rows:
        index.add_alias(alias.alias, place)
    index.finalize()
    return index


def resolve_place(session: Session, raw_name: str, gazetteer_aliases: AliasIndex) -> Place | None:
    correction = session.scalar(
        select(ManualCorrection).where(ManualCorrection.raw_name == raw_name)
    )
    if correction:
        return correction.place

    return gazetteer_aliases.lookup(raw_name)
