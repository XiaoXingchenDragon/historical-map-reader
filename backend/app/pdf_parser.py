import re
from dataclasses import dataclass
from pathlib import Path
from statistics import median

import fitz


TITLE_PATTERNS = [
    re.compile(r"^第[一二三四五六七八九十百千万零〇\d]+[章节篇部卷]\s*.+"),
    re.compile(r"^[一二三四五六七八九十百千万零〇]+[、．.]\s*.+"),
    re.compile(r"^\d{1,2}[.．、]\s*[\u4e00-\u9fffA-Za-z].+"),
    re.compile(r"^(序|序言|前言|导言|引言|绪论|尾声|结语|后记|译后记|参考文献|索引)([:：].*)?$"),
]

END_PUNCTUATION = "。！？；：”’）】》.!?;:"


@dataclass
class PdfLine:
    text: str
    page_number: int
    x0: float
    y0: float
    x1: float
    y1: float
    font_size: float
    page_width: float
    page_height: float


@dataclass
class Heading:
    title: str
    page_number: int
    line_index: int
    score: float
    source: str


def read_pdf_metadata(file_path: str) -> dict:
    with fitz.open(file_path) as doc:
        metadata = doc.metadata or {}
        title = (metadata.get("title") or "").strip() or Path(file_path).stem
        author = (metadata.get("author") or "").strip() or "Unknown"
        return {"title": title, "author": author}


def parse_pdf(file_path: str) -> list[dict]:
    with fitz.open(file_path) as doc:
        lines = _extract_lines(doc)
        if not lines:
            return []

        lines = _drop_repeated_headers_and_footers(lines)
        body_size = _estimate_body_font_size(lines)
        outline_headings = _outline_headings(doc, lines)
        layout_headings = _layout_headings(lines, body_size) if not outline_headings else []
        headings = outline_headings or layout_headings
        paragraphs = _paragraphs_from_lines(lines, {heading.line_index for heading in headings})

    if headings:
        return _chapters_from_headings(headings, paragraphs)
    return _fallback_page_chunks(paragraphs)


def _extract_lines(doc: fitz.Document) -> list[PdfLine]:
    lines: list[PdfLine] = []
    for page_index, page in enumerate(doc):
        page_number = page_index + 1
        page_rect = page.rect
        data = page.get_text("dict", sort=True)
        for block in data.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                text = "".join(span.get("text", "") for span in spans)
                text = _clean_line_text(text)
                if not text:
                    continue
                bbox = line.get("bbox") or block.get("bbox")
                if not bbox:
                    continue
                font_size = max((float(span.get("size", 0)) for span in spans), default=0)
                lines.append(
                    PdfLine(
                        text=text,
                        page_number=page_number,
                        x0=float(bbox[0]),
                        y0=float(bbox[1]),
                        x1=float(bbox[2]),
                        y1=float(bbox[3]),
                        font_size=font_size,
                        page_width=float(page_rect.width),
                        page_height=float(page_rect.height),
                    )
                )
    return lines


def _clean_line_text(text: str) -> str:
    text = text.replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _drop_repeated_headers_and_footers(lines: list[PdfLine]) -> list[PdfLine]:
    edge_lines = [
        line
        for line in lines
        if line.y0 < line.page_height * 0.12 or line.y1 > line.page_height * 0.9
    ]
    counts: dict[str, int] = {}
    for line in edge_lines:
        normalized = _normalize_repeated_edge(line.text)
        if normalized:
            counts[normalized] = counts.get(normalized, 0) + 1

    repeated = {text for text, count in counts.items() if count >= 3}
    result: list[PdfLine] = []
    for line in lines:
        normalized = _normalize_repeated_edge(line.text)
        is_edge = line.y0 < line.page_height * 0.12 or line.y1 > line.page_height * 0.9
        if is_edge and (normalized in repeated or _looks_like_page_number(line.text)):
            continue
        result.append(line)
    return result


def _normalize_repeated_edge(text: str) -> str:
    normalized = re.sub(r"\d+", "#", text).strip()
    return normalized if len(normalized) >= 2 else ""


def _looks_like_page_number(text: str) -> bool:
    return bool(re.fullmatch(r"[-—–]?\s*\d{1,4}\s*[-—–]?", text))


def _estimate_body_font_size(lines: list[PdfLine]) -> float:
    candidates = [line.font_size for line in lines if len(line.text) >= 12 and line.font_size > 0]
    if not candidates:
        return 10.0
    return float(median(candidates))


def _outline_headings(doc: fitz.Document, lines: list[PdfLine]) -> list[Heading]:
    toc = doc.get_toc()
    if not toc:
        return []
    headings: list[Heading] = []
    for level, title, page_number in toc:
        if level > 2 or page_number < 1:
            continue
        clean_title = _clean_title(title)
        if not clean_title:
            continue
        line_index = _line_index_for_outline_title(lines, page_number, clean_title)
        headings.append(Heading(clean_title, page_number, line_index, 1.0, "outline"))
    return _dedupe_headings(headings)


def _layout_headings(lines: list[PdfLine], body_size: float) -> list[Heading]:
    headings: list[Heading] = []
    previous_line: PdfLine | None = None
    for index, line in enumerate(lines):
        score = _heading_score(line, previous_line, body_size)
        if score >= 4:
            headings.append(Heading(_clean_title(line.text), line.page_number, index, score, "layout"))
        previous_line = line
    return _dedupe_headings(headings)


