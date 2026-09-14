"""上传内容校验测试：magic 头、解压炸弹、路径穿越、大小限制、清理。

这些是游客可达入口的安全防线（扩展名可伪造，文件头不可），
此前完全无测试。
"""
import io
import os
import zipfile

import pytest
from fastapi import UploadFile

from app.core.config import settings
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
