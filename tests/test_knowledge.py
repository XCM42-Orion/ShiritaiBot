import io
import json
import shutil
import struct
import subprocess

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from shiritaibot.config import KnowledgeConfig, ModelConfig, ProviderConfig
from shiritaibot.core import Context, PublicError
from shiritaibot.knowledge.extract import Section, chunk_sections, extract
from shiritaibot.web import create_app

OWNER = Context("private:a:1", "owner", "agent", True)


def texts(data: bytes, filename: str) -> str:
    return "\n".join(s.label + "\n" + s.text for s in extract(data, filename)[1])


# ---- file fixtures -------------------------------------------------------------------


def ole_file(streams: dict[str, bytes]) -> bytes:
    """A minimal compound file: one FAT sector, one directory sector, regular streams."""
    sector = 512
    body, fat, starts = b"", [0xFFFFFFFD, 0xFFFFFFFE], {}
    for name, content in streams.items():
        content = content.ljust(4096, b"\0")  # At the mini-stream cutoff: regular sectors.
        content += b"\0" * (-len(content) % sector)
        count = len(content) // sector
        first = len(fat)
        starts[name] = (first, len(content))
        fat += [first + i + 1 for i in range(count - 1)] + [0xFFFFFFFE]
        body += content
    assert len(fat) <= 128

    def entry(name, kind, left, right, child, start, size):
        raw = name.encode("utf-16-le") + b"\0\0"
        return (
            raw.ljust(64, b"\0")
            + struct.pack("<HBB3I", len(raw), kind, 1, left, right, child)
            + b"\0" * 36
            + struct.pack("<IIxxxx", start, size)
        )

    nothing = 0xFFFFFFFF
    names = sorted(streams, key=lambda n: (len(n), n.upper()))
    directory = entry("Root Entry", 5, nothing, nothing, 1, 0xFFFFFFFE, 0)
    for index, name in enumerate(names):
        right = index + 2 if index + 1 < len(names) else nothing
        directory += entry(name, 2, nothing, right, nothing, *starts[name])
    directory = directory.ljust(sector, b"\0")
    header = (
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        + b"\0" * 16
        + struct.pack("<HHHH", 0x3E, 3, 0xFFFE, 9)
        + struct.pack("<H", 6)
        + b"\0" * 6
        + struct.pack("<II", 0, 1)  # directory sectors, FAT sectors
        + struct.pack("<II", 1, 0)  # first directory sector, transaction
        + struct.pack("<I", 4096)
        + struct.pack("<II", 0xFFFFFFFE, 0)  # mini FAT
        + struct.pack("<II", 0xFFFFFFFE, 0)  # DIFAT
        + struct.pack("<I", 0)
        + struct.pack("<I", 0xFFFFFFFF) * 108
    )
    table = b"".join(struct.pack("<I", v) for v in fat).ljust(sector, b"\xff")
    return header + table + directory + body


def legacy_doc(text: str, *, encrypted=False) -> bytes:
    raw = text.encode("utf-16-le")
    fc = 0x400
    stream = bytearray(max(fc + len(raw), 0x400))
    struct.pack_into("<H", stream, 0, 0xA5EC)
    struct.pack_into("<H", stream, 0x0A, 0x0200 | (0x0100 if encrypted else 0))
    struct.pack_into("<I", stream, 0x4C, len(text))
    stream[fc : fc + len(raw)] = raw
    plc = struct.pack("<II", 0, len(text)) + struct.pack("<HIH", 0, fc, 0)
    clx = b"\x02" + struct.pack("<I", len(plc)) + plc
    struct.pack_into("<II", stream, 0x1A2, 0, len(clx))
    return ole_file({"WordDocument": bytes(stream), "1Table": clx})


def ppt_atom(kind, body, instance=0, version=0):
    return struct.pack("<HHI", version | instance << 4, kind, len(body)) + body


def ppt_box(kind, children, instance=0):
    return ppt_atom(kind, b"".join(children), instance, 0xF)


def ppt_chars(text):
    return ppt_atom(0x0FA0, text.encode("utf-16-le"))


