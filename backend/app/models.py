from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Book(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String, default="Untitled")
    author: Mapped[str] = mapped_column(String, default="Unknown")
    file_path: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    chapters: Mapped[list["Chapter"]] = relationship(cascade="all, delete-orphan")


class Chapter(Base):
    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), index=True)
    title: Mapped[str] = mapped_column(String, default="")
    order_index: Mapped[int] = mapped_column(Integer)

    paragraphs: Mapped[list["Paragraph"]] = relationship(cascade="all, delete-orphan")


class Paragraph(Base):
    __tablename__ = "paragraphs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id"), index=True)
    order_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Place(Base):
    __tablename__ = "places"
    __table_args__ = (UniqueConstraint("canonical_name", name="uq_places_canonical_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String, index=True)
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String, default="gazetteer")
    note: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String, default="")
    period: Mapped[str] = mapped_column(String, default="")
    region: Mapped[str] = mapped_column(String, default="")
    context_keywords: Mapped[str] = mapped_column(Text, default="")

    aliases: Mapped[list["PlaceAlias"]] = relationship(cascade="all, delete-orphan", back_populates="place")


class PlaceAlias(Base):
    __tablename__ = "place_aliases"
    __table_args__ = (UniqueConstraint("normalized_name", name="uq_place_aliases_normalized_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    place_id: Mapped[int] = mapped_column(ForeignKey("places.id"), index=True)
    alias: Mapped[str] = mapped_column(String, index=True)
    normalized_name: Mapped[str] = mapped_column(String, index=True)
    language: Mapped[str] = mapped_column(String, default="")
    source: Mapped[str] = mapped_column(String, default="gazetteer")

    place: Mapped[Place] = relationship(back_populates="aliases")


class PlaceMention(Base):
    __tablename__ = "place_mentions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), index=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id"), index=True)
    paragraph_id: Mapped[int] = mapped_column(ForeignKey("paragraphs.id"), index=True)
    raw_name: Mapped[str] = mapped_column(String, index=True)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    place_id: Mapped[int | None] = mapped_column(ForeignKey("places.id"), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.8)

    place: Mapped[Place | None] = relationship()


class ManualCorrection(Base):
    __tablename__ = "manual_corrections"
    __table_args__ = (UniqueConstraint("raw_name", name="uq_manual_corrections_raw_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_name: Mapped[str] = mapped_column(String, index=True)
    corrected_place_id: Mapped[int] = mapped_column(ForeignKey("places.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    place: Mapped[Place] = relationship()


class IgnoredPlaceCandidate(Base):
    __tablename__ = "ignored_place_candidates"
    __table_args__ = (UniqueConstraint("book_id", "normalized_name", name="uq_ignored_candidate_book_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), index=True)
    raw_name: Mapped[str] = mapped_column(String, index=True)
    normalized_name: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CandidateResolutionLog(Base):
    __tablename__ = "candidate_resolution_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id"), index=True)
    raw_name: Mapped[str] = mapped_column(String, index=True)
    context_level: Mapped[str] = mapped_column(String, default="sentence")
    request_context: Mapped[str] = mapped_column(Text, default="")
    response_json: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
