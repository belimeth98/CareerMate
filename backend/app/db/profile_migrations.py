"""Additive, lazy migrations; existing profile and experience rows are retained."""


def ensure_profile_extensions(db):
    additions = (("user_profile", "profile_name", "TEXT"),
                 ("experience", "skills", "TEXT NOT NULL DEFAULT '[]'"))
    pending = [(table, column, spec) for table, column, spec in additions
               if column not in {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
               and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()]
    if not pending:
        return
    db.execute("BEGIN IMMEDIATE")
    try:
        for table, column, spec in pending:
            if column not in {row[1] for row in db.execute(f"PRAGMA table_info({table})")}:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")
        db.commit()
    except Exception:
        db.rollback()
        raise