def legacy_ppt(*, chain=True) -> bytes:
    """Two slides saved twice: the second edit replaces slide 2 and appends a new Document."""

    def persist(key, slide_id=0):
        return ppt_atom(0x03F3, struct.pack("<IIIII", key, 0, 0, slide_id, 0))

    def slide_atom(notes):
        return ppt_atom(0x03EF, b"\0" * 16 + struct.pack("<I", notes) + b"\0" * 4)

    def document(titles):
        masters = ppt_box(0x0FF0, [persist(9), ppt_chars("单击此处编辑标题")], 1)
        slides = []
        for key, title in titles:
            slides += [persist(key), ppt_chars(title)]
        notes = ppt_box(0x0FF0, [persist(5, slide_id=0x100)], 2)
        return ppt_box(0x03E8, [masters, ppt_box(0x0FF0, slides, 0), notes])

    def slide(text, notes=0):
        box = ppt_box(0x040C, [ppt_atom(0x0FA8, text.encode("cp1252"))])
        return ppt_box(0x03EE, [slide_atom(notes), box])

    stream = b""
    offsets = {}

    def put(key, record):
        nonlocal stream
        offsets[key] = len(stream)
        stream += record

    def edit(keys, last):
        nonlocal stream
        entries = b"".join(struct.pack("<II", key | 1 << 20, offsets[key]) for key in keys)
        directory = len(stream)
        stream += ppt_atom(0x1772, entries)
        at = len(stream)
        stream += ppt_atom(0x0FF5, struct.pack("<I4xIII", 1, last, directory, 1))
        return at

    put(1, document([(2, "封面标题"), (3, "第二页旧标题")]))
    put(2, slide("Box one", notes=0x100))
    put(3, slide("Stale box"))
    put(5, ppt_box(0x03F0, [ppt_chars("封面的备注")]))
    put(9, ppt_box(0x03F8, [ppt_chars("母版文字")]))
    first = edit([1, 2, 3, 5, 9], 0)
    put(1, document([(2, "封面标题"), (3, "第二页新标题")]))
    put(3, slide("Fresh box"))
    second = edit([1, 3], first)
    current = ppt_atom(0x0FF6, struct.pack("<III", 0x14, 0xE391C05F, second) + b"\0" * 8)
    streams = {"PowerPoint Document": stream}
    if chain:
        streams["Current User"] = current
    return ole_file(streams)


