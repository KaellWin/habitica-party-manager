-- Schéma exact du MVP 0.1, commit 567514d.

                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS companions(
                    name TEXT PRIMARY KEY, user_id TEXT NOT NULL, enabled INTEGER NOT NULL,
                    task_id TEXT, audit_json TEXT, prepared INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS events(
                    id TEXT PRIMARY KEY, received_at REAL NOT NULL, occurred_at REAL,
                    mode TEXT NOT NULL, reason TEXT NOT NULL, snapshot TEXT NOT NULL,
                    receipts INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS operations(
                    id INTEGER PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id),
                    companion TEXT NOT NULL, user_id TEXT NOT NULL, task_id TEXT,
                    status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    next_at REAL NOT NULL, error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    UNIQUE(event_id, companion)
                );
                CREATE INDEX IF NOT EXISTS queue_due ON operations(status, next_at);
                CREATE TABLE IF NOT EXISTS audit_log(
                    id INTEGER PRIMARY KEY, at REAL NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL
                );
                PRAGMA user_version=1;
