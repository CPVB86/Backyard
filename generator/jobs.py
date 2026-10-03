"""Durable, species-deduplicated scheduling within Generator storage."""
from contextlib import contextmanager
import sqlite3
import time


class Jobs:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=0.2)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                domain TEXT NOT NULL, scientific_name TEXT NOT NULL,
                common_name TEXT NOT NULL, status TEXT NOT NULL,
                reason TEXT, created REAL NOT NULL,
                PRIMARY KEY (domain, scientific_name))""")

    def get(self, domain, name):
        if not self.path.exists():
            return None
        with self.connect() as db:
            row = db.execute("SELECT status, reason FROM jobs WHERE domain=? AND scientific_name=?",
                             (domain, name)).fetchone()
        return dict(row) if row else None

    def enqueue(self, domain, name, common):
        with self.connect() as db:
            db.execute("INSERT INTO jobs VALUES (?, ?, ?, 'generation_pending', NULL, ?) "
                       "ON CONFLICT(domain, scientific_name) DO UPDATE SET status='generation_pending', reason=NULL "
                       "WHERE jobs.status='complete'",
                       (domain, name, common, time.time()))

    def set(self, domain, name, status, reason=None):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status=?, reason=? WHERE domain=? AND scientific_name=?",
                       (status, reason, domain, name))

    def recover(self):
        # A killed process may already have incurred a paid request. Never retry it.
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='generation_failed', reason='interrupted_check_provider_usage' "
                       "WHERE status='generation_running'")
            # Credentials/adapters may have been configured since the previous start.
            db.execute("UPDATE jobs SET status='generation_pending', reason=NULL "
                       "WHERE status='generation_not_configured' AND reason != 'provider_credentials_rejected'")

    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT domain, scientific_name, common_name FROM jobs "
                             "WHERE status='generation_pending' ORDER BY created LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE jobs SET status='generation_running', reason=NULL "
                           "WHERE domain=? AND scientific_name=?", (row['domain'], row['scientific_name']))
        return dict(row) if row else None