def pdf_file(lines: list[str]) -> bytes:
    objects = []
    pages = len(lines)
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(pages))
    objects.append("<</Type/Catalog/Pages 2 0 R>>")
    objects.append(f"<</Type/Pages/Kids[{kids}]/Count {pages}>>")
    font = 3 + pages * 2
    for index, line in enumerate(lines):
        objects.append(
            f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 300]/Contents {4 + index * 2} 0 R"
            f"/Resources<</Font<</F1 {font} 0 R>>>>>>"
        )
        content = f"BT /F1 12 Tf 20 200 Td ({line}) Tj ET"
        objects.append(f"<</Length {len(content)}>>stream\n{content}\nendstream")
    objects.append("<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
    out, offsets = "%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n"
    start = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{offset:010} 00000 n \n" for offset in offsets)
    out += f"trailer<</Size {len(objects) + 1}/Root 1 0 R>>\nstartxref\n{start}\n%%EOF"
    return out.encode()


def docx_file() -> bytes:
    from docx import Document

    document = Document()
    document.add_heading("第一章 安装", 1)
    document.add_paragraph("运行安装脚本即可。")
    document.add_heading("依赖", 2)
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "名称", "版本"
    table.cell(1, 0).text, table.cell(1, 1).text = "Python", "3.12"
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def pptx_file() -> bytes:
    from pptx import Presentation

    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = "季度总结"
    slide.placeholders[1].text = "收入增长百分之二十"
    slide.notes_slide.notes_text_frame.text = "这里要强调成本"
    out = io.BytesIO()
    deck.save(out)
    return out.getvalue()


def xlsx_file() -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "价格"
    sheet.append(["商品", "单价"])
    sheet.append(["苹果", 3.5])
    sheet.append(["香蕉", 2])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def xls_file() -> bytes:
    import xlwt

    book = xlwt.Workbook()
    sheet = book.add_sheet("库存")
    for row, values in enumerate([("商品", "数量"), ("苹果", 12.0)]):
        for column, value in enumerate(values):
            sheet.write(row, column, value)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


# ---- extraction ----------------------------------------------------------------------


def test_text_encodings_and_markdown_headings():
    assert "你好" in texts("你好".encode("gb18030"), "a.txt")
    assert "你好" in texts("你好".encode("utf-16"), "a.txt")
    sections = extract("# 甲\n一\n## 乙\n二\n```\n# 不是标题\n```\n# 丙\n三".encode(), "a.md")[1]
    assert [s.label for s in sections] == ["甲", "甲 > 乙", "丙"]
    assert "不是标题" in sections[1].text


def test_csv_keeps_its_header_and_detects_delimiter():
    section = extract("姓名;年龄\n小明;18\n小红;20\n".encode(), "a.csv")[1][0]
    assert section.header == "姓名 | 年龄"
    assert section.text == "小明 | 18\n小红 | 20"


def test_pdf_pages_become_sections():
    sections = extract(pdf_file(["alpha page", "beta page"]), "a.pdf")[1]
    assert [s.label for s in sections] == ["第 1 页", "第 2 页"]
    assert "beta" in sections[1].text


def test_docx_tracks_headings_and_tables():
    sections = extract(docx_file(), "a.docx")[1]
    assert sections[0].label == "第一章 安装" and "安装脚本" in sections[0].text
    assert sections[1].label == "第一章 安装 > 依赖"
    assert "Python | 3.12" in sections[1].text


def test_pptx_reads_slides_and_notes():
    section = extract(pptx_file(), "a.pptx")[1][0]
    assert section.label == "第 1 张幻灯片"
    assert "季度总结" in section.text and "强调成本" in section.text


def test_spreadsheets_keep_sheet_name_and_header():
    section = extract(xlsx_file(), "a.xlsx")[1][0]
    assert section.label == "工作表 价格" and section.header == "商品 | 单价"
    assert "苹果 | 3.5" in section.text and "香蕉 | 2" in section.text
    section = extract(xls_file(), "a.xls")[1][0]
    assert section.label == "工作表 库存" and "苹果 | 12" in section.text


def test_legacy_doc_piece_table():
    body = "第一段\r\x13HYPERLINK x\x14链接\x15文字\r单元格\x07"
    text = texts(legacy_doc(body), "a.doc")
    assert "第一段" in text and "链接文字" in text and "单元格" in text
    assert "HYPERLINK" not in text and "\x07" not in text
    with pytest.raises(PublicError, match="加密"):
        extract(legacy_doc("x", encrypted=True), "a.doc")


def test_legacy_ppt_follows_the_latest_save():
    by_label = {s.label: s.text for s in extract(legacy_ppt(), "a.ppt")[1]}
    assert by_label == {
        "第 1 张幻灯片": "封面标题\nBox one\n封面的备注",
        "第 2 张幻灯片": "第二页新标题\nFresh box",
    }


def test_legacy_ppt_without_edit_chain_still_gives_its_text():
    text = texts(legacy_ppt(chain=False), "a.ppt")
    assert "第二页新标题" in text and "Box one" in text
    assert "母版文字" not in text and "单击此处" not in text


@pytest.mark.skipif(not shutil.which("soffice"), reason="needs LibreOffice")
@pytest.mark.parametrize(
    "source,target,expected",
    [(docx_file, "doc", ["安装脚本", "Python"]), (pptx_file, "ppt", ["季度总结", "收入增长"])],
)
def test_real_legacy_files_written_by_libreoffice(tmp_path, source, target, expected):
    name = "in." + target + "x"
    (tmp_path / name).write_bytes(source())
    subprocess.run(
        [
            "soffice",
            f"-env:UserInstallation=file://{tmp_path}/profile",
            "--headless",
            "--convert-to",
            target,
            "--outdir",
            str(tmp_path / "out"),
            str(tmp_path / name),
        ],
        capture_output=True,
        timeout=120,
    )
    converted = tmp_path / "out" / f"in.{target}"
    if not converted.exists():
        pytest.skip(f"this LibreOffice install cannot write .{target}")
    text = texts(converted.read_bytes(), f"in.{target}")
    for word in expected:
        assert word in text


@pytest.mark.parametrize(
    "data,name,message",
    [
        (b"x", "a.exe", "不支持"),
        (b"{\\rtf1 hello}", "a.doc", "RTF"),
        (b"plain", "a.xlsx", "Excel"),
        (b"PK\x03\x04broken", "a.docx", "无法解析"),
        (b"\0\1\2", "a.txt", "不是文本"),
        (b"   \n", "a.txt", "没有可提取"),
        (pdf_file([""]), "a.pdf", "OCR"),
    ],
)
def test_bad_files_explain_themselves(data, name, message):
    with pytest.raises(PublicError, match=message):
        extract(data, name)


# ---- chunking ------------------------------------------------------------------------


def test_chunks_respect_size_and_overlap():
    text = "\n".join(f"这是第{i}句话，内容不长。" for i in range(100))
    chunks = chunk_sections([Section("章", text)], 200, 20)
    assert len(chunks) > 3 and all(label == "章" for label, _ in chunks)
    assert all(len(body) <= 220 for _, body in chunks)
    assert chunks[0][1][-10:] in chunks[1][1]  # The tail repeats at the next chunk's start.
    joined = "".join(body for _, body in chunks)
    assert all(f"这是第{i}句话" in joined for i in range(100))


def test_overlong_line_is_split_at_sentences_then_hard():
    chunks = chunk_sections([Section("", "短句。" * 200 + "长" * 500)], 200, 0)
    assert all(len(body) <= 200 for _, body in chunks)
    assert "".join(body for _, body in chunks).count("长") == 500


def test_table_chunks_repeat_the_header_without_overlap():
    rows = "\n".join(f"商品{i} | {i}" for i in range(200))
    chunks = chunk_sections([Section("表", rows, "名称 | 价格")], 200, 50)
    assert len(chunks) > 2
    assert all(body.startswith("名称 | 价格\n") and len(body) <= 200 for _, body in chunks)
    assert sum(body.count("商品") for _, body in chunks) == 200


def test_overlap_cannot_swallow_the_chunk():
    with pytest.raises(ValidationError):
        KnowledgeConfig(chunk_chars=200, chunk_overlap=101)


# ---- storage and search --------------------------------------------------------------


@pytest.fixture
def no_background(app):
    """Embedding tests drive the index themselves instead of racing the background loop."""
    for task in app.tasks:
        if task.get_coro().__qualname__ == "KnowledgeBase.run":
            task.cancel()


def with_embeddings(app, calls=None):
    cfg = app.config.value
    cfg.providers.append(ProviderConfig(id="emb", base_url="https://emb.test/v1", api_key="k"))
    cfg.models.append(ModelConfig(id="vec", provider="emb", model="v1", kind="embedding"))
    cfg.routes["embedding"] = ["vec"]

    async def embed(items, *, query=False, settings=None, route="embedding"):
        if calls is not None:
            calls.append((len(items), query))
        # A toy space: coffee words point one way, everything else another.
        return "emb:v1", [
            [1.0, 0.0] if "咖啡" in t or "美式" in t or "浓缩" in t else [0.0, 1.0] for t in items
        ]

    app.llm.embed = embed


async def test_import_is_deduplicated_and_delete_removes_everything(app):
    kb = app.knowledge
    first = await kb.add("苹果的单价是三块五。".encode(), "价格表.txt", source="web")
    assert first["id"] == "d1" and first["title"] == "价格表" and not first["duplicate"]
    again = await kb.add("苹果的单价是三块五。".encode(), "别名.txt", source="web")
    assert again["id"] == "d1" and again["duplicate"]
    assert (await kb.stats())["documents"] == 1
    assert (kb.folder / first["sha256"]).exists()
    assert await kb.search("苹果 单价")
    await kb.delete("d1")
    assert await kb.search("苹果 单价") == []
    assert await kb.chunks("d1") == []
    assert not (kb.folder / first["sha256"]).exists()
    assert (await app.store.query("SELECT COUNT(*) AS n FROM kb_fts"))[0]["n"] == 0
    with pytest.raises(PublicError):
        await kb.delete("d1")


async def test_chunk_ids_are_not_reused_after_delete(app):
    kb = app.knowledge
    await kb.add("甲文档内容".encode(), "a.txt", source="web")
    await kb.delete("d1")
    await kb.add("乙文档内容".encode(), "b.txt", source="web")
    rows = await app.store.query("SELECT id FROM kb_chunk")
    assert rows == [{"id": 2}]


async def test_failed_import_leaves_nothing_behind(app, monkeypatch):
    kb = app.knowledge

    async def broken(*args):
        raise RuntimeError("disk full")

    monkeypatch.setattr(kb, "insert", broken)
    with pytest.raises(RuntimeError):
        await kb.add("内容".encode(), "a.txt", source="web")
    assert list(kb.folder.iterdir()) == []


async def test_keyword_search_works_without_embeddings(app):
    kb = app.knowledge
    assert kb.vectors.key() is None
    await kb.add("冰美式需要两份浓缩。".encode(), "咖啡手册.md", source="web")
    await kb.add("拿铁需要牛奶。".encode(), "牛奶手册.md", source="web")
    found = await kb.search("牛奶")
    assert [row["title"] for row in found] == ["牛奶手册"]
    assert found[0]["match"] == "keyword" and found[0]["document"] == "d2"
    # The title is searchable even when the chunk text does not mention it.
    assert (await kb.search("咖啡手册"))[0]["document"] == "d1"
    assert await kb.search("!!!") == []
    assert (await kb.stats())["index"]["model"] is None


async def test_keywords_survive_words_the_segmenter_does_not_know(app):
    kb = app.knowledge
    await kb.add("公司年假政策：入职满一年享有十天带薪年假。".encode(), "制度.txt", source="web")
    await kb.add("报销需要提供发票原件。".encode(), "财务.txt", source="web")
    found = await kb.search("年假有几天")
    assert [row["title"] for row in found] == ["制度"]
    assert found[0]["match"] == "keyword"


async def test_hybrid_search_fuses_both_retrievers(app, no_background):
    calls = []
    with_embeddings(app, calls)
    kb = app.knowledge
    await kb.add("冰美式需要两份浓缩。".encode(), "饮品.txt", source="web")
    await kb.add("咖啡豆存放在阴凉处。".encode(), "仓储.txt", source="web")
    await kb.add("苹果每斤三块五。".encode(), "水果.txt", source="web")
    assert (await kb.stats())["index"]["pending"] == 3
    assert await kb.embed_pending() == 3
    assert await kb.embed_pending() == 0  # Nothing is embedded twice.
    assert (await kb.stats())["index"] == {"model": "emb:v1", "indexed": 3, "pending": 0}

    found = await kb.search("咖啡")
    # Matches both ways beats a semantic-only match; the unrelated text is below the floor.
    assert [row["title"] for row in found] == ["仓储", "饮品"]
    assert [row["match"] for row in found] == ["hybrid", "semantic"]
    assert (True in {query for _, query in calls}) and found[0]["score"] > found[1]["score"]

    only = await kb.search("咖啡", document="d1")
    assert [row["title"] for row in only] == ["饮品"]

    await kb.delete("d1")
    assert [row["title"] for row in await kb.search("咖啡")] == ["仓储"]
    assert (await kb.stats())["index"]["indexed"] == 2
    rows = await app.store.query("SELECT id FROM kb_vector ORDER BY id")
    assert [row["id"] for row in rows] == ["2", "3"]


async def test_embedding_failure_degrades_to_keywords(app, no_background):
    with_embeddings(app)
    kb = app.knowledge
    await kb.add("拿铁需要牛奶。".encode(), "a.txt", source="web")
    await kb.embed_pending()

    async def down(*args, **kwargs):
        raise PublicError("provider down")

    app.llm.embed = down
    found = await kb.search("牛奶")
    assert found and found[0]["match"] == "keyword"


async def test_new_embedding_model_reembeds_everything(app, no_background):
    with_embeddings(app)
    kb = app.knowledge
    await kb.add("拿铁需要牛奶。".encode(), "a.txt", source="web")
    await kb.embed_pending()
    original = app.llm.embed

    async def other(items, **kwargs):
        return "emb:v2", (await original(items, **kwargs))[1]

    app.llm.embed = other
    app.config.value.models[-1].model = "v2"
    assert (await kb.stats())["index"]["pending"] == 1
    assert await kb.embed_pending() == 1


# ---- tools ---------------------------------------------------------------------------


async def run(app, name, args, ctx=OWNER):
    app.config.value.policy.owner_default.tools[name] = "allow"
    return json.loads(await app.registry.execute(ctx, name, args))


class FakeBot:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    async def call(self, action, params):
        self.calls.append(action)
        answer = self.answers.get(action)
        if answer is None:
            raise PublicError("unsupported")
        return answer


def serve_qq_file(monkeypatch, content: bytes):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=content))
    real = httpx.AsyncClient
    monkeypatch.setattr(
        "shiritaibot.tools.qq.httpx.AsyncClient",
        lambda **kwargs: real(transport=transport, **kwargs),
    )


