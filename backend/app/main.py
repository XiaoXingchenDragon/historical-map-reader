import json
from pathlib import Path
import re
import shutil
from base64 import urlsafe_b64decode, urlsafe_b64encode
from threading import Lock
from uuid import uuid4

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, joinedload

from app.database import BASE_DIR, BOOKS_DIR, get_session, init_db
from app.epub_parser import parse_epub, read_epub_metadata
from app.gazetteer import AliasIndex, aliases_by_place, normalize_name, resolve_place, seed_places, upsert_alias
from app.llm import classify_place_candidate, load_local_env
from app.models import (
    Book,
    CandidateResolutionLog,
    Chapter,
    IgnoredPlaceCandidate,
    ManualCorrection,
    Paragraph,
    Place,
    PlaceAlias,
    PlaceMention,
)
from app.ner import (
    PARENTHETICAL_PLACE_PATTERN,
    extract_mentions,
    extract_place_candidates,
    filter_repeated_place_candidates,
    normalize_parenthetical_english,
    should_suppress_parenthetical_token_mention,
)
from app.pdf_parser import parse_pdf, read_pdf_metadata


app = FastAPI(title="Historical Map Reader MVP")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class MentionCorrection(BaseModel):
    place_id: int | None = None
    canonical_name: str | None = None
    lat: float | None = None
    lng: float | None = None
    note: str = ""
    save_alias: bool = True


class CandidateAction(BaseModel):
    book_id: int
    paragraph_id: int
    raw_name: str
    start_offset: int
    end_offset: int


class CandidateIgnore(BaseModel):
    book_id: int
    raw_name: str


class LocalImport(BaseModel):
    path: str


def _path_token(path: Path) -> str:
    return urlsafe_b64encode(path.name.encode("utf-8")).decode("ascii").rstrip("=")


def _path_from_token(token: str) -> str | None:
    try:
        padded = token + "=" * (-len(token) % 4)
        return urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None


def _repair_mojibake(value: str) -> str:
    try:
        repaired = value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    return repaired if repaired else value


@app.on_event("startup")
def startup() -> None:
    load_local_env()
    init_db()
    with next(get_session()) as session:
        seed_places(session)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


PROJECT_DIR = BASE_DIR.parent
PROCESS_LOCKS: dict[int, Lock] = {}
PROCESS_LOCKS_GUARD = Lock()
PROCESS_PROGRESS: dict[int, dict] = {}
PROCESS_PROGRESS_GUARD = Lock()


def process_lock_for_book(book_id: int) -> Lock:
    with PROCESS_LOCKS_GUARD:
        lock = PROCESS_LOCKS.get(book_id)
        if lock is None:
            lock = Lock()
            PROCESS_LOCKS[book_id] = lock
        return lock


def set_process_progress(
    book_id: int,
    stage: str,
    percent: int,
    current: int = 0,
    total: int = 0,
    detail: str = "",
) -> None:
    with PROCESS_PROGRESS_GUARD:
        PROCESS_PROGRESS[book_id] = {
            "book_id": book_id,
            "stage": stage,
            "percent": max(0, min(100, percent)),
            "current": current,
            "total": total,
            "detail": detail,
        }


@app.get("/api/books/{book_id}/process-progress")
def get_process_progress(book_id: int) -> dict:
    with PROCESS_PROGRESS_GUARD:
        return PROCESS_PROGRESS.get(
            book_id,
            {
                "book_id": book_id,
                "stage": "idle",
                "percent": 0,
                "current": 0,
                "total": 0,
                "detail": "",
            },
        )


def local_import_candidates() -> list[Path]:
    return sorted(
        [
            item
            for item in PROJECT_DIR.iterdir()
            if item.is_file() and item.suffix.lower() in {".epub", ".pdf"}
        ],
        key=lambda item: item.name.lower(),
    )


def validate_local_import_path(path_value: str) -> Path:
    try:
        decoded_name = _path_from_token(path_value)
        path = PROJECT_DIR / decoded_name if decoded_name else Path(_repair_mojibake(path_value))
        if not path.is_absolute():
            path = PROJECT_DIR / path
        resolved = path.resolve()
    except OSError as error:
        raise HTTPException(status_code=400, detail="Invalid local file path.") from error

    if resolved.parent != PROJECT_DIR.resolve() or resolved.suffix.lower() not in {".epub", ".pdf"}:
        raise HTTPException(status_code=400, detail="Only EPUB/PDF files in the project folder can be imported.")
    if not resolved.exists():
        raise HTTPException(status_code=404, detail="Local file not found.")
    return resolved


