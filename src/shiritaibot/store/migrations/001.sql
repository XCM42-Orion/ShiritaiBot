CREATE TABLE IF NOT EXISTS records (
  kind TEXT NOT NULL, id TEXT NOT NULL, scope TEXT NOT NULL DEFAULT '',
  data TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
  PRIMARY KEY(kind,id)
);
CREATE INDEX IF NOT EXISTS records_scope ON records(kind,scope,updated);
CREATE TABLE IF NOT EXISTS messages (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT NOT NULL, turn TEXT NOT NULL,
  data TEXT NOT NULL, created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_session ON messages(session,seq);
CREATE INDEX IF NOT EXISTS messages_created ON messages(created);
CREATE INDEX IF NOT EXISTS records_created ON records(kind,created);
CREATE TABLE IF NOT EXISTS memory (
  id TEXT PRIMARY KEY, visibility TEXT NOT NULL, kind TEXT NOT NULL,
  content TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', created REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(id UNINDEXED, tokens);
CREATE TABLE IF NOT EXISTS seen_messages (id TEXT PRIMARY KEY, created REAL NOT NULL);
