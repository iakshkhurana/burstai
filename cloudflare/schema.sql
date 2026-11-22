-- Reference schema for the BurstAI Worker's D1 database.
-- You do not need to run this: the Worker creates these tables itself on first use (ensureSchema).
CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, pw_hash TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS scans (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, report_id TEXT, title TEXT, words INTEGER, similarity REAL, ai REAL, grammar INTEGER, report TEXT, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS scans_user ON scans(user_id, created DESC);
-- engine-signed verification copies (scores + sources + text fingerprint; never essay text)
CREATE TABLE IF NOT EXISTS certs (id TEXT PRIMARY KEY, payload TEXT NOT NULL, sig TEXT NOT NULL, title TEXT, saved_at REAL NOT NULL);
-- fixed-window rate limits keyed by a salted hash of the IP (rows live at most an hour)
CREATE TABLE IF NOT EXISTS rl (k TEXT PRIMARY KEY, n INTEGER NOT NULL, reset REAL NOT NULL);
-- small key/value store (current engine address)
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL, updated REAL NOT NULL);
