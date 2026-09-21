"""从 .doc / .docx / .pdf / .txt 文件中提取纯文本。"""
import os
import shutil
import subprocess
import tempfile

import pymupdf
from docx import Document as DocxDocument
from loguru import logger


def extract_text_from_file(file_path: str, file_ext: str) -> str:
    """
    从文件中提取纯文本

    :param file_path: 文件路径
    :param file_ext: 文件扩展名（含点，如 .docx）
    :return: 提取的文本内容
    """
    ext = file_ext.lower()
    if ext == ".txt":
        return _extract_txt(file_path)
    elif ext == ".doc":
        return _extract_doc(file_path)
    elif ext == ".docx":
        return _extract_docx(file_path)
    elif ext == ".pdf":
        return _extract_pdf(file_path)
    else:
        raise ValueError(f"不支持的文件格式: {ext}")


def _extract_txt(file_path: str) -> str:
    """提取 TXT 文件文本"""
    encodings = ["utf-8", "gbk", "gb2312", "utf-16", "latin-1"]
    for enc in encodings:
        try:
            with open(file_path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError("无法识别文件编码，请确保文件为 UTF-8 或 GBK 编码")


def _extract_doc(file_path: str) -> str:
    """
    提取旧版 .doc 格式 Word 文档文本
    Windows: 优先使用 Word/WPS COM 自动化转换
    Linux/Docker: 优先使用 antiword，其次 LibreOffice
    最后兜底尝试 python-docx 兼容模式
    """
    import platform

    # Linux 前置检查：如果没有 antiword 也没有 libreoffice，直接快速失败
    if platform.system() == "Linux":
        if not shutil.which("antiword") and not shutil.which("libreoffice") and not shutil.which("soffice"):
            raise ValueError(
                "服务器未安装 .doc 提取工具（antiword 或 LibreOffice），"
                "请联系管理员安装，或将文件另存为 .docx 格式后重新上传。"
            )

    # 方式0（Windows）：使用 Word/WPS COM 自动化转换为 docx 再提取
    if platform.system() == "Windows":
        try:
            import win32com.client
            abs_path = os.path.abspath(file_path)
            with tempfile.TemporaryDirectory() as tmp_dir:
                docx_path = os.path.join(tmp_dir, "converted.docx")
                word = win32com.client.Dispatch("Word.Application")
                word.Visible = False
                word.DisplayAlerts = False
                try:
                    doc = word.Documents.Open(abs_path)
                    # SaveAs2 格式 16 = wdFormatDocumentDefault (.docx)
                    doc.SaveAs2(os.path.abspath(docx_path), FileFormat=16)
                    doc.Close(False)
                finally:
                    word.Quit()
                if os.path.exists(docx_path):
                    logger.info("[doc提取] 使用 Word/WPS COM 转换成功")
                    return _extract_docx(docx_path)
        except ImportError:
            logger.warning("[doc提取] pywin32 未安装，跳过 COM 方式（pip install pywin32）")
        except Exception as e:
            logger.warning(f"[doc提取] Word/WPS COM 转换失败: {e}")

    # 方式1：使用 antiword 提取文本（轻量级，Docker 环境推荐）
    if shutil.which("antiword"):
        try:
            result = subprocess.run(
                ["antiword", "-m", "UTF-8", file_path],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                logger.info("[doc提取] 使用 antiword 成功提取文本")
                return result.stdout.strip()
            else:
                logger.warning(f"[doc提取] antiword 返回码={result.returncode} stderr={result.stderr[:200]}")
        except subprocess.TimeoutExpired:
            logger.warning("[doc提取] antiword 执行超时")
        except Exception as e:
            logger.warning(f"[doc提取] antiword 异常: {e}")

    # 方式2：使用 LibreOffice 转换为 docx 再提取
    if shutil.which("libreoffice") or shutil.which("soffice"):
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                lo_cmd = "libreoffice" if shutil.which("libreoffice") else "soffice"
                subprocess.run(
                    [lo_cmd, "--headless", "--convert-to", "docx", "--outdir", tmp_dir, file_path],
                    capture_output=True, timeout=60,
                )
                base_name = os.path.splitext(os.path.basename(file_path))[0]
                converted_path = os.path.join(tmp_dir, f"{base_name}.docx")
                if os.path.exists(converted_path):
                    logger.info("[doc提取] 使用 LibreOffice 转换成功")
                    return _extract_docx(converted_path)
        except Exception as e:
            logger.warning(f"[doc提取] LibreOffice 转换异常: {e}")

    # 方式3：尝试用 python-docx 直接打开（部分 .doc 文件实际是 XML 格式）
    try:
        logger.info("[doc提取] 尝试使用 python-docx 兼容模式")
        return _extract_docx(file_path)
    except Exception as e:
        logger.warning(f"[doc提取] python-docx 兼容模式失败: {e}")

    raise ValueError(
        "无法提取 .doc 文件内容。该文件可能是旧版 Word 二进制格式，"
        "建议使用 Word 或 WPS 将文件另存为 .docx 格式后重新上传。"
    )


def _extract_docx(file_path: str) -> str:
    """提取 Word 文档文本"""
    doc = DocxDocument(file_path)
    paragraphs = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _extract_pdf(file_path: str) -> str:
    """提取 PDF 文件文本"""
    with pymupdf.open(file_path) as doc:
        if doc.page_count > 100:
            raise ValueError(f"PDF 页数超过限制（{doc.page_count}页，最多100页）")

        text_parts = []
        for page_num in range(doc.page_count):
            page = doc.load_page(page_num)
            text = page.get_text("text")
            if text.strip():
                text_parts.append(text.strip())

    return "\n".join(text_parts)