@app.get("/api/local-files")
def list_local_files() -> list[dict]:
    return [
        {
            "id": _path_token(item),
            "path": item.name,
            "name": item.name,
            "size": item.stat().st_size,
            "type": item.suffix.lower().lstrip("."),
        }
        for item in local_import_candidates()
    ]


@app.post("/api/books/import-local")
def import_local_book(payload: LocalImport, session: Session = Depends(get_session)) -> dict:
    source = validate_local_import_path(payload.path)
    target = BOOKS_DIR / f"{uuid4().hex}_{source.name}"
    shutil.copy2(source, target)

    metadata = read_book_metadata(str(target))
    book = Book(title=metadata["title"], author=metadata["author"], file_path=str(target))
    session.add(book)
    session.commit()
    session.refresh(book)
    return {"book_id": book.id, "title": book.title, "author": book.author}


@app.post("/api/books/upload")
async def upload_book(file: UploadFile = File(...), session: Session = Depends(get_session)) -> dict:
    filename = _repair_mojibake(file.filename or "")
    if not filename or Path(filename).suffix.lower() not in {".epub", ".pdf"}:
        raise HTTPException(status_code=400, detail="Only EPUB and PDF files are supported.")

    target = BOOKS_DIR / f"{uuid4().hex}_{Path(filename).name}"
    content = await file.read()
    target.write_bytes(content)

    metadata = read_book_metadata(str(target))
    book = Book(title=metadata["title"], author=metadata["author"], file_path=str(target))
    session.add(book)
    session.commit()
    session.refresh(book)
    return {"book_id": book.id, "title": book.title, "author": book.author}


@app.post("/api/books/{book_id}/process")
def process_book(book_id: int, session: Session = Depends(get_session)) -> dict:
    with process_lock_for_book(book_id):
        set_process_progress(book_id, "starting", 1, detail="Preparing book")
        book = session.get(Book, book_id)
        if not book:
            set_process_progress(book_id, "error", 0, detail="Book not found")
            raise HTTPException(status_code=404, detail="Book not found.")

        set_process_progress(book_id, "cleaning", 3, detail="Clearing previous parsed data")
        session.execute(delete(PlaceMention).where(PlaceMention.book_id == book_id))
        old_chapter_ids = select(Chapter.id).where(Chapter.book_id == book_id)
        session.execute(delete(Paragraph).where(Paragraph.chapter_id.in_(old_chapter_ids)))
        session.execute(delete(Chapter).where(Chapter.book_id == book_id))
        session.commit()

        seed_places(session)
        aliases = aliases_by_place(session)
        set_process_progress(book_id, "parsing", 8, detail="Reading EPUB/PDF text")
        parsed = parse_book(book.file_path)
        total_paragraphs = sum(len(chapter_data["paragraphs"]) for chapter_data in parsed)
        set_process_progress(
            book_id,
            "processing",
            10,
            current=0,
            total=total_paragraphs,
            detail=f"Processing 0/{total_paragraphs} paragraphs",
        )
        mention_count = 0
        paragraph_count = 0
        ignored_names = set(
            session.scalars(
                select(IgnoredPlaceCandidate.normalized_name).where(IgnoredPlaceCandidate.book_id == book.id)
            ).all()
        )

        for chapter_index, chapter_data in enumerate(parsed):
            chapter = Chapter(book_id=book.id, title=chapter_data["title"], order_index=chapter_index)
            session.add(chapter)
            session.flush()

            for paragraph_index, paragraph_data in enumerate(chapter_data["paragraphs"]):
                text, page_number = paragraph_text_and_page(paragraph_data)
                paragraph = Paragraph(
                    chapter_id=chapter.id,
                    order_index=paragraph_index,
                    text=text,
                    page_number=page_number,
                )
                session.add(paragraph)
                session.flush()
                paragraph_count += 1
                if paragraph_count == 1 or paragraph_count % 25 == 0 or paragraph_count == total_paragraphs:
                    percent = 10 + int((paragraph_count / max(1, total_paragraphs)) * 84)
                    set_process_progress(
                        book_id,
                        "processing",
                        percent,
                        current=paragraph_count,
                        total=total_paragraphs,
                        detail=f"Processing {paragraph_count}/{total_paragraphs} paragraphs",
                    )

                mention_candidates = apply_parenthetical_aliases(session, text, extract_mentions(text, aliases), aliases)
                for candidate in mention_candidates:
                    if normalize_name(candidate["raw_name"]) in ignored_names:
                        continue
                    place = candidate["place"] or resolve_place(session, candidate["raw_name"], aliases)
                    mention = PlaceMention(
                        book_id=book.id,
                        chapter_id=chapter.id,
                        paragraph_id=paragraph.id,
                        raw_name=candidate["raw_name"],
                        start_offset=candidate["start_offset"],
                        end_offset=candidate["end_offset"],
                        place_id=place.id if place else None,
                        confidence=candidate["confidence"],
                    )
                    session.add(mention)
                    mention_count += 1

        set_process_progress(
            book_id,
            "saving",
            96,
            current=paragraph_count,
            total=total_paragraphs,
            detail="Saving parsed places",
        )
        session.commit()
        set_process_progress(
            book_id,
            "done",
            100,
            current=paragraph_count,
            total=total_paragraphs,
            detail=f"Parsed {paragraph_count} paragraphs and {mention_count} mentions",
        )
        return {
            "book_id": book.id,
            "chapters": len(parsed),
            "paragraphs": paragraph_count,
            "mentions": mention_count,
        }


