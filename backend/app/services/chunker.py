"""Markdown chunking service with hierarchy preservation."""
import re
import uuid
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class ChunkHierarchy:
    """Hierarchy information for a chunk."""
    level: int
    path: List[str]
    parent_id: Optional[str] = None


@dataclass
class ChunkPosition:
    """Position information for a chunk."""
    start_line: int
    end_line: int
    prev_chunk_id: Optional[str] = None
    next_chunk_id: Optional[str] = None


@dataclass
class Chunk:
    """A document chunk with metadata."""
    chunk_id: str
    document_id: str
    user_id: int
    content: str
    hierarchy: ChunkHierarchy
    position: ChunkPosition
    metadata: Dict[str, Any] = field(default_factory=dict)


class MarkdownChunker:
    """Chunk markdown documents by hierarchy."""

    def __init__(self, max_chunk_size: int = 500, min_chunk_size: int = 200):
        self.max_chunk_size = max_chunk_size
        self.min_chunk_size = min_chunk_size

    def _parse_headers(self, markdown: str) -> List[Tuple[int, str, int]]:
        """Parse all headers from markdown, returns list of (level, title, line_num)."""
        headers = []
        lines = markdown.split('\n')

        for line_num, line in enumerate(lines):
            # Match ATX headers (# ## ###)
            match = re.match(r'^(#{1,6})\s+(.+)$', line.strip())
            if match:
                level = len(match.group(1))
                title = match.group(2).strip()
                headers.append((level, title, line_num))

        return headers

    def _get_header_level(self, line: str) -> Tuple[Optional[int], Optional[str]]:
        """Get header level and title from a line."""
        match = re.match(r'^(#{1,6})\s+(.+)$', line.strip())
        if match:
            return len(match.group(1)), match.group(2).strip()
        return None, None

    def _extract_section_content(
        self,
        lines: List[str],
        start_line: int,
        end_line: int
    ) -> str:
        """Extract content between two line numbers."""
        content_lines = lines[start_line:end_line]
        return '\n'.join(content_lines).strip()

    def _split_oversized_chunk(self, content: str, chunk_id_prefix: str) -> List[str]:
        """Split oversized content into smaller chunks."""
        if len(content) <= self.max_chunk_size:
            return [content]

        chunks = []
        # Try to split by paragraphs first
        paragraphs = re.split(r'\n\s*\n', content)

        current_chunk = ""
        for para in paragraphs:
            if len(current_chunk) + len(para) + 2 <= self.max_chunk_size:
                if current_chunk:
                    current_chunk += "\n\n" + para
                else:
                    current_chunk = para
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                # If single paragraph is too long, split by sentences
                if len(para) > self.max_chunk_size:
                    sentences = re.split(r'(?<=[.!?。！？])\s+', para)
                    current_chunk = ""
                    for sent in sentences:
                        if len(current_chunk) + len(sent) + 1 <= self.max_chunk_size:
                            if current_chunk:
                                current_chunk += " " + sent
                            else:
                                current_chunk = sent
                        else:
                            if current_chunk:
                                chunks.append(current_chunk)
                            current_chunk = sent
                else:
                    current_chunk = para

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    def chunk_document(
        self,
        document_id: str,
        user_id: int,
        markdown: str,
        source_file: str = ""
    ) -> List[Chunk]:
        """
        Chunk a markdown document by hierarchy.

        Args:
            document_id: Unique document ID
            user_id: User ID for isolation
            markdown: Markdown content
            source_file: Source file name

        Returns:
            List of chunks with hierarchy information
        """
        lines = markdown.split('\n')
        headers = self._parse_headers(markdown)

        if not headers:
            # No headers, treat entire document as one chunk
            content = markdown.strip()
            if not content:
                return []

            chunk_id = str(uuid.uuid4())
            chunks = self._split_oversized_chunk(content, chunk_id)

            result = []
            for i, chunk_content in enumerate(chunks):
                result.append(Chunk(
                    chunk_id=f"{chunk_id}_{i}" if len(chunks) > 1 else chunk_id,
                    document_id=document_id,
                    user_id=user_id,
                    content=chunk_content,
                    hierarchy=ChunkHierarchy(level=0, path=["Document"]),
                    position=ChunkPosition(
                        start_line=0,
                        end_line=len(lines) - 1,
                        prev_chunk_id=result[-1].chunk_id if result else None,
                        next_chunk_id=None
                    ),
                    metadata={"source_file": source_file}
                ))

            # Fix next_chunk_id references
            for i in range(len(result) - 1):
                result[i].position.next_chunk_id = result[i + 1].chunk_id

            return result

        # Build hierarchy tree
        chunks = []
        header_stack = []  # Stack of (level, title, chunk_id)

        for i, (level, title, line_num) in enumerate(headers):
            # Determine section end
            if i + 1 < len(headers):
                end_line = headers[i + 1][2]
            else:
                end_line = len(lines)

            # Build hierarchy path
            while header_stack and header_stack[-1][0] >= level:
                header_stack.pop()

            hierarchy_path = [item[1] for item in header_stack] + [title]
            parent_id = header_stack[-1][2] if header_stack else None

            # Extract content
            content = self._extract_section_content(lines, line_num, end_line)

            if content:
                # Create chunk for this section
                chunk_id = str(uuid.uuid4())

                # If content is too large, split it
                content_chunks = self._split_oversized_chunk(content, chunk_id)

                for j, chunk_content in enumerate(content_chunks):
                    if j == 0:
                        chunk_hierarchy = ChunkHierarchy(
                            level=level,
                            path=hierarchy_path,
                            parent_id=parent_id
                        )
                    else:
                        # Sub-chunks maintain same path but with part indicator
                        chunk_hierarchy = ChunkHierarchy(
                            level=level,
                            path=hierarchy_path + [f"[Part {j + 1}]"],
                            parent_id=chunk_id if j == 1 else f"{chunk_id}_{j - 1}"
                        )

                    chunk = Chunk(
                        chunk_id=f"{chunk_id}_{j}" if j > 0 else chunk_id,
                        document_id=document_id,
                        user_id=user_id,
                        content=chunk_content,
                        hierarchy=chunk_hierarchy,
                        position=ChunkPosition(
                            start_line=line_num,
                            end_line=end_line - 1,
                            prev_chunk_id=chunks[-1].chunk_id if chunks else None,
                            next_chunk_id=None
                        ),
                        metadata={
                            "source_file": source_file,
                            "is_sub_chunk": j > 0
                        }
                    )

                    if chunks:
                        chunks[-1].position.next_chunk_id = chunk.chunk_id

                    chunks.append(chunk)

            # Push current header to stack
            header_stack.append((level, title, chunks[-1].chunk_id if chunks else None))

        # Handle content before first header
        if headers and headers[0][2] > 0:
            intro_content = self._extract_section_content(lines, 0, headers[0][2])
            if intro_content.strip():
                chunk_id = str(uuid.uuid4())
                intro_chunk = Chunk(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    user_id=user_id,
                    content=intro_content.strip(),
                    hierarchy=ChunkHierarchy(level=0, path=["Introduction"]),
                    position=ChunkPosition(
                        start_line=0,
                        end_line=headers[0][2] - 1,
                        prev_chunk_id=None,
                        next_chunk_id=chunks[0].chunk_id if chunks else None
                    ),
                    metadata={"source_file": source_file, "is_intro": True}
                )
                if chunks:
                    chunks[0].position.prev_chunk_id = chunk_id
                chunks.insert(0, intro_chunk)

        return chunks


def chunk_markdown(
    markdown: str,
    document_id: str,
    user_id: int,
    source_file: str = ""
) -> List[Chunk]:
    """Convenience function to chunk markdown document."""
    chunker = MarkdownChunker()
    return chunker.chunk_document(document_id, user_id, markdown, source_file)
