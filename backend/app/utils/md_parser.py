"""Markdown parsing utilities."""
import logging
import re
from typing import Tuple, Optional

logger = logging.getLogger(__name__)


def convert_document_to_markdown(file_path: str, file_type: str) -> Tuple[str, Optional[str]]:
    """
    Convert a document to markdown.

    Args:
        file_path: Path to the document
        file_type: Type of document (pdf, docx, txt, md)

    Returns:
        Tuple of (markdown content, title)
    """
    file_type = file_type.lower()

    if file_type in ('md', 'markdown'):
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        title = extract_title_from_markdown(content)
        return content, title

    try:
        from markitdown import MarkItDown
        md = MarkItDown()
        result = md.convert(file_path)
        # markitdown versions differ: older returns a DocumentConverterResult with
        # .text_content and no .title; newer returns an object with .markdown
        # and .title. Be defensive about both shapes.
        text = getattr(result, "text_content", None) or getattr(result, "markdown", None) or ""
        title = getattr(result, "title", None)
        if not title and text:
            title = extract_title_from_markdown(text)
        return text, title
    except ImportError:
        logger.warning("markitdown not installed; using fallback text extractor")
        return extract_text_fallback(file_path, file_type)
    except Exception as e:
        logger.error("markitdown conversion failed for %s: %s", file_path, e, exc_info=True)
        return extract_text_fallback(file_path, file_type)


def extract_text_fallback(file_path: str, file_type: str) -> Tuple[str, Optional[str]]:
    """Fallback text extraction."""
    if file_type.lower() == 'txt':
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        return content, None
    else:
        # For unsupported types, return empty
        return "", None


def extract_title_from_markdown(markdown: str) -> Optional[str]:
    """Extract the first H1 title from markdown."""
    match = re.search(r'^#\s+(.+)$', markdown, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None


def clean_markdown(markdown: str) -> str:
    """Clean and normalize markdown content."""
    # Remove excessive whitespace
    cleaned = re.sub(r'\n{3,}', '\n\n', markdown)
    # Normalize line endings
    cleaned = cleaned.replace('\r\n', '\n')
    return cleaned.strip()
