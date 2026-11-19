import sqlite3
from contextlib import closing
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = PROJECT_ROOT / "careermate.sqlite3"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db() -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    with closing(connect_db()) as connection:
        connection.executescript(schema)


if __name__ == "__main__":
    init_db()
    print(f"SQLite 초기화 완료: {DB_PATH}")