def read_book_metadata(file_path: str) -> dict:
    suffix = Path(file_path).suffix.lower()
    if suffix == ".epub":
        return read_epub_metadata(file_path)
    if suffix == ".pdf":
        return read_pdf_metadata(file_path)
    raise HTTPException(status_code=400, detail="Unsupported file type.")


def parse_book(file_path: str) -> list[dict]:
    suffix = Path(file_path).suffix.lower()
    if suffix == ".epub":
        return parse_epub(file_path)
    if suffix == ".pdf":
        return parse_pdf(file_path)
    raise HTTPException(status_code=400, detail="Unsupported file type.")


def paragraph_text_and_page(paragraph_data: str | dict) -> tuple[str, int | None]:
    if isinstance(paragraph_data, dict):
        return str(paragraph_data.get("text", "")), paragraph_data.get("page_number")
    return str(paragraph_data), None


def apply_parenthetical_aliases(
    session: Session,
    text: str,
    mentions: list[dict],
    aliases: AliasIndex,
) -> list[dict]:
    adjusted = list(mentions)

    for match in PARENTHETICAL_PLACE_PATTERN.finditer(text):
        raw_zh = match.group("zh")
        raw_en = normalize_parenthetical_english(match.group("en"))
        zh_start, zh_end = match.start("zh"), match.end("zh")
        en_start, en_end = match.start("en"), match.end("en")

        place = aliases.lookup(raw_en)
        if not place and len(re.findall(r"[A-Za-z\u00c0-\u024f]+", raw_en)) > 1:
            adjusted = [
                item
                for item in adjusted
                if not (item["start_offset"] >= en_start and item["end_offset"] <= en_end and item.get("place"))
            ]
        token_place = None if place else aliases.lookup_parenthetical_english_tokens(raw_en)
        matched_place = place or token_place
        if not matched_place:
            if _parenthetical_english_token_count(raw_en) > 1:
                adjusted = _remove_mentions_inside(adjusted, en_start, en_end)
            continue

        zh_mention_exists = any(
            zh_start < item["end_offset"]
            and zh_end > item["start_offset"]
            and item.get("place")
            and item["place"].id == matched_place.id
            for item in adjusted
        )

        adjusted = _remove_mentions_inside(adjusted, en_start, en_end, matched_place.id)

        if zh_mention_exists or token_place:
            continue

        if any(zh_start < item["end_offset"] and zh_end > item["start_offset"] for item in adjusted):
            continue

        adjusted.append(
            {
                "raw_name": raw_zh,
                "start_offset": zh_start,
                "end_offset": zh_end,
                "place": matched_place,
                "confidence": 0.93,
            }
        )
        upsert_alias(session, matched_place, raw_zh, "paired_parenthetical")
        aliases.add_alias(raw_zh, matched_place)
        aliases.finalize()

    return sorted(adjusted, key=lambda item: (item["start_offset"], item["end_offset"]))