def _heading_score(line: PdfLine, previous_line: PdfLine | None, body_size: float) -> float:
    text = line.text
    score = 0.0
    if any(pattern.match(text) for pattern in TITLE_PATTERNS):
        score += 3
    if line.font_size >= body_size + 1.5:
        score += 2
    if len(text) <= 28:
        score += 1
    if line.y0 < line.page_height * 0.42:
        score += 1
    center = (line.x0 + line.x1) / 2
    if abs(center - line.page_width / 2) < line.page_width * 0.18:
        score += 1
    if previous_line and previous_line.page_number == line.page_number:
        gap = line.y0 - previous_line.y1
        if gap > max(line.font_size, body_size) * 1.2:
            score += 1
    if text.endswith(tuple(END_PUNCTUATION)):
        score -= 2
    if len(text) > 45:
        score -= 3
    if _looks_like_page_number(text):
        score -= 5
    return score


def _clean_title(title: str) -> str:
    title = _clean_line_text(title)
    title = re.sub(r"\.{2,}\s*\d+$", "", title)
    return title.strip(" .·•")


def _dedupe_headings(headings: list[Heading]) -> list[Heading]:
    result: list[Heading] = []
    seen: set[tuple[str, int]] = set()
    for heading in sorted(headings, key=lambda item: (item.page_number, item.line_index)):
        key = (heading.title, heading.page_number)
        if key in seen:
            continue
        seen.add(key)
        result.append(heading)
    return result


def _first_line_index_for_page(lines: list[PdfLine], page_number: int) -> int:
    for index, line in enumerate(lines):
        if line.page_number >= page_number:
            return index
    return len(lines)


def _line_index_for_outline_title(lines: list[PdfLine], page_number: int, title: str) -> int:
    title_key = _compact_title(title)
    page_line_indexes = [index for index, line in enumerate(lines) if line.page_number == page_number]
    if not page_line_indexes:
        return _first_line_index_for_page(lines, page_number)

    for index in page_line_indexes:
        line_key = _compact_title(lines[index].text)
        if title_key and (title_key in line_key or line_key in title_key):
            return index

    title_tail = title_key[-8:]
    if title_tail:
        for index in page_line_indexes:
            if title_tail in _compact_title(lines[index].text):
                return index

    return page_line_indexes[0]


def _compact_title(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", value)


def _paragraphs_from_lines(lines: list[PdfLine], heading_indexes: set[int]) -> list[dict]:
    paragraphs: list[dict] = []
    current: list[PdfLine] = []

    def flush() -> None:
        nonlocal current
        if not current:
            return
        text = _merge_paragraph_lines(current)
        if len(text) >= 2:
            paragraphs.append({"text": text, "page_number": current[0].page_number, "line_index": lines.index(current[0])})
        current = []

    for index, line in enumerate(lines):
        if index in heading_indexes:
            flush()
            continue
        if _looks_like_page_number(line.text):
            continue
        if not current:
            current.append(line)
            continue

        previous = current[-1]
        starts_new_page = line.page_number != previous.page_number
        gap = line.y0 - previous.y1 if not starts_new_page else 999
        indent_delta = line.x0 - current[0].x0
        previous_ends_sentence = previous.text.endswith(tuple(END_PUNCTUATION))
        starts_new = starts_new_page or gap > max(previous.font_size, 10) * 1.25
        starts_new = starts_new or (previous_ends_sentence and indent_delta > max(previous.font_size, 10))

        if starts_new:
            flush()
        current.append(line)
    flush()
    return paragraphs


def _merge_paragraph_lines(lines: list[PdfLine]) -> str:
    text = lines[0].text
    for line in lines[1:]:
        if text.endswith(tuple(END_PUNCTUATION)):
            text += line.text
        else:
            text += line.text
    return re.sub(r"\s+", " ", text).strip()


def _chapters_from_headings(headings: list[Heading], paragraphs: list[dict]) -> list[dict]:
    chapters: list[dict] = []
    for index, heading in enumerate(headings):
        next_heading = headings[index + 1] if index + 1 < len(headings) else None
        chapter_paragraphs = [
            _paragraph_payload(paragraph)
            for paragraph in paragraphs
            if paragraph["line_index"] > heading.line_index
            and (next_heading is None or paragraph["line_index"] < next_heading.line_index)
        ]
        chapter_paragraphs = _drop_heading_like_first_paragraph(heading.title, chapter_paragraphs)
        if chapter_paragraphs:
            chapters.append(
                {
                    "title": heading.title,
                    "source": heading.source,
                    "confidence": heading.score,
                    "paragraphs": chapter_paragraphs,
                }
            )
    return chapters


def _drop_heading_like_first_paragraph(title: str, paragraphs: list[dict]) -> list[dict]:
    if not paragraphs:
        return paragraphs
    title_key = _compact_title(title)
    first_key = _compact_title(paragraphs[0]["text"])
    if title_key and first_key and (first_key in title_key or title_key in first_key):
        return paragraphs[1:]
    return paragraphs


def _fallback_page_chunks(paragraphs: list[dict], pages_per_chapter: int = 10) -> list[dict]:
    chapters_by_bucket: dict[int, list[dict]] = {}
    for paragraph in paragraphs:
        bucket = (max(paragraph["page_number"], 1) - 1) // pages_per_chapter
        chapters_by_bucket.setdefault(bucket, []).append(_paragraph_payload(paragraph))
    return [
        {
            "title": f"第 {bucket * pages_per_chapter + 1}-{(bucket + 1) * pages_per_chapter} 页",
            "source": "fallback",
            "confidence": 0,
            "paragraphs": chapter_paragraphs,
        }
        for bucket, chapter_paragraphs in sorted(chapters_by_bucket.items())
    ]


def _paragraph_payload(paragraph: dict) -> dict:
    return {"text": paragraph["text"], "page_number": paragraph.get("page_number")}
