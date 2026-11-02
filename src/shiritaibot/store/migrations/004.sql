-- Knowledge base (docs/KNOWLEDGE.md): flat documents and immutable text chunks.
CREATE TABLE IF NOT EXISTS kb_document (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  filename TEXT NOT NULL,
  format TEXT NOT NULL,
  bytes INTEGER NOT NULL,
  sha256 TEXT NOT NULL UNIQUE,
  chars INTEGER NOT NULL,
  chunks INTEGER NOT NULL,
  source TEXT NOT NULL,               -- web | qq | agent
  origin TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS kb_document_created ON kb_document(created);
CREATE TABLE IF NOT EXISTS kb_chunk (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document INTEGER NOT NULL,
  seq INTEGER NOT NULL,
  label TEXT NOT NULL DEFAULT '',     -- page, slide, sheet or heading path
  text TEXT NOT NULL,
  UNIQUE(document, seq)
);
-- rowid is the kb_chunk id, so deleting a document stays cheap at any library size.
CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(tokens);
CREATE TABLE IF NOT EXISTS kb_vector (
  id TEXT NOT NULL, model TEXT NOT NULL, hash TEXT NOT NULL, vec BLOB NOT NULL,
  PRIMARY KEY(id, model)
);
