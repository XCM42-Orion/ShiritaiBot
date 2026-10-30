-- Long-term memory (docs/MEMORY.md §2). Times are UTC epoch seconds.
CREATE TABLE IF NOT EXISTS mem_entity (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,                 -- person | thing
  name TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL DEFAULT '',      -- owner: the fixed entity for the owner
  status TEXT NOT NULL,               -- candidate | confirmed | archived
  merged_into INTEGER,
  seen_count INTEGER NOT NULL DEFAULT 0,
  last_seen REAL,
  created REAL NOT NULL, updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS mem_alias (
  alias TEXT NOT NULL, entity INTEGER NOT NULL,
  source TEXT NOT NULL,               -- owner | agent | extracted | identity
  first_seen REAL NOT NULL, last_seen REAL NOT NULL,
  PRIMARY KEY(alias, entity)
);
CREATE INDEX IF NOT EXISTS mem_alias_entity ON mem_alias(entity);
CREATE TABLE IF NOT EXISTS mem_identity (
  identity TEXT PRIMARY KEY,          -- qq:<id> | email:<address>; written by code only
  entity INTEGER NOT NULL, display TEXT NOT NULL DEFAULT '',
  first_seen REAL NOT NULL, last_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS mem_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  subject INTEGER,                    -- mem_entity id; NULL for general knowledge
  content TEXT NOT NULL,
  valid_from REAL, valid_to REAL,
  as_of REAL NOT NULL,                -- last confirmed by evidence
  authority TEXT NOT NULL,            -- owner > direct > derived > agent
  confidence REAL NOT NULL,
  status TEXT NOT NULL,               -- active | conflicted | superseded | archived
  superseded_by INTEGER,
  created REAL NOT NULL, updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS mem_fact_subject ON mem_fact(subject, status);
CREATE TABLE IF NOT EXISTS mem_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  content TEXT NOT NULL,
  occurred_at REAL,                   -- NULL when only fuzzy wording is known
  tz TEXT NOT NULL,
  granularity TEXT NOT NULL,          -- minute | day | month | year | fuzzy
  time_text TEXT NOT NULL DEFAULT '',
  phase TEXT NOT NULL,                -- happened | planned | cancelled
  authority TEXT NOT NULL,
  status TEXT NOT NULL,               -- active | superseded | archived
  superseded_by INTEGER,
  created REAL NOT NULL, updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS mem_event_time ON mem_event(status, occurred_at);
CREATE TABLE IF NOT EXISTS mem_participant (
  event INTEGER NOT NULL, entity INTEGER NOT NULL, PRIMARY KEY(event, entity)
);
CREATE INDEX IF NOT EXISTS mem_participant_entity ON mem_participant(entity);
CREATE TABLE IF NOT EXISTS mem_evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  target TEXT NOT NULL,               -- f<id> | v<id>
  source TEXT NOT NULL,               -- owner:<turn> | tool:<turn> | event:<id> | legacy:<id>
  quote TEXT NOT NULL,
  observed_at REAL NOT NULL,
  verdict TEXT NOT NULL               -- quoted (code-checked) | verified | unverified
);
CREATE INDEX IF NOT EXISTS mem_evidence_target ON mem_evidence(target);
CREATE TABLE IF NOT EXISTS mem_conflict (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,                 -- fact | identity
  items TEXT NOT NULL,                -- JSON list of refs
  note TEXT NOT NULL,
  status TEXT NOT NULL,               -- open | resolved
  asked_at REAL,
  resolution TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL, resolved REAL
);
CREATE TABLE IF NOT EXISTS mem_tombstone (
  hash TEXT PRIMARY KEY, target TEXT NOT NULL, reason TEXT NOT NULL, created REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5(id UNINDEXED, tokens);
CREATE TABLE IF NOT EXISTS mem_vector (
  id TEXT NOT NULL, model TEXT NOT NULL, hash TEXT NOT NULL, vec BLOB NOT NULL,
  PRIMARY KEY(id, model)
);