async def test_private_chat_file_reaches_the_knowledge_base(app, monkeypatch):
    # NapCat names the file in `file` and answers get_file with a local path.
    bot = FakeBot(
        {
            "get_file": {"url": "/tmp/napcat/manual.docx"},
            "get_private_file_url": {"url": "https://multimedia.nt.qq.com.cn/download?id=1"},
        }
    )
    app.adapters.instances["a"] = bot
    ref = "a" * 32
    await app.store.put(
        "qq_file",
        ref,
        {"adapter": "a", "group_id": "", "data": {"file": "手册.docx", "file_id": "F"}},
        OWNER.session,
    )
    serve_qq_file(monkeypatch, docx_file())
    try:
        row = await run(app, "kb_import", {"ref": ref})
    finally:
        del app.adapters.instances["a"]
    assert row["id"] == "d1" and row["title"] == "手册" and row["format"] == "docx"
    assert bot.calls == ["get_file", "get_private_file_url"]
    document = await app.knowledge.get("d1")
    assert document["source"] == "qq" and document["origin"] == OWNER.session
    found = await run(app, "kb_search", {"query": "安装脚本"})
    assert found["results"][0]["label"] == "第一章 安装"


async def test_file_refs_stay_in_their_session(app):
    ref = "b" * 32
    await app.store.put("qq_file", ref, {"adapter": "a", "data": {}}, "private:a:other")
    assert "不属于" in (await run(app, "kb_import", {"ref": ref}))["error"]


