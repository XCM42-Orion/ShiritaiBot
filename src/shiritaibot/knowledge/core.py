"""Knowledge base storage and hybrid search (docs/KNOWLEDGE.md).

Documents are flat and immutable: a file becomes chunks once and is only ever removed
as a whole. Search needs no text model, so it stays cheap however large the library is.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from contextlib import suppress
from pathlib import Path

from shiritaibot.core import PublicError, now
from shiritaibot.core.logs import get_logger
from shiritaibot.knowledge.extract import chunk_sections, extract
from shiritaibot.memory.longterm import keywords, tokenize_sync
from shiritaibot.memory.vectors import VectorIndex

log = get_logger("知识库")
RRF_K = 60
# How far each retriever reaches before fusion.
POOL = 50
# Below this cosine a semantic hit is noise rather than a weak match.
VECTOR_FLOOR = 0.3
EMBED_PAGE = 256
DOCUMENT_REF = re.compile(r"^d([1-9][0-9]*)$")
REF_PATTERN = DOCUMENT_REF.pattern
CJK_RUN = re.compile(r"[\u3400-\u9fff]{2,}")


def document_key(ref: str) -> int:
    match = DOCUMENT_REF.fullmatch(ref)
    if not match:
        raise PublicError("无效的文档编号")
    return int(match[1])


def public(row: dict) -> dict:
    return {**row, "id": f"d{row['id']}"}


def searchable(title: str, label: str, text: str) -> str:
    """What FTS and embeddings see: context a chunk alone would lack."""
    return "\n".join(part for part in (title, label, text) if part)


def bigrams(text: str) -> list[str]:
    """Overlapping CJK pairs: jieba cuts words it does not know ("年假" -> "年", "假") and
    may cut them differently in a question and a document; pairs always meet."""
    return [run[i : i + 2] for run in CJK_RUN.findall(text) for i in range(len(run) - 1)]


def index_tokens(text: str) -> str:
    return " ".join([*tokenize_sync(text), *bigrams(text)])


def clean_filename(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name
    return re.sub(r"[\x00-\x1f\x7f]", "", name)[:200]


class KnowledgeBase:
    def __init__(self, store, config, llm):
        self.store, self.config, self.llm = store, config, llm
        self.folder = store.home / "knowledge"
        self.vectors = VectorIndex(store, llm, "kb_vector")
        self.lock = asyncio.Lock()
        self.wake = asyncio.Event()

    async def open(self) -> None:
        await asyncio.to_thread(self.folder.mkdir, parents=True, exist_ok=True)

    # ---- writes --------------------------------------------------------------------

    async def add(
        self, data: bytes, filename: str, *, title: str = "", source: str, origin: str = ""
    ) -> dict:
        """Store one file; identical content returns the existing document."""
        settings = self.config.value.knowledge
        if len(data) > settings.max_file_mb * 1024 * 1024:
            raise PublicError(f"文件超过 {settings.max_file_mb} MiB 上限")
        if not data:
            raise PublicError("文件是空的")
        filename = clean_filename(filename)
        title = " ".join(title.split())[:200] or Path(filename).stem or filename
        digest = hashlib.sha256(data).hexdigest()
        if existing := await self.by_hash(digest):
            return {**existing, "duplicate": True}

        def prepare():
            name, sections = extract(data, filename)
            chunks = chunk_sections(sections, settings.chunk_chars, settings.chunk_overlap)
            tokens = [index_tokens(searchable(title, label, text)) for label, text in chunks]
            return name, chunks, tokens

        name, chunks, tokens = await asyncio.to_thread(prepare)
        if not chunks:
            raise PublicError("文件里没有可提取的文本")
        path = self.folder / digest
        async with self.lock:
            if existing := await self.by_hash(digest):
                return {**existing, "duplicate": True}
            await asyncio.to_thread(path.write_bytes, data)
            try:
                key = await self.insert(
                    (
                        title,
                        filename,
                        name,
                        len(data),
                        digest,
                        sum(len(text) for _, text in chunks),
                        len(chunks),
                        source,
                        origin,
                        now().timestamp(),
                    ),
                    chunks,
                    tokens,
                )
            except BaseException:
                await asyncio.to_thread(path.unlink, missing_ok=True)
                raise
        self.wake.set()
        log.log(f"已收录《{title}》：{len(chunks)} 个分块")
        document = await self.get(f"d{key}")
        assert document is not None
        return {**document, "duplicate": False}

    async def insert(self, document: tuple, chunks: list, tokens: list) -> int:
        """One transaction, so a crash never leaves a half-imported document."""
        db = self.store.db
        async with self.store.lock:
            try:
                cursor = await db.execute(
                    "INSERT INTO kb_document(title,filename,format,bytes,sha256,chars,chunks,"
                    "source,origin,created) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    document,
                )
                key = cursor.lastrowid
                # Ids are assigned here so that thousands of rows fit one executemany;
                # sqlite_sequence keeps them from reusing ids of deleted chunks.
                async with db.execute(
                    "SELECT seq FROM sqlite_sequence WHERE name='kb_chunk'"
                ) as found:
                    row = await found.fetchone()
                base = row[0] if row else 0
                await db.executemany(
                    "INSERT INTO kb_chunk(id,document,seq,label,text) VALUES(?,?,?,?,?)",
                    [
                        (base + seq + 1, key, seq, label, text)
                        for seq, (label, text) in enumerate(chunks)
                    ],
                )
                await db.executemany(
                    "INSERT INTO kb_fts(rowid,tokens) VALUES(?,?)",
                    [(base + seq + 1, value) for seq, value in enumerate(tokens)],
                )
                await db.commit()
                assert key is not None
                return key
            except BaseException:
                await db.rollback()
                raise

    async def delete(self, ref: str) -> None:
        key = document_key(ref)
        rows = await self.store.query("SELECT sha256 FROM kb_document WHERE id=?", (key,))
        if not rows:
            raise PublicError("文档不存在")
        db = self.store.db
        async with self.lock, self.store.lock:
            try:
                for sql in (
                    "DELETE FROM kb_fts WHERE rowid IN (SELECT id FROM kb_chunk WHERE document=?)",
                    "DELETE FROM kb_vector WHERE id IN "
                    "(SELECT CAST(id AS TEXT) FROM kb_chunk WHERE document=?)",
                    "DELETE FROM kb_chunk WHERE document=?",
                    "DELETE FROM kb_document WHERE id=?",
                ):
                    await db.execute(sql, (key,))
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
        self.vectors.cache.clear()
        await asyncio.to_thread((self.folder / rows[0]["sha256"]).unlink, missing_ok=True)
        log.log(f"已删除文档 {ref}")

    # ---- reads ---------------------------------------------------------------------

    async def by_hash(self, digest: str) -> dict | None:
        rows = await self.store.query("SELECT * FROM kb_document WHERE sha256=?", (digest,))
        return public(rows[0]) if rows else None

    async def get(self, ref: str) -> dict | None:
        rows = await self.store.query("SELECT * FROM kb_document WHERE id=?", (document_key(ref),))
        return public(rows[0]) if rows else None

    async def file(self, ref: str) -> tuple[dict, Path] | None:
        document = await self.get(ref)
        return (document, self.folder / document["sha256"]) if document else None

    async def chunks(self, ref: str, start: int = 0, count: int = 3) -> list[dict]:
        return await self.store.query(
            "SELECT seq,label,text FROM kb_chunk WHERE document=? AND seq>=? ORDER BY seq LIMIT ?",
            (document_key(ref), start, count),
        )

    async def listing(self, query: str, page: int, page_size: int) -> dict:
        where, args = "", ()
        if query:
            where = "WHERE instr(lower(title||' '||filename),lower(?))>0"
            args = (query,)
        total = (await self.store.query(f"SELECT COUNT(*) AS n FROM kb_document {where}", args))[0][
            "n"
        ]
        page = min(page, max(1, (total + page_size - 1) // page_size))
        rows = await self.store.query(
            f"SELECT * FROM kb_document {where} ORDER BY created DESC,id DESC LIMIT ? OFFSET ?",
            (*args, page_size, (page - 1) * page_size),
        )
        return {
            "items": [public(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def stats(self) -> dict:
        row = (
            await self.store.query(
                "SELECT COUNT(*) AS documents,COALESCE(SUM(chunks),0) AS chunks,"
                "COALESCE(SUM(bytes),0) AS bytes FROM kb_document"
            )
        )[0]
        key = self.vectors.key()
        indexed = 0
        if key is not None:
            indexed = (
                await self.store.query(
                    "SELECT COUNT(*) AS n FROM kb_chunk c JOIN kb_vector v "
                    "ON v.id=CAST(c.id AS TEXT) AND v.model=?",
                    (key,),
                )
            )[0]["n"]
        return {
            **row,
            "index": {"model": key, "indexed": indexed, "pending": row["chunks"] - indexed},
        }

    # ---- hybrid search -------------------------------------------------------------

    async def keyword_hits(self, query: str, document: int, limit: int) -> list[int]:
        tokens = await asyncio.to_thread(tokenize_sync, query)
        # A one-character question ("水") still deserves an answer, stopword or not.
        words = keywords([*tokens, *bigrams(query)]) or [
            t.strip() for t in tokens if any(c.isalnum() for c in t)
        ]
        if not words:
            return []
        match = " OR ".join('"' + word.replace('"', '""') + '"' for word in words[:32])
        rows = await self.store.query(
            "SELECT kb_fts.rowid AS id FROM kb_fts JOIN kb_chunk c ON c.id=kb_fts.rowid "
            "WHERE kb_fts MATCH ? AND (?=0 OR c.document=?) ORDER BY bm25(kb_fts) LIMIT ?",
            (match, document, document, limit),
        )
        return [row["id"] for row in rows]

    async def vector_hits(self, query: str, document: int, limit: int) -> list[int]:
        # A document filter is applied after ranking, so it needs a much wider net.
        reach = limit * 40 if document else limit
        hits = [
            int(key)
            for key, score in await self.vectors.search(query, limit=reach)
            if score >= VECTOR_FLOOR
        ]
        if not document or not hits:
            return hits[:limit]
        marks = ",".join("?" * len(hits))
        rows = await self.store.query(
            f"SELECT id FROM kb_chunk WHERE document=? AND id IN ({marks})", (document, *hits)
        )
        kept = {row["id"] for row in rows}
        return [key for key in hits if key in kept][:limit]

    async def search(self, query: str, limit: int = 5, document: str = "") -> list[dict]:
        """Reciprocal rank fusion of BM25 and cosine; either side alone still works."""
        scope = document_key(document) if document else 0
        keyword, semantic = await asyncio.gather(
            self.keyword_hits(query, scope, POOL), self.vector_hits(query, scope, POOL)
        )
        scores: dict[int, float] = {}
        for ranked in (keyword, semantic):
            for rank, key in enumerate(ranked):
                scores[key] = scores.get(key, 0.0) + 1 / (RRF_K + rank + 1)
        best = sorted(scores, key=lambda key: (-scores[key], key))[:limit]
        if not best:
            return []
        marks = ",".join("?" * len(best))
        # Vectors may outlive their chunk for a moment; the join drops those.
        rows = await self.store.query(
            "SELECT c.id,c.document,c.seq,c.label,c.text,d.title FROM kb_chunk c "
            f"JOIN kb_document d ON d.id=c.document WHERE c.id IN ({marks})",
            tuple(best),
        )
        by_id = {row["id"]: row for row in rows}
        keyword_set, semantic_set = set(keyword), set(semantic)
        return [
            {
                "document": f"d{by_id[key]['document']}",
                "seq": by_id[key]["seq"],
                "title": by_id[key]["title"],
                "label": by_id[key]["label"],
                "text": by_id[key]["text"],
                "score": round(scores[key], 4),
                "match": "hybrid"
                if key in keyword_set and key in semantic_set
                else "keyword"
                if key in keyword_set
                else "semantic",
            }
            for key in best
            if key in by_id
        ]

    # ---- background embedding ------------------------------------------------------

    async def embed_pending(self) -> int:
        """Chunks never change, so a missing vector is the only reason to embed."""
        total = 0
        while (key := self.vectors.key()) is not None:
            rows = await self.store.query(
                "SELECT c.id,c.label,c.text,d.title FROM kb_chunk c "
                "JOIN kb_document d ON d.id=c.document "
                "LEFT JOIN kb_vector v ON v.id=CAST(c.id AS TEXT) AND v.model=? "
                "WHERE v.id IS NULL ORDER BY c.id LIMIT ?",
                (key, EMBED_PAGE),
            )
            if not rows:
                break
            documents = {
                str(row["id"]): searchable(row["title"], row["label"], row["text"]) for row in rows
            }
            done = await self.vectors.embed(documents, key)
            total += done
            if done < len(documents):
                break  # The embedding configuration changed; the next pass restarts.
        if total:
            # A document deleted mid-batch can leave vectors behind.
            await self.store.execute(
                "DELETE FROM kb_vector WHERE CAST(id AS INTEGER) NOT IN (SELECT id FROM kb_chunk)"
            )
            self.vectors.cache.clear()
        return total

    async def run(self) -> None:
        backoff = 30.0
        while True:
            self.wake.clear()
            try:
                done = await self.embed_pending()
                if done:
                    log.debug(f"已为 {done} 个分块计算向量")
                backoff = 30.0
            except Exception as exc:
                if isinstance(exc, PublicError):
                    log.warning(f"知识库向量计算暂停：{exc}，{backoff:.0f} 秒后重试")
                else:
                    log.error(f"知识库向量循环出错（{type(exc).__name__}）", exc=exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 900)
                continue
            with suppress(TimeoutError):
                await asyncio.wait_for(self.wake.wait(), timeout=300)
