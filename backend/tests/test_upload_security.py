"""上传内容校验测试：magic 头、解压炸弹、路径穿越、大小限制、清理。

这些是游客可达入口的安全防线（扩展名可伪造，文件头不可），
此前完全无测试。
"""
import io
import os
import zipfile

import pymupdf
import pytest
from fastapi import UploadFile

from app.core.config import settings
from app.services.document import extract_text_from_file
from app.services.upload import UploadRejected, remove_upload_dir, store_upload

FILE_ID = "test-upload-1"


def _upload(data: bytes, filename: str = "f.txt") -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(data))


async def _store(data: bytes, filename: str):
    ext = os.path.splitext(filename)[1]
    return await store_upload(_upload(data), FILE_ID, filename, ext)


def _make_docx(content: str = "测试文本") -> bytes:
    """最小合法 docx（OOXML 必需成员）"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", f"<w:document>{content}</w:document>")
    return buf.getvalue()


async def test_txt_stored_and_size_returned(client):
    stored = await _store("你好，世界。".encode("utf-8"), "a.txt")
    assert stored.file_size > 0
    assert os.path.isfile(stored.file_path)
    remove_upload_dir(FILE_ID)


async def test_empty_file_rejected(client):
    with pytest.raises(UploadRejected, match="内容为空"):
        await _store(b"", "a.txt")


async def test_oversize_rejected_immediately(client):
    big = b"x" * (settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024 + 1)
    with pytest.raises(UploadRejected, match="超过限制"):
        await _store(big, "a.txt")
    # 超限立即中止：不留残留
    assert not os.path.exists(os.path.join(settings.UPLOAD_DIR, FILE_ID))


async def test_pdf_magic_mismatch_rejected(client):
    # 可执行文件伪装成 .pdf
    with pytest.raises(UploadRejected, match="不是有效的 PDF"):
        await _store(b"MZ\x90\x00fake-exe", "a.pdf")


def _make_pdf(pages: list[str]) -> bytes:
    with pymupdf.open() as doc:
        for text in pages:
            page = doc.new_page()
            if text:
                page.insert_text((72, 72), text, fontname="china-s", fontsize=12)
        return doc.tobytes()


async def test_real_pdf_upload_extracts_ordered_chinese_pages(client):
    stored = await _store(_make_pdf(["第一页测试文本。", "", "第二页测试文本。"]), "chinese.pdf")
    try:
        assert extract_text_from_file(stored.file_path, ".pdf") == "第一页测试文本。\n第二页测试文本。"
    finally:
        remove_upload_dir(FILE_ID)


async def test_real_pdf_upload_api_returns_text_and_preview(client):
    response = await client.post(
        "/api/v1/document/upload",
        files={"file": ("chinese.pdf", _make_pdf(["中文审校测试。", "第二页内容。"]), "application/pdf")},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["extracted_text"] == "中文审校测试。\n第二页内容。"
    assert data["text_length"] == len(data["extracted_text"])
    assert "中文审校测试。" in data["extracted_html"]


@pytest.mark.parametrize(
    ("pages", "message"),
    [([""], "文件中未提取到有效文本内容"), (["测试文本。"] * 101, "PDF 页数超过限制（101页，最多100页）")],
)
async def test_pdf_upload_api_rejects_invalid_content(client, pages, message):
    response = await client.post(
        "/api/v1/document/upload", files={"file": ("invalid.pdf", _make_pdf(pages), "application/pdf")},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == message


def test_pdf_accepts_exactly_100_pages(tmp_path):
    path = tmp_path / "limit.pdf"
    path.write_bytes(_make_pdf(["测试文本。"] * 100))
    assert len(extract_text_from_file(str(path), ".pdf").splitlines()) == 100


def test_pdf_rejects_more_than_100_pages(tmp_path):
    path = tmp_path / "over-limit.pdf"
    path.write_bytes(_make_pdf(["测试文本。"] * 101))
    with pytest.raises(ValueError, match="PDF 页数超过限制（101页，最多100页）"):
        extract_text_from_file(str(path), ".pdf")


def test_blank_pdf_has_no_extractable_text(tmp_path):
    path = tmp_path / "blank.pdf"
    path.write_bytes(_make_pdf([""]))
    assert extract_text_from_file(str(path), ".pdf") == ""


def test_corrupt_pdf_rejected_by_parser(tmp_path):
    path = tmp_path / "corrupt.pdf"
    path.write_bytes(b"%PDF-1.7\nnot a PDF document")
    with pytest.raises(pymupdf.FileDataError):
        extract_text_from_file(str(path), ".pdf")


async def test_docx_not_zip_rejected(client):
    with pytest.raises(UploadRejected, match="不是有效的 .docx"):
        await _store(b"not-a-zip-at-all", "a.docx")


async def test_doc_rejects_arbitrary_binary(client):
    with pytest.raises(UploadRejected, match="不是有效的 .doc"):
        await _store(b"\x00\x01\x02garbage", "a.doc")


async def test_txt_with_nul_rejected(client):
    with pytest.raises(UploadRejected, match="不是有效的文本文件"):
        await _store(b"text\x00binary", "a.txt")


async def test_valid_docx_accepted(client):
    stored = await _store(_make_docx(), "a.docx")
    assert os.path.isfile(stored.file_path)
    remove_upload_dir(FILE_ID)


async def test_docx_missing_required_members_rejected(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("random.bin", "data")
    with pytest.raises(UploadRejected, match="文档结构不完整"):
        await _store(buf.getvalue(), "a.docx")


async def test_docx_path_traversal_rejected(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<doc/>")
        zf.writestr("../evil.sh", "rm -rf /")
    with pytest.raises(UploadRejected, match="非法路径"):
        await _store(buf.getvalue(), "a.docx")


async def test_docx_zip_bomb_ratio_rejected(client):
    """压缩比异常的解压炸弹：1MB+ 数据压缩后几字节"""
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<doc/>")
        zf.writestr("payload.bin", b"\0" * (2 * 1024 * 1024))  # 2MB 零字节
    with pytest.raises(UploadRejected, match="压缩炸弹"):
        await _store(bomb.getvalue(), "a.docx")


async def test_docx_too_many_members_rejected(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<doc/>")
        for i in range(2100):
            zf.writestr(f"filler/{i}.bin", "x")
    with pytest.raises(UploadRejected, match="条目过多"):
        await _store(buf.getvalue(), "a.docx")


async def test_failed_upload_leaves_no_partial_file(client):
    # 失败路径统一走 remove_upload_silently：无 .part 残留、无空目录
    with pytest.raises(UploadRejected):
        await _store(b"garbage-not-pdf", "a.pdf")
    assert not os.path.exists(os.path.join(settings.UPLOAD_DIR, FILE_ID))
