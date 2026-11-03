import json
import math
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, directory):
        self.path = Path(directory) / "analytics.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS usage (
                    path TEXT PRIMARY KEY, last_used TEXT NOT NULL,
                    last_loaded TEXT, last_referenced TEXT, use_count INTEGER NOT NULL,
                    signature TEXT
                );
                CREATE TABLE IF NOT EXISTS moves (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, destination TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL, created_at TEXT NOT NULL,
                    status TEXT NOT NULL, error TEXT, fingerprint TEXT
                );
                CREATE TABLE IF NOT EXISTS protected_models (path TEXT PRIMARY KEY, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS file_hashes (
                    path TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trash_locations (path TEXT PRIMARY KEY);
            """)
            # Keep databases from development/earlier releases readable.
            if "signature" not in {row["name"] for row in db.execute("PRAGMA table_info(usage)")}:
                db.execute("ALTER TABLE usage ADD COLUMN signature TEXT")
            if "fingerprint" not in {row["name"] for row in db.execute("PRAGMA table_info(moves)")}:
                db.execute("ALTER TABLE moves ADD COLUMN fingerprint TEXT")
            db.execute("INSERT OR IGNORE INTO settings VALUES ('tracking_since', ?)", (json.dumps(now()),))
            previous = db.execute("SELECT value FROM settings WHERE key='trash_directory'").fetchone()
            if previous and json.loads(previous["value"]):
                db.execute(
                    "INSERT OR IGNORE INTO trash_locations VALUES (?)",
                    (str(Path(json.loads(previous["value"])) / "trash"),),
                )
            # Preserve restore locations when upgrading an existing installation.
            for row in db.execute("SELECT destination FROM moves").fetchall():
                destination = Path(row["destination"])
                for parent in destination.parents:
                    if parent.name.lower() == "trash" and destination.relative_to(parent).parts[0] in {
                        "models",
                        "collisions",
                    }:
                        db.execute("INSERT OR IGNORE INTO trash_locations VALUES (?)", (str(parent),))
                        break

    @contextmanager
    def connection(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=30)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()

    def settings(self):
        values = {"trash_directory": "", "stale_days": 30}
        with self.connection() as db:
            values.update({row["key"]: json.loads(row["value"]) for row in db.execute("SELECT * FROM settings")})
        return values

    def configure(self, trash_directory, stale_days):
        if (
            isinstance(stale_days, bool)
            or not isinstance(stale_days, (int, float))
            or not math.isfinite(stale_days)
            or not 0 < stale_days <= 36500
        ):
            raise ValueError("The recency window must be between 0 and 36500 days.")
        with self.connection() as db:
            db.executemany(
                "INSERT OR REPLACE INTO settings VALUES (?, ?)",
                [
                    ("trash_directory", json.dumps(trash_directory)),
                    ("stale_days", json.dumps(stale_days)),
                ],
            )
            if trash_directory:
                db.execute("INSERT OR IGNORE INTO trash_locations VALUES (?)", (str(Path(trash_directory) / "trash"),))
        return self.settings()

    def protected_paths(self):
        with self.connection() as db:
            return {row["path"] for row in db.execute("SELECT path FROM protected_models")}

    def protect(self, path, protected):
        with self.connection() as db:
            if protected:
                db.execute("INSERT OR IGNORE INTO protected_models VALUES (?, ?)", (path, now()))
            else:
                db.execute("DELETE FROM protected_models WHERE path=?", (path,))

    def hashes(self):
        with self.connection() as db:
            return {
                row["path"]: {"fingerprint": json.loads(row["fingerprint"]), "sha256": row["sha256"]}
                for row in db.execute("SELECT * FROM file_hashes")
            }

    def save_hash(self, path, signature, sha256):
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO file_hashes VALUES (?, ?, ?)", (path, json.dumps(signature), sha256))

    def trash_locations(self):
        with self.connection() as db:
            return [row["path"] for row in db.execute("SELECT path FROM trash_locations ORDER BY path")]

    def record(self, paths, source):
        timestamp = now()
        column = {"load": "last_loaded", "workflow": "last_referenced"}[source]
        with self.connection() as db:
            for path in set(paths):
                try:
                    info = Path(path).stat()
                except OSError:
                    continue
                signature = json.dumps([info.st_size, info.st_mtime_ns])
                previous = db.execute("SELECT signature FROM usage WHERE path=?", (path,)).fetchone()
                if previous and previous["signature"] != signature:
                    db.execute("DELETE FROM usage WHERE path=?", (path,))
                db.execute(
                    f"""
                    INSERT INTO usage (path, last_used, {column}, use_count, signature) VALUES (?, ?, ?, 1, ?)
                    ON CONFLICT(path) DO UPDATE SET last_used=excluded.last_used,
                    {column}=excluded.{column}, use_count=usage.use_count+1
                """,
                    (path, timestamp, timestamp, signature),
                )

    def history(self):
        with self.connection() as db:
            return {
                row["path"]: {**dict(row), "signature": json.loads(row["signature"] or "null")}
                for row in db.execute("SELECT * FROM usage")
            }

    def journal(self, move_id, source, destination, size):
        with self.connection() as db:
            db.execute(
                "INSERT INTO moves (id, source, destination, size_bytes, created_at, status) VALUES (?, ?, ?, ?, ?, 'pending')",
                (move_id, str(source), str(destination), size, now()),
            )

    def update_move(self, move_id, status, error=None, fingerprint=None):
        with self.connection() as db:
            db.execute(
                "UPDATE moves SET status=?, error=?, fingerprint=COALESCE(?, fingerprint) WHERE id=?",
                (status, error, json.dumps(fingerprint) if fingerprint else None, move_id),
            )

    def moves(self):
        with self.connection() as db:
            return [
                {**dict(row), "fingerprint": json.loads(row["fingerprint"] or "null")}
                for row in db.execute("SELECT * FROM moves ORDER BY created_at DESC")
            ]
