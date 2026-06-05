import re
from pathlib import Path

from bs4 import BeautifulSoup
from ebooklib import epub
from ebooklib.epub import EpubHtml


def _first_metadata_value(book: epub.EpubBook, namespace: str, name: str, fallback: str) -> str:
    values = book.get_metadata(namespace, name)
    if not values:
        return fallback
    return str(values[0][0]).strip() or fallback


def read_epub_metadata(file_path: str) -> dict:
    book = epub.read_epub(file_path)
    return {
        "title": _first_metadata_value(book, "DC", "title", Path(file_path).stem),
        "author": _first_metadata_value(book, "DC", "creator", "Unknown"),
    }


def parse_epub(file_path: str) -> list[dict]:
    book = epub.read_epub(file_path)
    chapters: list[dict] = []

    for item in book.get_items():
        if not isinstance(item, EpubHtml):
            continue
        item_name = item.get_name().lower()
        item_id = item.get_id().lower()
        if item_id in {"nav", "ncx"} or item_name.endswith("nav.xhtml") or item_name.endswith("nav.html"):
            continue

        soup = BeautifulSoup(item.get_content(), "html.parser")
        title_tag = soup.find(["h1", "h2", "h3", "title"])
        title = title_tag.get_text(" ", strip=True) if title_tag else item.get_name()

        paragraphs = []
        for node in soup.find_all(["p", "li"]):
            text = re.sub(r"\s+", " ", node.get_text(" ", strip=True))
            if len(text) >= 2:
                paragraphs.append(text)

        if paragraphs:
            chapters.append({"title": title, "paragraphs": paragraphs})

    return chapters