def _parenthetical_english_token_count(value: str) -> int:
    return len(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", value))


def _remove_mentions_inside(mentions: list[dict], start: int, end: int, place_id: int | None = None) -> list[dict]:
    return [
        item
        for item in mentions
        if not (
            item["start_offset"] >= start
            and item["end_offset"] <= end
            and (place_id is None or (item.get("place") and item["place"].id == place_id))
        )
    ]


def _needs_parenthetical_alias_rules(text: str) -> bool:
    return bool(
        re.search(r"[\uff08(][^\uff08\uff09()]*[A-Za-z\u00c0-\u024f]", text)
        or re.search(r"[\u201c\u2018\"'][A-Za-z\u00c0-\u024f]", text)
    )


@app.get("/api/books")
def list_books(session: Session = Depends(get_session)) -> list[dict]:
    books = session.scalars(select(Book).order_by(Book.created_at.desc())).all()
    return [
        {
            "id": book.id,
            "title": book.title,
            "author": book.author,
            "created_at": book.created_at.isoformat(),
        }
        for book in books
    ]


@app.delete("/api/books/{book_id}")
def delete_book(book_id: int, session: Session = Depends(get_session)) -> dict:
    book = session.get(Book, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")

    file_path = Path(book.file_path)
    chapter_ids = select(Chapter.id).where(Chapter.book_id == book_id)
    session.execute(delete(PlaceMention).where(PlaceMention.book_id == book_id))
    session.execute(delete(CandidateResolutionLog).where(CandidateResolutionLog.book_id == book_id))
    session.execute(delete(IgnoredPlaceCandidate).where(IgnoredPlaceCandidate.book_id == book_id))
    session.execute(delete(Paragraph).where(Paragraph.chapter_id.in_(chapter_ids)))
    session.execute(delete(Chapter).where(Chapter.book_id == book_id))
    session.delete(book)
    session.commit()

    file_deleted = False
    try:
        resolved_file = file_path.resolve()
        resolved_books_dir = BOOKS_DIR.resolve()
        if resolved_file.exists() and resolved_books_dir in resolved_file.parents:
            resolved_file.unlink()
            file_deleted = True
    except OSError:
        file_deleted = False

    return {"book_id": book_id, "deleted": True, "file_deleted": file_deleted}


@app.get("/api/books/{book_id}/chapters")
def list_chapters(book_id: int, session: Session = Depends(get_session)) -> list[dict]:
    chapters = session.scalars(
        select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.order_index)
    ).all()
    return [{"id": chapter.id, "title": chapter.title, "order_index": chapter.order_index} for chapter in chapters]


@app.get("/api/chapters/{chapter_id}")
def get_chapter(chapter_id: int, session: Session = Depends(get_session)) -> dict:
    chapter = session.get(Chapter, chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found.")

    paragraphs = session.scalars(
        select(Paragraph).where(Paragraph.chapter_id == chapter_id).order_by(Paragraph.order_index)
    ).all()
    mentions = session.scalars(
        select(PlaceMention)
        .options(joinedload(PlaceMention.place))
        .where(PlaceMention.chapter_id == chapter_id)
    ).all()

    mentions_by_paragraph: dict[int, list[PlaceMention]] = {}
    for mention in mentions:
        mentions_by_paragraph.setdefault(mention.paragraph_id, []).append(mention)

    aliases_cache: AliasIndex | None = None

    def get_aliases() -> AliasIndex:
        nonlocal aliases_cache
        if aliases_cache is None:
            aliases_cache = aliases_by_place(session)
        return aliases_cache

    def paragraph_mentions(paragraph: Paragraph) -> list[dict]:
        aliases = get_aliases() if _needs_parenthetical_alias_rules(paragraph.text) else None
        return [
            {
                "id": mention.id,
                "raw_name": mention.raw_name,
                "start_offset": mention.start_offset,
                "end_offset": mention.end_offset,
                "lat": mention.place.lat if mention.place else None,
                "lng": mention.place.lng if mention.place else None,
                "place_id": mention.place_id,
                "canonical_name": mention.place.canonical_name if mention.place else None,
                "confidence": mention.confidence,
            }
            for mention in sorted(
                mentions_by_paragraph.get(paragraph.id, []),
                key=lambda item: (item.start_offset, item.end_offset),
            )
            if aliases is None
            or not should_suppress_parenthetical_token_mention(
                paragraph.text,
                mention.start_offset,
                mention.end_offset,
                aliases,
            )
        ]

    all_candidate_items: list[tuple[int, int, dict]] = []
    mention_payload_by_paragraph: dict[int, list[dict]] = {}
    ignored_names = set(
        session.scalars(
            select(IgnoredPlaceCandidate.normalized_name).where(IgnoredPlaceCandidate.book_id == chapter.book_id)
        ).all()
    )
    for paragraph in paragraphs:
        mention_payload = paragraph_mentions(paragraph)
        mention_payload_by_paragraph[paragraph.id] = mention_payload

    for paragraph in paragraphs:
        mention_payload = [
            {
                "start_offset": mention.start_offset,
                "end_offset": mention.end_offset,
            }
            for mention in mentions_by_paragraph.get(paragraph.id, [])
        ]
        candidates = [
            candidate
            for candidate in extract_place_candidates(
                paragraph.text,
                mention_payload,
                get_aliases() if _needs_parenthetical_alias_rules(paragraph.text) else None,
            )
            if normalize_name(candidate["raw_name"]) not in ignored_names
        ]
        for index, candidate in enumerate(candidates):
            candidate["id"] = f"{paragraph.id}:{candidate['start_offset']}:{candidate['end_offset']}:{index}"
            candidate["paragraph_id"] = paragraph.id
            all_candidate_items.append((paragraph.id, paragraph.order_index, candidate))

    candidates_by_paragraph = filter_repeated_place_candidates(all_candidate_items)

    return {
        "id": chapter.id,
        "title": chapter.title,
        "book_id": chapter.book_id,
        "paragraphs": [
            {
                "paragraph_id": paragraph.id,
                "text": paragraph.text,
                "mentions": mention_payload_by_paragraph.get(paragraph.id, []),
                "candidates": candidates_by_paragraph.get(paragraph.id, []),
            }
            for paragraph in paragraphs
        ],
    }


@app.get("/api/books/{book_id}/places")
def list_book_places(book_id: int, session: Session = Depends(get_session)) -> list[dict]:
    rows = session.execute(
        select(Place, func.count(PlaceMention.id))
        .join(PlaceMention, PlaceMention.place_id == Place.id)
        .where(PlaceMention.book_id == book_id)
        .group_by(Place.id)
        .order_by(func.count(PlaceMention.id).desc())
    ).all()
    return [
        {
            "place_id": place.id,
            "canonical_name": place.canonical_name,
            "lat": place.lat,
            "lng": place.lng,
            "source": place.source,
            "note": place.note,
            "count": count,
        }
        for place, count in rows
    ]


@app.post("/api/mentions/{mention_id}/correct")
def correct_mention(
    mention_id: int,
    payload: MentionCorrection,
    session: Session = Depends(get_session),
) -> dict:
    mention = session.get(PlaceMention, mention_id)
    if not mention:
        raise HTTPException(status_code=404, detail="Mention not found.")

    place = session.get(Place, payload.place_id) if payload.place_id else None
    if not place:
        if payload.lat is None or payload.lng is None:
            raise HTTPException(status_code=400, detail="Provide place_id or canonical_name with lat/lng.")
        place = Place(
            canonical_name=payload.canonical_name or mention.raw_name,
            lat=payload.lat,
            lng=payload.lng,
            source="manual",
            note=payload.note,
        )
        session.add(place)
        session.flush()

    correction = session.scalar(
        select(ManualCorrection).where(ManualCorrection.raw_name == mention.raw_name)
    )
    if correction:
        correction.corrected_place_id = place.id
    else:
        session.add(ManualCorrection(raw_name=mention.raw_name, corrected_place_id=place.id))

    if payload.save_alias:
        upsert_alias(session, place, mention.raw_name, "manual")
        if payload.canonical_name:
            upsert_alias(session, place, payload.canonical_name, "manual")

    for same_raw in session.scalars(
        select(PlaceMention).where(PlaceMention.raw_name == mention.raw_name)
    ):
        same_raw.place_id = place.id

    session.commit()
    return {
        "mention_id": mention.id,
        "raw_name": mention.raw_name,
        "place_id": place.id,
        "canonical_name": place.canonical_name,
        "lat": place.lat,
        "lng": place.lng,
    }


@app.delete("/api/mentions/{mention_id}")
def delete_mention(mention_id: int, session: Session = Depends(get_session)) -> dict:
    mention = session.get(PlaceMention, mention_id)
    if not mention:
        raise HTTPException(status_code=404, detail="Mention not found.")

    book_id = mention.book_id
    raw_name = mention.raw_name
    normalized = normalize_name(raw_name)
    deleted = session.execute(
        delete(PlaceMention).where(
            PlaceMention.book_id == book_id,
            PlaceMention.raw_name == raw_name,
        )
    ).rowcount

    existing = session.scalar(
        select(IgnoredPlaceCandidate).where(
            IgnoredPlaceCandidate.book_id == book_id,
            IgnoredPlaceCandidate.normalized_name == normalized,
        )
    )
    if not existing:
        session.add(IgnoredPlaceCandidate(book_id=book_id, raw_name=raw_name, normalized_name=normalized))

    session.commit()
    return {"mention_id": mention_id, "raw_name": raw_name, "deleted": deleted or 0, "ignored": True}


def sentence_for_offsets(text: str, start_offset: int, end_offset: int) -> str:
    left = max(text.rfind(mark, 0, start_offset) for mark in ["。", "！", "？", ";", "\n"])
    right_positions = [text.find(mark, end_offset) for mark in ["。", "！", "？", ";", "\n"]]
    right_candidates = [position for position in right_positions if position != -1]
    right = min(right_candidates) + 1 if right_candidates else len(text)
    return text[left + 1 : right].strip()


def validate_candidate(payload: CandidateAction, session: Session) -> tuple[Paragraph, Chapter, Book]:
    paragraph = session.get(Paragraph, payload.paragraph_id)
    if not paragraph:
        raise HTTPException(status_code=404, detail="Paragraph not found.")

    chapter = session.get(Chapter, paragraph.chapter_id)
    if not chapter or chapter.book_id != payload.book_id:
        raise HTTPException(status_code=400, detail="Candidate does not belong to this book.")

    raw_from_text = paragraph.text[payload.start_offset : payload.end_offset]
    if raw_from_text != payload.raw_name:
        raise HTTPException(status_code=400, detail="Candidate offsets no longer match paragraph text.")
    book = session.get(Book, payload.book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")
    return paragraph, chapter, book


def place_from_llm_result(session: Session, raw_name: str, result: dict) -> Place | None:
    names = [raw_name, result.get("canonical_name") or "", *(result.get("aliases") or [])]
    normalized_names = [normalize_name(name) for name in names if str(name).strip()]
    for normalized in normalized_names:
        alias = session.scalar(select(PlaceAlias).where(PlaceAlias.normalized_name == normalized))
        if alias and alias.place:
            return alias.place
        place = session.scalar(select(Place).where(func.lower(Place.canonical_name) == normalized))
        if place:
            return place

    lat = result.get("llm_lat")
    lng = result.get("llm_lng")
    try:
        lat_value = float(lat)
        lng_value = float(lng)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat_value <= 90 and -180 <= lng_value <= 180):
        return None

    canonical_name = (result.get("canonical_name") or raw_name).strip()
    place = session.scalar(select(Place).where(Place.canonical_name == canonical_name))
    if not place:
        place = Place(
            canonical_name=canonical_name,
            lat=lat_value,
            lng=lng_value,
            source="llm_suggested",
            note=result.get("reason") or "Resolved from DeepSeek candidate search.",
            category=result.get("place_type") or "",
            region=result.get("country_or_region") or "",
        )
        session.add(place)
        session.flush()
    return place


def sentences_containing_candidate(session: Session, book_id: int, raw_name: str, limit: int = 12) -> list[str]:
    chapter_ids = select(Chapter.id).where(Chapter.book_id == book_id)
    paragraphs = session.scalars(
        select(Paragraph).where(Paragraph.chapter_id.in_(chapter_ids)).order_by(Paragraph.chapter_id, Paragraph.order_index)
    ).all()
    sentences: list[str] = []
    for paragraph in paragraphs:
        for match in re.finditer(re.escape(raw_name), paragraph.text):
            sentence = sentence_for_offsets(paragraph.text, match.start(), match.end())
            if sentence and sentence not in sentences:
                sentences.append(sentence)
            break
        if len(sentences) >= limit:
            break
    return sentences


def apply_candidate_place(session: Session, book_id: int, raw_name: str, place: Place) -> int:
    upsert_alias(session, place, raw_name, "llm_candidate")
    upsert_alias(session, place, place.canonical_name, "llm_candidate")
    correction = session.scalar(select(ManualCorrection).where(ManualCorrection.raw_name == raw_name))
    if correction:
        correction.corrected_place_id = place.id
    else:
        session.add(ManualCorrection(raw_name=raw_name, corrected_place_id=place.id))

    chapter_ids = select(Chapter.id).where(Chapter.book_id == book_id)
    paragraphs = session.scalars(select(Paragraph).where(Paragraph.chapter_id.in_(chapter_ids))).all()
    mention_count = 0
    for current_paragraph in paragraphs:
        existing_mentions = session.scalars(
            select(PlaceMention).where(PlaceMention.paragraph_id == current_paragraph.id)
        ).all()
        occupied = [(mention.start_offset, mention.end_offset) for mention in existing_mentions]
        for match in re.finditer(re.escape(raw_name), current_paragraph.text):
            start, end = match.start(), match.end()
            if any(start < used_end and end > used_start for used_start, used_end in occupied):
                continue
            session.add(
                PlaceMention(
                    book_id=book_id,
                    chapter_id=current_paragraph.chapter_id,
                    paragraph_id=current_paragraph.id,
                    raw_name=raw_name,
                    start_offset=start,
                    end_offset=end,
                    place_id=place.id,
                    confidence=0.88,
                )
            )
            occupied.append((start, end))
            mention_count += 1
    return mention_count


@app.post("/api/place-candidates/ignore")
def ignore_place_candidate(payload: CandidateIgnore, session: Session = Depends(get_session)) -> dict:
    book = session.get(Book, payload.book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found.")

    normalized = normalize_name(payload.raw_name)
    existing = session.scalar(
        select(IgnoredPlaceCandidate).where(
            IgnoredPlaceCandidate.book_id == payload.book_id,
            IgnoredPlaceCandidate.normalized_name == normalized,
        )
    )
    if not existing:
        session.add(
            IgnoredPlaceCandidate(book_id=payload.book_id, raw_name=payload.raw_name, normalized_name=normalized)
        )
    session.commit()
    return {"raw_name": payload.raw_name, "ignored": True}


@app.post("/api/place-candidates/search")
def search_place_candidate(payload: CandidateAction, session: Session = Depends(get_session)) -> dict:
    paragraph, _chapter, book = validate_candidate(payload, session)
    sentence = sentence_for_offsets(paragraph.text, payload.start_offset, payload.end_offset)

    try:
        result = classify_place_candidate(book_title=book.title, raw_name=payload.raw_name, sentence=sentence)
        if result.get("needs_more_context") or result.get("status") == "ambiguous":
            result = classify_place_candidate(
                book_title=book.title,
                raw_name=payload.raw_name,
                sentence=sentence,
                paragraph=paragraph.text,
                all_sentences=sentences_containing_candidate(session, payload.book_id, payload.raw_name),
            )
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"DeepSeek candidate search failed: {error}") from error

    session.add(
        CandidateResolutionLog(
            book_id=payload.book_id,
            raw_name=payload.raw_name,
            context_level=result.get("_context_level", "sentence"),
            request_context=result.get("_request_context", ""),
            response_json=json.dumps(result, ensure_ascii=False),
        )
    )

    status = result.get("status")
    place = place_from_llm_result(session, payload.raw_name, result) if status == "resolved" else None
    mentions_created = 0
    if place:
        mentions_created = apply_candidate_place(session, payload.book_id, payload.raw_name, place)
        ignored = session.scalar(
            select(IgnoredPlaceCandidate).where(
                IgnoredPlaceCandidate.book_id == payload.book_id,
                IgnoredPlaceCandidate.normalized_name == normalize_name(payload.raw_name),
            )
        )
        if ignored:
            session.delete(ignored)

    session.commit()
    return {
        "raw_name": payload.raw_name,
        "status": "resolved" if place else status or "ambiguous",
        "place": {
            "place_id": place.id,
            "canonical_name": place.canonical_name,
            "lat": place.lat,
            "lng": place.lng,
            "source": place.source,
        }
        if place
        else None,
        "mentions_created": mentions_created,
        "llm_result": {key: value for key, value in result.items() if not key.startswith("_")},
        "context_level": result.get("_context_level", "sentence"),
    }
