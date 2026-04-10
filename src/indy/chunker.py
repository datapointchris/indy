import ast
from dataclasses import dataclass
from pathlib import Path

from indy.config import CODE_CHUNK_OVERLAP
from indy.config import CODE_CHUNK_SIZE
from indy.config import CODE_EXTENSIONS
from indy.config import CONFIG_EXTENSIONS
from indy.config import DOC_EXTENSIONS
from indy.config import PROSE_CHUNK_OVERLAP
from indy.config import PROSE_CHUNK_SIZE

LANGUAGE_MAP = {
    '.py': 'python',
    '.go': 'go',
    '.js': 'javascript',
    '.ts': 'typescript',
    '.tsx': 'typescript',
    '.sh': 'bash',
    '.rs': 'rust',
    '.rb': 'ruby',
    '.md': 'markdown',
    '.rst': 'rst',
    '.txt': 'text',
    '.yaml': 'yaml',
    '.yml': 'yaml',
    '.toml': 'toml',
    '.json': 'json',
}


@dataclass
class Chunk:
    text: str
    file_path: str
    repo: str
    language: str | None
    symbol_name: str | None = None
    symbol_type: str | None = None  # "function" | "class" | "method" | "prose"
    module: str | None = None


def detect_language(file_path: str) -> str | None:
    return LANGUAGE_MAP.get(Path(file_path).suffix.lower())


def chunk_file(file_path: str, content: str, repo: str) -> list[Chunk]:
    """Dispatch to the appropriate chunking strategy based on file extension."""
    language = detect_language(file_path)
    ext = Path(file_path).suffix.lower()

    if ext == '.py':
        return chunk_python(file_path, content, repo)
    elif ext in DOC_EXTENSIONS:
        return chunk_prose(file_path, content, repo, language)
    elif ext in CONFIG_EXTENSIONS:
        return chunk_config(file_path, content, repo, language)
    elif ext in CODE_EXTENSIONS:
        return chunk_code(file_path, content, repo, language)
    else:
        return chunk_code(file_path, content, repo, language)


# ── Python AST chunker ────────────────────────────────────────────────────────


def _path_to_module(file_path: str) -> str | None:
    """Convert a file path to a Python dotted module path, best effort."""
    parts = list(Path(file_path).with_suffix('').parts)
    if 'src' in parts:
        return '.'.join(parts[parts.index('src') + 1 :])
    # Fall back to just the stem
    return Path(file_path).stem


def _extract_lines(lines: list[str], node: ast.AST) -> str:
    return '\n'.join(lines[node.lineno - 1 : node.end_lineno])  # type: ignore[attr-defined]


def _split_large_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
    file_path: str,
    repo: str,
    module: str | None,
    parent_name: str | None = None,
) -> list[Chunk]:
    """Break a function > 1500 chars into sub-chunks at nested function boundaries."""
    nested = [n for n in ast.walk(node) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n is not node]

    if not nested:
        # No nested functions — truncate to 1500 chars rather than losing the symbol entirely
        text = _extract_lines(lines, node)
        return [
            Chunk(
                text=text[:1500],
                file_path=file_path,
                repo=repo,
                language='python',
                symbol_name=f'{parent_name}.{node.name}' if parent_name else node.name,
                symbol_type='method' if parent_name else 'function',
                module=module,
            )
        ]

    chunks = []
    first_nested_line = min(n.lineno for n in nested)

    # Header slice: function signature + body before first nested def
    header_text = '\n'.join(lines[node.lineno - 1 : first_nested_line - 1])
    if header_text.strip():
        chunks.append(
            Chunk(
                text=header_text,
                file_path=file_path,
                repo=repo,
                language='python',
                symbol_name=f'{parent_name}.{node.name}' if parent_name else node.name,
                symbol_type='method' if parent_name else 'function',
                module=module,
            )
        )

    for nested_node in sorted(nested, key=lambda n: n.lineno):
        text = _extract_lines(lines, nested_node)
        chunks.append(
            Chunk(
                text=text,
                file_path=file_path,
                repo=repo,
                language='python',
                symbol_name=f'{node.name}.{nested_node.name}',
                symbol_type='function',
                module=module,
            )
        )

    return chunks


def _class_header_text(node: ast.ClassDef, lines: list[str]) -> str:
    """Return the class definition line + docstring + non-method body lines."""
    base_str = ', '.join(ast.unparse(b) for b in node.bases)
    header = [f'class {node.name}({base_str}):' if base_str else f'class {node.name}:']

    for item in node.body:
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        header.append(_extract_lines(lines, item))

    return '\n'.join(header)


