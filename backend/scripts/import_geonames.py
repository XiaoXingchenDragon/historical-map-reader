from __future__ import annotations

import csv
import json
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
IMPORT_DIR = ROOT / "data" / "imports" / "geonames"
GAZETTEER_DIR = ROOT / "data" / "gazetteer"
CITIES_ZIP = IMPORT_DIR / "cities15000.zip"
ALTERNATES_ZIP = IMPORT_DIR / "alternateNamesV2.zip"
OUTPUT_PATH = GAZETTEER_DIR / "geonames_cities15000_zh_en.json"

LANGUAGES = {"en", "zh", "zh-CN", "zh-Hans", "zh-Hant", "zh-TW", "zh-HK"}
ZH_LANGUAGES = {"zh", "zh-CN", "zh-Hans", "zh-Hant", "zh-TW", "zh-HK"}


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
        names = ", ".join(archive.namelist()[:5])
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


def read_cities() -> dict[int, dict]:
    cities: dict[int, dict] = {}
    with open_zip_member(CITIES_ZIP, "cities15000.txt") as handle:
        reader = csv.reader((line.decode("utf-8") for line in handle), delimiter="\t")
        for row in reader:
            if len(row) < 19:
                continue
            geonameid = int(row[0])
            cities[geonameid] = {
                "geonameid": geonameid,
                "canonical_name": row[1],
                "ascii_name": row[2],
                "country_code": row[8],
                "admin1_code": row[10],
                "feature_class": row[6],
                "feature_code": row[7],
                "population": int(row[14] or 0),
                "lat": float(row[4]),
                "lng": float(row[5]),
                "aliases": set(),
                "source": "geonames_cities15000",
            }
    return cities


def merge_alternate_names(cities: dict[int, dict]) -> None:
    city_ids = set(cities)
    with open_zip_member(ALTERNATES_ZIP, "alternateNamesV2.txt") as handle:
        reader = csv.reader((line.decode("utf-8") for line in handle), delimiter="\t")
        for row in reader:
            if len(row) < 4:
                continue
            try:
                geonameid = int(row[1])
            except ValueError:
                continue
            if geonameid not in city_ids:
                continue
            language = row[2]
            if language not in LANGUAGES:
                continue
            alias = row[3].strip()
            if valid_alias(alias):
                cities[geonameid]["aliases"].add(alias)


def has_chinese_alias(record: dict) -> bool:
    return any(any("\u4e00" <= char <= "\u9fff" for char in alias) for alias in record["aliases"])


def normalize_record(record: dict) -> dict:
    aliases = {record["canonical_name"], record["ascii_name"], *record["aliases"]}
    aliases = sorted(alias.strip() for alias in aliases if valid_alias(alias))
    return {
        **{key: value for key, value in record.items() if key != "aliases"},
        "aliases": aliases,
    }


def main() -> None:
    require_file(CITIES_ZIP, "https://download.geonames.org/export/dump/cities15000.zip")
    require_file(ALTERNATES_ZIP, "https://download.geonames.org/export/dump/alternateNamesV2.zip")
    GAZETTEER_DIR.mkdir(parents=True, exist_ok=True)

    cities = read_cities()
    merge_alternate_names(cities)
    records = [normalize_record(record) for record in cities.values() if has_chinese_alias(record)]
    records.sort(key=lambda item: item["population"], reverse=True)

    OUTPUT_PATH.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {len(records)} records to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
