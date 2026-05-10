import sqlite3
import json
from pathlib import Path
from datetime import datetime


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS files (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                path            TEXT UNIQUE NOT NULL,
                file_type       TEXT NOT NULL,
                filename        TEXT NOT NULL,
                size_bytes      INTEGER,
                analyzed_at     TEXT,

                blur_score      REAL,
                is_blurry       INTEGER DEFAULT 0,
                phash           TEXT,

                category        TEXT,
                subject         TEXT,
                quality         TEXT,
                issues          TEXT,
                action          TEXT,
                confidence      INTEGER,
                reason          TEXT,

                duplicate_group TEXT,
                moved_to        TEXT,
                status          TEXT DEFAULT 'pending'
            );

            CREATE INDEX IF NOT EXISTS idx_action ON files(action);
            CREATE INDEX IF NOT EXISTS idx_phash  ON files(phash);
            CREATE INDEX IF NOT EXISTS idx_status ON files(status);
        """)
        self.conn.commit()

    def reset(self):
        self.conn.execute("DROP TABLE IF EXISTS files")
        self.conn.commit()
        self._create_tables()

    def is_analyzed(self, path: str) -> bool:
        row = self.conn.execute(
            "SELECT id FROM files WHERE path = ? AND status = 'analyzed'", (path,)
        ).fetchone()
        return row is not None

    def save_result(self, file_path: Path, result: dict):
        stat = file_path.stat()
        self.conn.execute("""
            INSERT OR REPLACE INTO files
              (path, file_type, filename, size_bytes, analyzed_at,
               blur_score, is_blurry, phash,
               category, subject, quality, issues, action, confidence, reason, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'analyzed')
        """, (
            str(file_path),
            result.get('file_type', 'image'),
            file_path.name,
            stat.st_size,
            datetime.now().isoformat(),
            result.get('blur_score'),
            1 if result.get('is_blurry') else 0,
            result.get('phash'),
            result.get('category'),
            result.get('subject'),
            result.get('quality'),
            json.dumps(result.get('issues', [])),
            result.get('action', 'review'),
            result.get('confidence', 50),
            result.get('reason', ''),
        ))
        self.conn.commit()

    def get_result(self, path: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM files WHERE path = ?", (path,)).fetchone()
        return dict(row) if row else None

    def update_moved_to(self, path: str, new_path: str):
        self.conn.execute(
            "UPDATE files SET moved_to = ? WHERE path = ?", (new_path, path)
        )
        self.conn.commit()

    def update_duplicate_group(self, path: str, group_id: str):
        self.conn.execute(
            "UPDATE files SET duplicate_group = ?, action = 'probably_delete' WHERE path = ?",
            (group_id, path),
        )
        self.conn.commit()

    def get_all_phashes(self):
        rows = self.conn.execute(
            "SELECT path, phash FROM files WHERE phash IS NOT NULL"
        ).fetchall()
        return [(r['path'], r['phash']) for r in rows]

    def get_all_results(self):
        rows = self.conn.execute("SELECT * FROM files ORDER BY action, filename").fetchall()
        return [dict(r) for r in rows]

    def get_stats(self):
        stats = {}
        stats['total'] = self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        for action in ('keep', 'review', 'probably_delete'):
            stats[action] = self.conn.execute(
                "SELECT COUNT(*) FROM files WHERE action = ?", (action,)
            ).fetchone()[0]
        stats['duplicates'] = self.conn.execute(
            "SELECT COUNT(*) FROM files WHERE duplicate_group IS NOT NULL"
        ).fetchone()[0]
        stats['low_quality'] = self.conn.execute(
            "SELECT COUNT(*) FROM files WHERE quality IN ('poor', 'very_poor')"
        ).fetchone()[0]
        return stats

    def close(self):
        self.conn.close()
