"""
TextMirror 上传落盘与内容校验
Web 与开放 API 共用：分块落盘（不把整个文件读进内存）、按真实字节数限额、
magic 头与容器结构校验（扩展名可以伪造，文件头不行）、DOCX 解压炸弹防护。
"""
import os
import zipfile
from dataclasses import dataclass

from fastapi import UploadFile
from loguru import logger

from app.core.config import settings
from app.core.file_security import safe_upload_path

CHUNK_SIZE = 64 * 1024

# DOCX 解压防护阈值：正常办公文档远低于这些量级
_ZIP_MAX_MEMBERS = 2000
_ZIP_MAX_TOTAL_UNCOMPRESSED = 400 * 1024 * 1024
_ZIP_MAX_COMPRESSION_RATIO = 200
# OOXML 必需成员，缺失说明不是有效的 Word 文档
_DOCX_REQUIRED = ("[Content_Types].xml", "word/document.xml")


class UploadRejected(Exception):
    """上传内容不合法（调用方转 400，并附带 code 供开放 API 错误契约使用）"""

    def __init__(self, message: str, code: str = "INVALID_FILE"):
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class StoredUpload:
    """落盘结果"""
    file_path: str
    file_size: int


def remove_upload_silently(file_path: str) -> None:
    """清理已落盘文件及其 file_id 目录（上传目录按 file_id 隔离，属单次请求独有）"""
    if not file_path:
        return
    try:
        dir_path = os.path.dirname(file_path)
        if os.path.isfile(file_path):
            os.remove(file_path)
        part_path = f"{file_path}.part"
        if os.path.isfile(part_path):
            os.remove(part_path)
        if os.path.isdir(dir_path) and not os.listdir(dir_path):
            os.rmdir(dir_path)
    except OSError as e:
        logger.warning(f"[上传清理] 清理失败 {file_path}: {e}")


def remove_upload_dir(file_id: str) -> None:
    """
    删除整个 file_id 目录（原文件 + 修订件同在该目录）。
    remove_upload_silently 仅在目录为空时移除目录，不适用于删除场景。
    """
    import shutil

    if not file_id:
        return
    upload_dir = os.path.abspath(settings.UPLOAD_DIR)
    target = os.path.abspath(os.path.join(upload_dir, file_id))
    # 纵深防御：只允许删除上传根目录下的子目录
    if not target.startswith(upload_dir + os.sep) or target == upload_dir:
        logger.warning(f"[上传清理] 拒绝删除越界路径: {target}")
        return
    try:
        shutil.rmtree(target, ignore_errors=True)
    except OSError as e:
        logger.warning(f"[上传清理] 删除目录失败 {target}: {e}")


async def store_upload(file: UploadFile, file_id: str, filename: str, file_ext: str) -> StoredUpload:
    """
    分块落盘并校验内容，成功后原子改名为正式文件。
    任何失败都不会留下残留文件。
    """
    max_size = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    final_path = safe_upload_path(file_id, filename)
    part_path = f"{final_path}.part"
    os.makedirs(os.path.dirname(final_path), exist_ok=True)

    file_size = 0
    head = b""
    try:
        with open(part_path, "wb") as out:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                file_size += len(chunk)
                # 超限立即中止：不再继续读取或写入剩余内容
                if file_size > max_size:
                    raise UploadRejected(
                        f"文件大小超过限制（最大 {settings.MAX_UPLOAD_SIZE_MB}MB）",
                        code="FILE_TOO_LARGE",
                    )
                if len(head) < 8:
                    head += chunk[: 8 - len(head)]
                out.write(chunk)

        if file_size == 0:
            raise UploadRejected("文件内容为空")

        _validate_magic(file_ext, head)
        if file_ext == ".docx":
            _validate_docx_container(part_path)
        elif file_ext == ".txt":
            _validate_text_file(part_path)

        os.replace(part_path, final_path)
    except Exception:
        remove_upload_silently(final_path)
        raise
    finally:
        await file.close()

    return StoredUpload(file_path=final_path, file_size=file_size)


def _validate_magic(file_ext: str, head: bytes) -> None:
    """按扩展名校验文件头，阻断改名伪装（如把可执行文件改成 .docx）"""
    if file_ext == ".pdf":
        if not head.startswith(b"%PDF-"):
            raise UploadRejected("文件内容不是有效的 PDF")
    elif file_ext == ".docx":
        # docx 是 ZIP 容器
        if not head.startswith(b"PK\x03\x04"):
            raise UploadRejected("文件内容不是有效的 .docx（应为 ZIP 容器）")
    elif file_ext == ".doc":
        # 旧版 .doc 为 OLE 复合文档；部分 .doc 实际是 docx/XML，放行由解析层兜底
        if not (head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1") or head.startswith(b"PK\x03\x04")):
            raise UploadRejected("文件内容不是有效的 .doc")


def _validate_docx_container(path: str) -> None:
    """DOCX 结构与解压炸弹防护：只读中央目录，不解压内容"""
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if len(infos) > _ZIP_MAX_MEMBERS:
                raise UploadRejected("文档内部条目过多，疑似异常文件")

            names = set()
            total_uncompressed = 0
            for info in infos:
                name = info.filename
                # 路径穿越 / 绝对路径
                if name.startswith("/") or ".." in name.replace("\\", "/").split("/"):
                    raise UploadRejected("文档内部存在非法路径")
                if name in names:
                    raise UploadRejected("文档内部存在重复条目，疑似异常文件")
                names.add(name)

                total_uncompressed += info.file_size
                if total_uncompressed > _ZIP_MAX_TOTAL_UNCOMPRESSED:
                    raise UploadRejected("文档解压后体积过大，疑似压缩炸弹")
                if info.compress_size > 0:
                    ratio = info.file_size / info.compress_size
                    if ratio > _ZIP_MAX_COMPRESSION_RATIO and info.file_size > 1024 * 1024:
                        raise UploadRejected("文档压缩比异常，疑似压缩炸弹")

            missing = [m for m in _DOCX_REQUIRED if m not in names]
            if missing:
                raise UploadRejected("文档结构不完整，请确认是有效的 Word 文档")
    except zipfile.BadZipFile:
        raise UploadRejected("文档已损坏或不是有效的 .docx")


def _validate_text_file(path: str, probe_bytes: int = 64 * 1024) -> None:
    """TXT 拒绝二进制：含 NUL 字节的基本不是文本（编码识别交给解析层）"""
    with open(path, "rb") as f:
        probe = f.read(probe_bytes)
    if b"\x00" in probe:
        raise UploadRejected("文件内容不是有效的文本文件")
