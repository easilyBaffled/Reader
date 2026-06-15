"""SQLite connection and numbered-migration runner.

Migrations live in audibleweb/migrations/ as NNN_description.sql files
and are applied in order, tracked via PRAGMA user_version.
"""

import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> int:
    """Apply pending migrations newer than PRAGMA user_version. Returns the resulting version."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]

    for path in sorted(migrations_dir.glob("*.sql")):
        version = int(path.name.split("_", 1)[0])
        if version <= current:
            continue
        conn.executescript(path.read_text())
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
        current = version

    return current