async def test_import_from_content_and_workspace_path(app, tmp_path):
    row = await run(app, "kb_import", {"content": "# 标题\n正文", "title": "笔记"})
    assert row["format"] == "md" and row["title"] == "笔记"
    assert "title" in (await run(app, "kb_import", {"content": "x"}))["error"]
    assert "只能提供一个" in (await run(app, "kb_import", {"content": "x", "path": "y"}))["error"]
    assert "只能提供一个" in (await run(app, "kb_import", {}))["error"]

    from shiritaibot.tools.qq import session_dir

    workspace = session_dir(app.home, "workspaces", OWNER.session)
    workspace.mkdir(parents=True)
    (workspace / "data.csv").write_text("名称,价格\n苹果,3\n", encoding="utf-8")
    assert (await run(app, "kb_import", {"path": "data.csv"}))["format"] == "csv"

    secret = tmp_path / "outside.txt"
    secret.write_text("x")
    for path in (str(secret), "../../vault.key", str(app.home / "web.token")):
        assert "只能导入" in (await run(app, "kb_import", {"path": path}))["error"]
    assert "不存在" in (await run(app, "kb_import", {"path": "missing.txt"}))["error"]


async def test_read_list_and_delete_tools(app):
    body = "\n".join(f"第{i}行内容，用来撑满分块长度。" for i in range(200))
    await run(app, "kb_import", {"content": body, "title": "长文"})
    first = await run(app, "kb_read", {"document": "d1", "count": 2})
    assert [c["seq"] for c in first["chunks"]] == [0, 1] and first["next_start"] == 2
    tail = await run(app, "kb_read", {"document": "d1", "start": first["total_chunks"] - 1})
    assert "next_start" not in tail
    assert "不存在" in (await run(app, "kb_read", {"document": "d9"}))["error"]
    listed = await run(app, "kb_list", {"query": "长"})
    assert listed["total"] == 1 and listed["items"][0]["title"] == "长文"
    assert (await run(app, "kb_delete", {"document": "d1"}))["deleted"] == "d1"
    assert (await run(app, "kb_list", {}))["total"] == 0


