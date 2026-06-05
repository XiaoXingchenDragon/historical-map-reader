from pathlib import Path

from sqlalchemy import event, inspect, text
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
BOOKS_DIR = DATA_DIR / "books"
DB_PATH = DATA_DIR / "reader.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
BOOKS_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 30},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@event.listens_for(engine, "connect")
def configure_sqlite(connection, _record):
    cursor = connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


class Base(DeclarativeBase):
    pass


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db():
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    ensure_schema()


def ensure_schema() -> None:
    inspector = inspect(engine)
    if "places" not in inspector.get_table_names():
        return

    place_columns = {column["name"] for column in inspector.get_columns("places")}
    additions = {
        "category": "TEXT DEFAULT ''",
        "period": "TEXT DEFAULT ''",
        "region": "TEXT DEFAULT ''",
        "context_keywords": "TEXT DEFAULT ''",
    }
    with engine.begin() as connection:
        for column_name, column_type in additions.items():
            if column_name not in place_columns:
                connection.execute(text(f"ALTER TABLE places ADD COLUMN {column_name} {column_type}"))

        if "paragraphs" in inspector.get_table_names():
            paragraph_columns = {column["name"] for column in inspector.get_columns("paragraphs")}
            if "page_number" not in paragraph_columns:
                connection.execute(text("ALTER TABLE paragraphs ADD COLUMN page_number INTEGER"))
