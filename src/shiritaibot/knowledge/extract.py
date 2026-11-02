"""Turn an uploaded file into labelled text sections, then into chunks.

Everything here is synchronous and CPU-bound; callers run it in a worker thread. Files
come from the owner but may still be damaged or encrypted, so every parser failure
surfaces as a PublicError naming the format.
"""

from __future__ import annotations

import csv
import io
import re
import struct
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path

from shiritaibot.core import PublicError

TEXT_FORMATS = frozenset({"txt", "md", "markdown", "rst", "log", "json", "yaml", "yml", "xml"})
TABLE_TEXT_FORMATS = frozenset({"csv", "tsv"})
FORMATS = frozenset(
    TEXT_FORMATS | TABLE_TEXT_FORMATS | {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx"}
)
MAX_CHARS = 20_000_000
MAX_UNZIPPED = 400 * 1024 * 1024
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_MAGIC = b"PK\x03\x04"
SENTENCE = re.compile(r"(?<=[。！？；!?;])|(?<=[.]\s)")
MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
DOCX_HEADING = re.compile(r"^(?:heading|标题)\s*(\d)$", re.IGNORECASE)


@dataclass(slots=True)
class Section:
    label: str
    text: str
    # Table sections repeat this row in every chunk so that cells keep their meaning.
    header: str = ""


def extension(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".")


def decode_text(data: bytes) -> str:
    for mark, encoding in (
        (b"\xef\xbb\xbf", "utf-8-sig"),
        (b"\xff\xfe", "utf-16"),
        (b"\xfe\xff", "utf-16"),
    ):
        if data.startswith(mark):
            return data.decode(encoding, errors="replace")
    if b"\0" in data:
        raise PublicError("文件内容不是文本")
    for encoding in ("utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise PublicError("无法识别文本编码，请转存为 UTF-8")


def clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x0b", "\n").replace("\f", "\n")
    text = re.sub(r"[^\S\n]+\n", "\n", text.replace("\0", ""))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (datetime, date, time)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    return " ".join(str(value).split())


def table_section(label: str, rows) -> Section | None:
    lines = []
    for row in rows:
        values = [cell(value) for value in row]
        while values and not values[-1]:
            values.pop()
        if values:
            lines.append(" | ".join(values))
    if not lines:
        return None
    return Section(label, "\n".join(lines[1:]) or lines[0], lines[0] if len(lines) > 1 else "")


# ---- plain text ----------------------------------------------------------------------


def markdown_sections(text: str) -> list[Section]:
    sections: list[Section] = []
    path: list[str] = []
    current: list[str] = []
    fence = False

    def flush() -> None:
        body = clean("\n".join(current))
        if body:
            sections.append(Section(" > ".join(path), body))
        current.clear()

    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        match = None if fence else MD_HEADING.match(line)
        if match:
            flush()
            path = [*path[: len(match[1]) - 1], match[2]]
        else:
            current.append(line)
    flush()
    return sections


def delimited_sections(text: str, name: str) -> list[Section]:
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel_tab if name == "tsv" else csv.excel
    section = table_section("", csv.reader(io.StringIO(text, newline=""), dialect))
    return [section] if section else []


# ---- binary formats ------------------------------------------------------------------


def guard_zip(data: bytes) -> None:
    """Refuse decompression bombs before an OOXML parser expands the archive."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sum(item.file_size for item in archive.infolist()) > MAX_UNZIPPED:
            raise PublicError("压缩后的文档内容过大")


def pdf_sections(data: bytes) -> list[Section]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted and not reader.decrypt(""):
        raise PublicError("PDF 已加密，请解除密码后再上传")
    sections = []
    for number, page in enumerate(reader.pages, 1):
        text = clean(page.extract_text() or "")
        if text:
            sections.append(Section(f"第 {number} 页", text))
    return sections


def docx_sections(data: bytes) -> list[Section]:
    from docx import Document
    from docx.table import Table

    sections: list[Section] = []
    path: list[str] = []
    current: list[str] = []

    def flush() -> None:
        body = clean("\n".join(current))
        if body:
            sections.append(Section(" > ".join(path), body))
        current.clear()

    for block in Document(io.BytesIO(data)).iter_inner_content():
        if isinstance(block, Table):
            for row in block.rows:
                values: list[str] = []
                for item in row.cells:
                    text = " ".join(item.text.split())
                    # A merged cell is reported once per grid column it spans.
                    if not values or values[-1] != text:
                        values.append(text)
                current.append(" | ".join(values))
            continue
        style = block.style.name if block.style is not None else ""
        match = DOCX_HEADING.match(style or "")
        if match and block.text.strip():
            flush()
            path = [*path[: int(match[1]) - 1], block.text.strip()]
        else:
            current.append(block.text)
    flush()
    return sections


def word_binary_text(stream: bytes, table: bytes) -> str:
    """Main document text via the piece table ([MS-DOC] §2.8.35)."""
    ccp_text = struct.unpack_from("<I", stream, 0x4C)[0]
    fc_clx, lcb_clx = struct.unpack_from("<II", stream, 0x1A2)
    clx = table[fc_clx : fc_clx + lcb_clx]
    pos = 0
    while pos < len(clx) and clx[pos] == 0x01:  # Prc: formatting we do not need
        pos += 3 + struct.unpack_from("<H", clx, pos + 1)[0]
    if pos >= len(clx) or clx[pos] != 0x02:
        raise ValueError("no piece table")
    size = struct.unpack_from("<I", clx, pos + 1)[0]
    plc = clx[pos + 5 : pos + 5 + size]
    count = (len(plc) - 4) // 12
    pieces = []
    for index in range(count):
        start, end = struct.unpack_from("<II", plc, index * 4)
        fc = struct.unpack_from("<I", plc, (count + 1) * 4 + index * 8 + 2)[0]
        length = end - start
        if fc & 0x40000000:
            offset = (fc & ~0x40000000) // 2
            pieces.append(stream[offset : offset + length].decode("cp1252", errors="replace"))
        else:
            pieces.append(stream[fc : fc + length * 2].decode("utf-16-le", errors="replace"))
    return "".join(pieces)[: ccp_text or None]


def word_binary_clean(text: str) -> str:
    # Field codes: \x13 instruction \x14 result \x15 keeps only the result.
    text = re.sub(r"\x13[^\x13\x14\x15]*\x14", "", text)
    text = re.sub(r"\x13[^\x13\x14\x15]*\x15", "", text).replace("\x15", "")
    # \x07 ends a table cell; object anchors and the like are other control characters.
    text = text.replace("\x07", " | ")
    return clean(re.sub(r"[\x00-\x06\x08\x0e-\x1f]", "", text))


def doc_sections(data: bytes) -> list[Section]:
    import olefile

    with olefile.OleFileIO(io.BytesIO(data)) as ole:
        if ole.exists("EncryptedPackage"):
            raise PublicError("文档已加密，请解除密码后再上传")
        if not ole.exists("WordDocument"):
            raise PublicError("不是有效的 Word 文档")
        stream = ole.openstream("WordDocument").read()
        flags = struct.unpack_from("<H", stream, 0x0A)[0]
        if flags & 0x0100:
            raise PublicError("文档已加密，请解除密码后再上传")
        name = "1Table" if flags & 0x0200 else "0Table"
        text = word_binary_clean(word_binary_text(stream, ole.openstream(name).read()))
    return [Section("", text)] if text else []


PPT_TEXT_CHARS, PPT_TEXT_BYTES = 0x0FA0, 0x0FA8
PPT_MASTER, PPT_SLIDE_ATOM = 0x03F8, 0x03EF
PPT_SLIDE_LIST, PPT_SLIDE_PERSIST = 0x0FF0, 0x03F3
PPT_MAX_DEPTH = 32


def ppt_records(stream: bytes, start: int, end: int, depth: int = 0):
    """(kind, instance, body, stop) of every atom and container, parents first."""
    pos = start
    while pos + 8 <= end and depth < PPT_MAX_DEPTH:
        head, kind, size = struct.unpack_from("<HHI", stream, pos)
        body, stop = pos + 8, min(pos + 8 + size, end)
        yield kind, head >> 4, body, stop
        if head & 0xF == 0xF:
            yield from ppt_records(stream, body, stop, depth + 1)
        pos = stop


def ppt_atom_text(stream: bytes, kind: int, body: int, stop: int) -> str:
    encoding = "utf-16-le" if kind == PPT_TEXT_CHARS else "cp1252"
    return clean(stream[body:stop].decode(encoding, errors="replace"))


def ppt_container_end(stream: bytes, offset: int) -> tuple[int, int]:
    size = struct.unpack_from("<I", stream, offset + 4)[0]
    return offset + 8, min(offset + 8 + size, len(stream))


def ppt_persist_map(stream: bytes, current_user: bytes) -> tuple[dict[int, int], int]:
    """Latest offset of every persist object, and the id of the live Document.

    Fast saves append new revisions and leave old ones in place, so only this chain
    ([MS-PPT] §2.3.3) says which records are current; a plain scan reads stale text.
    """
    edit = struct.unpack_from("<I", current_user, 16)[0]
    objects: dict[int, int] = {}
    document = 0
    visited: set[int] = set()
    while edit and edit not in visited:
        visited.add(edit)
        last, directory, doc_ref = struct.unpack_from("<III", stream, edit + 8 + 8)
        document = document or doc_ref
        size = struct.unpack_from("<I", stream, directory + 4)[0]
        pos, end = directory + 8, min(directory + 8 + size, len(stream))
        while pos + 4 <= end:
            entry = struct.unpack_from("<I", stream, pos)[0]
            first, count = entry & 0xFFFFF, entry >> 20
            for index in range(count):
                offset = struct.unpack_from("<I", stream, pos + 4 + index * 4)[0]
                objects.setdefault(first + index, offset)  # Newest edit is read first.
            pos += 4 + count * 4
        edit = last
    return objects, document


def ppt_live_sections(stream: bytes, current_user: bytes) -> list[Section]:
    objects, document = ppt_persist_map(stream, current_user)
    # Each slide starts with whatever text the Document's slide list holds for it.
    slides: list[tuple[int, list[str]]] = []
    body, end = ppt_container_end(stream, objects[document])
    # A SlideAtom names its notes by slide id; the notes list maps that to a persist id.
    notes_persist: dict[int, int] = {}
    list_kind, list_end = -1, 0
    for kind, instance, start, stop in ppt_records(stream, body, end):
        if kind == PPT_SLIDE_LIST:
            list_kind, list_end = instance, stop
        elif start >= list_end:
            list_kind = -1
        if kind == PPT_SLIDE_PERSIST and list_kind == 0:
            slides.append((struct.unpack_from("<I", stream, start)[0], []))
        elif kind == PPT_SLIDE_PERSIST and list_kind == 2:
            persist, _, _, slide_id = struct.unpack_from("<IIII", stream, start)
            notes_persist[slide_id] = persist
        elif kind in (PPT_TEXT_CHARS, PPT_TEXT_BYTES) and list_kind == 0 and slides:
            slides[-1][1].append(ppt_atom_text(stream, kind, start, stop))

    def container_texts(offset: int | None) -> tuple[list[str], int]:
        """Text atoms in a Slide or Notes container, and the notes slide id it names."""
        if offset is None:
            return [], 0
        texts, notes = [], 0
        for kind, _, start, stop in ppt_records(stream, *ppt_container_end(stream, offset)):
            if kind in (PPT_TEXT_CHARS, PPT_TEXT_BYTES):
                texts.append(ppt_atom_text(stream, kind, start, stop))
            elif kind == PPT_SLIDE_ATOM and stop - start >= 20:
                notes = struct.unpack_from("<I", stream, start + 16)[0]
        return texts, notes

    sections = []
    for number, (persist, listed) in enumerate(slides, 1):
        own, notes_id = container_texts(objects.get(persist))
        notes = container_texts(objects.get(notes_persist.get(notes_id, -1)))[0]
        # Slide-number and date placeholders are single characters.
        lines = dict.fromkeys(text for text in [*listed, *own, *notes] if len(text) > 1)
        if lines:
            sections.append(Section(f"第 {number} 张幻灯片", "\n".join(lines)))
    return sections


def ppt_sections(data: bytes) -> list[Section]:
    import olefile

    with olefile.OleFileIO(io.BytesIO(data)) as ole:
        if ole.exists("EncryptedSummary") or ole.exists("EncryptedPackage"):
            raise PublicError("演示文稿已加密，请解除密码后再上传")
        if not ole.exists("PowerPoint Document"):
            raise PublicError("不是有效的 PowerPoint 文件")
        stream = ole.openstream("PowerPoint Document").read()
        current = ole.openstream("Current User").read() if ole.exists("Current User") else b""
    try:
        if sections := ppt_live_sections(stream, current):
            return sections
    except (struct.error, KeyError, IndexError):
        pass  # Damaged or foreign writer: fall back to whatever text atoms exist.
    lines: dict[str, None] = {}
    master_end = 0
    for kind, instance, start, stop in ppt_records(stream, 0, len(stream)):
        if kind == PPT_MASTER or (kind == PPT_SLIDE_LIST and instance == 1):
            master_end = max(master_end, stop)
        elif start >= master_end and kind in (PPT_TEXT_CHARS, PPT_TEXT_BYTES):
            text = ppt_atom_text(stream, kind, start, stop)
            if len(text) > 1:
                lines[text] = None
    return [Section("", "\n".join(lines))] if lines else []


def pptx_sections(data: bytes) -> list[Section]:
    from pptx import Presentation

    def shape_text(shape) -> list[str]:
        if getattr(shape, "shape_type", None) == 6:  # MSO_SHAPE_TYPE.GROUP
            return [text for inner in shape.shapes for text in shape_text(inner)]
        if getattr(shape, "has_table", False):
            return [
                " | ".join(" ".join(c.text.split()) for c in row.cells) for row in shape.table.rows
            ]
        return [shape.text_frame.text] if getattr(shape, "has_text_frame", False) else []

    sections = []
    for number, slide in enumerate(Presentation(io.BytesIO(data)).slides, 1):
        lines = [text for shape in slide.shapes for text in shape_text(shape)]
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            lines.append(slide.notes_slide.notes_text_frame.text)
        text = clean("\n".join(lines))
        if text:
            sections.append(Section(f"第 {number} 张幻灯片", text))
    return sections


def xlsx_sections(data: bytes) -> list[Section]:
    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        sections = [
            table_section(f"工作表 {sheet.title}", sheet.iter_rows(values_only=True))
            for sheet in book.worksheets
        ]
    finally:
        book.close()
    return [section for section in sections if section]


def xls_sections(data: bytes) -> list[Section]:
    import xlrd

    try:
        book = xlrd.open_workbook(file_contents=data)
    except xlrd.biffh.XLRDError as exc:
        if "encrypted" in str(exc).lower():
            raise PublicError("表格已加密，请解除密码后再上传") from None
        raise

    def value(item):
        if item.ctype == xlrd.XL_CELL_DATE:
            try:
                return xlrd.xldate_as_datetime(item.value, book.datemode)
            except ValueError:
                return item.value
        if item.ctype == xlrd.XL_CELL_BOOLEAN:
            return bool(item.value)
        if item.ctype == xlrd.XL_CELL_ERROR:
            return None
        return item.value

    sections = [
        table_section(
            f"工作表 {sheet.name}",
            ([value(item) for item in sheet.row(index)] for index in range(sheet.nrows)),
        )
        for sheet in book.sheets()
    ]
    return [section for section in sections if section]


# Which parser the bytes call for: extensions lie (a .doc may really be OOXML or RTF).
OFFICE = {
    "doc": ("Word", docx_sections, doc_sections),
    "docx": ("Word", docx_sections, doc_sections),
    "xls": ("Excel", xlsx_sections, xls_sections),
    "xlsx": ("Excel", xlsx_sections, xls_sections),
    "ppt": ("PowerPoint", pptx_sections, ppt_sections),
    "pptx": ("PowerPoint", pptx_sections, ppt_sections),
}


def office_sections(data: bytes, name: str) -> list[Section]:
    family, modern, legacy = OFFICE[name]
    if data.startswith(ZIP_MAGIC):
        guard_zip(data)
        return modern(data)
    if data.startswith(OLE_MAGIC):
        if name.endswith("x"):
            # OOXML files with a password are wrapped in an OLE container.
            import olefile

            with olefile.OleFileIO(io.BytesIO(data)) as ole:
                if ole.exists("EncryptedPackage"):
                    raise PublicError("文档已加密，请解除密码后再上传")
        return legacy(data)
    if data.lstrip().startswith(b"{\\rtf"):
        raise PublicError("这是 RTF 文件，请在 Word 中另存为 .docx 后再上传")
    raise PublicError(f"文件内容不是 {family} 格式")


def sections_of(data: bytes, name: str) -> list[Section]:
    if name in OFFICE:
        return office_sections(data, name)
    if name == "pdf":
        return pdf_sections(data)
    text = decode_text(data)
    if name in TABLE_TEXT_FORMATS:
        return delimited_sections(text, name)
    if name in {"md", "markdown"}:
        return markdown_sections(text)
    text = clean(text)
    return [Section("", text)] if text else []


def extract(data: bytes, filename: str) -> tuple[str, list[Section]]:
    """(format, sections) of a file; raises PublicError when nothing usable comes out."""
    name = extension(filename)
    if name not in FORMATS:
        raise PublicError("不支持的文件格式，支持：" + "、".join(sorted(FORMATS)))
    try:
        sections = sections_of(data, name)
    except PublicError:
        raise
    except Exception as exc:
        raise PublicError(f"无法解析 {name} 文件（{type(exc).__name__}）") from exc
    if not sections:
        hint = "（扫描版 PDF 需要先做 OCR）" if name == "pdf" else ""
        raise PublicError("文件里没有可提取的文本" + hint)
    if sum(len(section.text) for section in sections) > MAX_CHARS:
        raise PublicError("文档文本过长，请拆分后再上传")
    return name, sections


# ---- chunking ------------------------------------------------------------------------


def split_long(text: str, size: int) -> list[str]:
    """Sentences packed up to size; a single overlong sentence is cut hard."""
    parts: list[str] = []
    current = ""
    for sentence in SENTENCE.split(text):
        while len(sentence) > size:
            if current:
                parts.append(current)
                current = ""
            parts.append(sentence[:size])
            sentence = sentence[size:]
        if len(current) + len(sentence) > size:
            parts.append(current)
            current = ""
        current += sentence
    if current:
        parts.append(current)
    return [part for part in (piece.strip() for piece in parts) if part]


def pieces_of(text: str, size: int) -> list[str]:
    result: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if line:
            result += [line] if len(line) <= size else split_long(line, size)
    return result


def chunk_section(section: Section, size: int, overlap: int) -> list[str]:
    room = max(size - len(section.header) - 1, size // 2) if section.header else size
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for piece in pieces_of(section.text, room):
        if current and length + len(piece) + 1 > room:
            chunks.append("\n".join(current))
            # Rows stand alone behind the repeated header; prose overlaps for context.
            tail = chunks[-1][-overlap:].lstrip() if overlap and not section.header else ""
            current, length = ([tail], len(tail)) if tail else ([], 0)
        current.append(piece)
        length += len(piece) + 1
    if current:
        chunks.append("\n".join(current))
    return [section.header + "\n" + chunk if section.header else chunk for chunk in chunks]


def chunk_sections(sections: list[Section], size: int, overlap: int) -> list[tuple[str, str]]:
    """(label, text) chunks in document order; overlap stays inside a section."""
    return [
        (section.label[:200], text)
        for section in sections
        for text in chunk_section(section, size, overlap)
    ]