def chunk_python(file_path: str, content: str, repo: str) -> list[Chunk]:
    """AST-based chunking: one chunk per top-level function / class method.
    Falls back to recursive code splitting if the file fails to parse."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return chunk_code(file_path, content, repo, language='python')

    lines = content.splitlines()
    module = _path_to_module(file_path)
    chunks: list[Chunk] = []

    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            text = _extract_lines(lines, node)
            if len(text) > 1500:
                chunks.extend(_split_large_function(node, lines, file_path, repo, module))
            else:
                chunks.append(
                    Chunk(
                        text=text,
                        file_path=file_path,
                        repo=repo,
                        language='python',
                        symbol_name=node.name,
                        symbol_type='function',
                        module=module,
                    )
                )

        elif isinstance(node, ast.ClassDef):
            # Class header chunk (docstring + class-level attributes, no methods)
            header = _class_header_text(node, lines)
            if header.strip():
                chunks.append(
                    Chunk(
                        text=header,
                        file_path=file_path,
                        repo=repo,
                        language='python',
                        symbol_name=node.name,
                        symbol_type='class',
                        module=module,
                    )
                )

            # One chunk per method
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    text = _extract_lines(lines, item)
                    if len(text) > 1500:
                        chunks.extend(_split_large_function(item, lines, file_path, repo, module, parent_name=node.name))
                    else:
                        chunks.append(
                            Chunk(
                                text=text,
                                file_path=file_path,
                                repo=repo,
                                language='python',
                                symbol_name=f'{node.name}.{item.name}',
                                symbol_type='method',
                                module=module,
                            )
                        )

    # If AST found nothing indexable, fall back to code splitter
    return chunks if chunks else chunk_code(file_path, content, repo, language='python')


# ── Recursive character splitter ─────────────────────────────────────────────

_CODE_DELIMITERS = ['\n\nfunc ', '\n\ndef ', '\nclass ', '\n\n', '\n', ' ']
_PROSE_DELIMITERS = ['\n## ', '\n### ', '\n#### ', '\n\n', '\n']


def _recursive_split(text: str, chunk_size: int, overlap: int, delimiters: list[str]) -> list[str]:
    """Split text using priority delimiters, targeting chunk_size with overlap between chunks."""
    if len(text) <= chunk_size:
        return [text] if text.strip() else []

    for i, delimiter in enumerate(delimiters):
        if delimiter not in text:
            continue

        splits = text.split(delimiter)
        # Reattach delimiter to all pieces after the first
        pieces = [splits[0]] + [delimiter + s for s in splits[1:]]

        merged: list[str] = []
        current = ''

        for piece in pieces:
            if current and len(current) + len(piece) > chunk_size:
                if current.strip():
                    merged.append(current)
                overlap_prefix = current[-overlap:] if overlap else ''
                current = overlap_prefix + piece
            else:
                current += piece

        if current.strip():
            merged.append(current)

        # Recurse on any merged chunk still over the limit
        result: list[str] = []
        remaining_delimiters = delimiters[i + 1 :]
        for chunk in merged:
            if len(chunk) > chunk_size and remaining_delimiters:
                result.extend(_recursive_split(chunk, chunk_size, overlap, remaining_delimiters))
            else:
                if chunk.strip():
                    result.append(chunk)
        return result

    # No delimiter matched — hard split with overlap
    result = []
    step = max(chunk_size - overlap, 1)
    for start in range(0, len(text), step):
        chunk = text[start : start + chunk_size]
        if chunk.strip():
            result.append(chunk)
    return result


def chunk_code(file_path: str, content: str, repo: str, language: str | None = None) -> list[Chunk]:
    """Recursive character splitter for non-Python code files."""
    pieces = _recursive_split(content, CODE_CHUNK_SIZE, CODE_CHUNK_OVERLAP, _CODE_DELIMITERS)
    return [Chunk(text=piece, file_path=file_path, repo=repo, language=language, symbol_type='prose') for piece in pieces]


def chunk_prose(file_path: str, content: str, repo: str, language: str | None) -> list[Chunk]:
    """Paragraph-aware chunker for Markdown and plain text."""
    pieces = _recursive_split(content, PROSE_CHUNK_SIZE, PROSE_CHUNK_OVERLAP, _PROSE_DELIMITERS)
    return [Chunk(text=piece, file_path=file_path, repo=repo, language=language, symbol_type='prose') for piece in pieces]


def chunk_config(file_path: str, content: str, repo: str, language: str | None) -> list[Chunk]:
    """Whole-file for small configs; code-split for large ones (> 300 lines)."""
    if len(content.splitlines()) <= 300:
        if content.strip():
            return [Chunk(text=content, file_path=file_path, repo=repo, language=language, symbol_type='prose')]
        return []
    return chunk_code(file_path, content, repo, language)
