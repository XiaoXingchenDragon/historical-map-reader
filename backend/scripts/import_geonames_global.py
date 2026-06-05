from __future__ import annotations

import csv
import json
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
IMPORT_DIR = ROOT / "data" / "imports" / "geonames"
GAZETTEER_DIR = ROOT / "data" / "gazetteer"
ALL_COUNTRIES_ZIP = IMPORT_DIR / "allCountries.zip"
ALTERNATES_ZIP = IMPORT_DIR / "alternateNamesV2.zip"
OUTPUT_PATH = GAZETTEER_DIR / "geonames_global_zh_en.json"

LANGUAGES = {"en", "zh", "zh-CN", "zh-Hans", "zh-Hant", "zh-TW", "zh-HK"}
ZH_LANGUAGES = {"zh", "zh-CN", "zh-Hans", "zh-Hant", "zh-TW", "zh-HK"}

FEATURE_ALLOWLIST = {
    "A": {"PCLI", "ADM1", "ADM2"},
    "H": {"STM", "STMI", "LK", "LKS", "BAY", "GULF"},
    "L": {"RGN", "AREA", "PRK"},
    "P": {"PPLC", "PPLA", "PPLA2", "PPLA3", "PPL", "PPLX"},
    "S": {"ARCH", "RUIN", "HSTS"},
    "T": {"ISL", "ISLS", "MT", "MTS", "VAL"},
}

FEATURE_PRIORITY = {
    "PCLI": 100,
    "ADM1": 90,
    "ADM2": 78,
    "PPLC": 75,
    "PPLA": 70,
    "PPLA2": 65,
    "PPLA3": 60,
    "PPLA4": 55,
    "PPL": 50,
    "PPLX": 45,
    "STM": 42,
    "STMI": 40,
    "LK": 40,
    "LKS": 38,
    "ISL": 38,
    "ISLS": 36,
    "MT": 35,
    "MTS": 35,
    "RGN": 32,
    "AREA": 30,
    "BAY": 40,
    "GULF": 40,
    "PRK": 34,
    "VAL": 34,
    "ARCH": 30,
    "RUIN": 30,
    "HSTS": 30,
}

ADMIN2_MIN_POPULATION = 50000
ADMIN2_ZH_MIN_POPULATION = 20000
PPLA2_MIN_POPULATION = 50000
PPLA2_ZH_MIN_POPULATION = 20000
PPLA3_MIN_POPULATION = 100000
PPLA3_ZH_MIN_POPULATION = 50000
PPL_MIN_POPULATION = 100000
PPL_ZH_MIN_POPULATION = 50000


def require_file(path: Path, download_url: str) -> None:
    if path.exists():
        return
    raise SystemExit(
        f"Missing required GeoNames dump file:\n"
        f"  {path}\n\n"
        f"Download it from:\n"
        f"  {download_url}\n\n"
        f"Then place it in:\n"
        f"  {IMPORT_DIR}"
    )


def open_zip_member(path: Path, expected_name: str):
    archive = ZipFile(path)
    if expected_name not in archive.namelist():
        names = ", ".join(archive.namelist()[:8])
        raise ValueError(f"{path} does not contain {expected_name}. Found: {names}")
    return archive.open(expected_name, "r")


def valid_alias(value: str) -> bool:
    stripped = value.strip()
    if len(stripped) < 2:
        return False
    if stripped.startswith(("http://", "https://")):
        return False
    if stripped in {"-", "--", "N/A", "n/a"}:
        return False
    return True


def feature_allowed(feature_class: str, feature_code: str) -> bool:
    return feature_code in FEATURE_ALLOWLIST.get(feature_class, set())