async def test_writes_are_owner_private_only(app):
    group = Context("group:a:1", "owner", "agent", True, True)
    stranger = Context("private:a:2", "2", "agent", False)
    for ctx in (group, stranger):
        result = await run(app, "kb_import", {"content": "x", "title": "t"}, ctx)
        assert "error" in result
        assert "error" in await run(app, "kb_delete", {"document": "d1"}, ctx)
    assert (await app.knowledge.stats())["documents"] == 0


async def test_chat_access_and_enabled_switch(app):
    from shiritaibot.tools.builtin import register_builtin

    chat = Context("private:a:9", "9", "chat", False)
    names = lambda ctx: {t["function"]["name"] for t in app.registry.specs(ctx)}  # noqa: E731
    assert not names(chat) & {"kb_search", "kb_read", "kb_list"}
    app.config.value.knowledge.chat = True
    await register_builtin(app)
    assert {"kb_search", "kb_read", "kb_list"} <= names(chat)
    assert not names(chat) & {"kb_import", "kb_delete"}
    app.config.value.knowledge.enabled = False
    assert not {n for n in names(chat) | names(OWNER) if n.startswith("kb_")}


# ---- web -----------------------------------------------------------------------------


def test_web_api_round_trip(tmp_path):
    with TestClient(create_app(tmp_path, token="t")) as client:
        auth = {"Authorization": "Bearer t"}
        assert client.get("/api/knowledge").status_code == 401
        uploaded = client.post(
            "/api/knowledge",
            params={"filename": "../../价格.xlsx", "title": "价目"},
            content=xlsx_file(),
            headers=auth,
        )
        assert uploaded.status_code == 200, uploaded.text
        row = uploaded.json()
        assert row["id"] == "d1" and row["filename"] == "价格.xlsx" and row["source"] == "web"

        listing = client.get("/api/knowledge", headers=auth).json()
        assert listing["total"] == 1 and listing["documents"] == 1
        assert listing["chunks"] == row["chunks"] and listing["index"]["model"] is None
        assert client.get("/api/knowledge?q=不存在", headers=auth).json()["total"] == 0

        detail = client.get("/api/knowledge/d1", headers=auth).json()
        assert detail["items"][0]["label"] == "工作表 价格"
        found = client.get("/api/knowledge/search", params={"q": "苹果"}, headers=auth).json()
        assert found[0]["document"] == "d1" and "苹果" in found[0]["text"]

        file = client.get("/api/knowledge/d1/file", headers=auth)
        assert file.content == xlsx_file() or file.content[:2] == b"PK"
        assert file.headers["content-type"] == "application/octet-stream"
        assert "attachment" in file.headers["content-disposition"]

        bad = client.post(
            "/api/knowledge", params={"filename": "a.exe"}, content=b"x", headers=auth
        )
        assert bad.status_code == 400 and "不支持" in bad.json()["detail"]
        assert client.get("/api/knowledge/bad", headers=auth).status_code == 400
        assert client.get("/api/knowledge/d9", headers=auth).status_code == 404

        assert client.delete("/api/knowledge/d1", headers=auth).json() == {"ok": True}
        assert client.get("/api/knowledge/d1/file", headers=auth).status_code == 404


def test_web_upload_size_limit(tmp_path):
    with TestClient(create_app(tmp_path, token="t")) as client:
        client.app.state.runtime.config.value.knowledge.max_file_mb = 1  # type: ignore[attr-defined]
        response = client.post(
            "/api/knowledge",
            params={"filename": "a.txt"},
            content=b"x" * (1024 * 1024 + 1),
            headers={"Authorization": "Bearer t"},
        )
        assert response.status_code == 413
