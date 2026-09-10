"""Markdown parsing utilities."""
import logging
import re
from typing import Optional, Tuple

import anydoc

logger = logging.getLogger(__name__)


def convert_document_to_markdown(file_path: str, file_type: str) -> Tuple[str, Optional[str]]:
    """
    Convert a document to markdown.

    Args:
        file_path: Path to the document
        file_type: Type of document (pdf, docx, doc, txt, md)

    Returns:
        Tuple of (markdown content, title)
    """
    file_type = file_type.lower()

    if file_type in ('md', 'markdown'):
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        title = extract_title_from_markdown(content)
        return content, title

    if file_type == 'txt':
        return extract_text_fallback(file_path, file_type)

    try:
        markdown = anydoc.to_markdown(file_path)
    except anydoc.NeedsOcrError:
        # Image-only pages (scanned PDF): nothing to extract without an OCR
        # service. Empty content routes the upload to the 400 "Could not
        # extract text" path instead of a 500.
        logger.info("Document needs OCR (no text layer): %s", file_path)
        return "", None
    except Exception:
        # Malformed / encrypted / unsupported / resource-limit all degrade to
        # the text fallback ("" for binary types) so the caller answers 400,
        # never 500. anydoc errors are plain Exception subclasses, so this
        # catches everything it raises.
        logger.error("Document conversion failed for %s", file_path, exc_info=True)
        return extract_text_fallback(file_path, file_type)

    title = extract_title_from_markdown(markdown)
    return markdown, title


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