def read_geonames() -> dict[int, dict]:
    records: dict[int, dict] = {}
    with open_zip_member(ALL_COUNTRIES_ZIP, "allCountries.txt") as handle:
        reader = csv.reader((line.decode("utf-8") for line in handle), delimiter="\t")
        for row in reader:
            if len(row) < 19:
                continue
            feature_class = row[6]
            feature_code = row[7]
            if not feature_allowed(feature_class, feature_code):
                continue
            try:
                geonameid = int(row[0])
                population = int(row[14] or 0)
                lat = float(row[4])
                lng = float(row[5])
            except ValueError:
                continue
            records[geonameid] = {
                "geonameid": geonameid,
                "canonical_name": row[1],
                "ascii_name": row[2],
                "country_code": row[8],
                "admin1_code": row[10],
                "admin2_code": row[11],
                "feature_class": feature_class,
                "feature_code": feature_code,
                "population": population,
                "lat": lat,
                "lng": lng,
                "aliases": set(),
                "has_zh_alias": False,
                "source": "geonames_global",
            }
    return records


def merge_alternate_names(records: dict[int, dict]) -> None:
    record_ids = set(records)
    with open_zip_member(ALTERNATES_ZIP, "alternateNamesV2.txt") as handle:
        reader = csv.reader((line.decode("utf-8") for line in handle), delimiter="\t")
        for row in reader:
            if len(row) < 4:
                continue
            try:
                geonameid = int(row[1])
            except ValueError:
                continue
            if geonameid not in record_ids:
                continue
            language = row[2]
            if language not in LANGUAGES:
                continue
            alias = row[3].strip()
            if not valid_alias(alias):
                continue
            records[geonameid]["aliases"].add(alias)
            if language in ZH_LANGUAGES or any("\u4e00" <= char <= "\u9fff" for char in alias):
                records[geonameid]["has_zh_alias"] = True


def should_keep(record: dict) -> bool:
    feature_class = record["feature_class"]
    feature_code = record["feature_code"]
    population = record["population"]
    has_zh_alias = record["has_zh_alias"]

    if feature_code in {"PCLI", "ADM1"}:
        return True
    if feature_code == "ADM2":
        return population >= ADMIN2_MIN_POPULATION or (has_zh_alias and population >= ADMIN2_ZH_MIN_POPULATION)
    if feature_code in {"PPLC", "PPLA"}:
        return True
    if feature_code == "PPLA2":
        return population >= PPLA2_MIN_POPULATION or (has_zh_alias and population >= PPLA2_ZH_MIN_POPULATION)
    if feature_code == "PPLA3":
        return population >= PPLA3_MIN_POPULATION or (has_zh_alias and population >= PPLA3_ZH_MIN_POPULATION)
    if feature_code in {"PPL", "PPLX"}:
        return population >= PPL_MIN_POPULATION or (has_zh_alias and population >= PPL_ZH_MIN_POPULATION)
    if feature_class in {"H", "L", "S", "T"}:
        return has_zh_alias
    return False


def normalize_record(record: dict) -> dict:
    aliases = {record["canonical_name"], record["ascii_name"], *record["aliases"]}
    aliases = sorted(alias.strip() for alias in aliases if valid_alias(alias))
    return {
        **{key: value for key, value in record.items() if key not in {"aliases", "has_zh_alias"}},
        "aliases": aliases,
        "category": record["feature_code"],
        "region": record["country_code"],
        "context_keywords": [record["country_code"], record["feature_code"]],
    }


def sort_key(record: dict) -> tuple[int, int, str]:
    return (
        FEATURE_PRIORITY.get(record["feature_code"], 0),
        record["population"],
        record["canonical_name"],
    )


def main() -> None:
    require_file(ALL_COUNTRIES_ZIP, "https://download.geonames.org/export/dump/allCountries.zip")
    require_file(ALTERNATES_ZIP, "https://download.geonames.org/export/dump/alternateNamesV2.zip")
    GAZETTEER_DIR.mkdir(parents=True, exist_ok=True)

    records = read_geonames()
    merge_alternate_names(records)
    output = [normalize_record(record) for record in records.values() if should_keep(record)]
    output.sort(key=sort_key, reverse=True)

    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(output)} records to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
