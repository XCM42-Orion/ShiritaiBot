-- Meme library embeddings, kept apart from memory vectors (docs/MEMES.md).
CREATE TABLE IF NOT EXISTS meme_vector (
  id TEXT NOT NULL, model TEXT NOT NULL, hash TEXT NOT NULL, vec BLOB NOT NULL,
  PRIMARY KEY(id, model)
);
