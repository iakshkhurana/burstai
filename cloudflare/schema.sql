-- D1 schema for the BurstAI Worker. Mirrors app/auth.py and app/reports.py.
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  pw_hash TEXT NOT NULL,
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  report_id TEXT,
  title TEXT,
  words INTEGER,
  similarity REAL,
  ai REAL,
  grammar INTEGER,
  report TEXT,
  created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS scans_user ON scans(user_id, created DESC);
CREATE TABLE IF NOT EXISTS reports (
  id TEXT PRIMARY KEY,
  report TEXT NOT NULL,
  saved_at REAL NOT NULL
);
