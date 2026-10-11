import ast
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

try:
    import tree_sitter_go
    import tree_sitter_lua
    import tree_sitter_rust
    import tree_sitter_typescript
    from tree_sitter import Language
    from tree_sitter import Node
    from tree_sitter import Parser

    # Each entry builds a parser, so no tree-sitter name is read outside this branch.
    TS_PARSERS: dict[str, Callable[[], Parser]] = {
        'go': partial(Parser, Language(tree_sitter_go.language())),
        'lua': partial(Parser, Language(tree_sitter_lua.language())),
        'rust': partial(Parser, Language(tree_sitter_rust.language())),
        'typescript': partial(Parser, Language(tree_sitter_typescript.language_typescript())),
        'tsx': partial(Parser, Language(tree_sitter_typescript.language_tsx())),
    }
except ImportError:
    # An empty table is the whole fallback: every lookup misses and sends the
    # file to chunk_code. A separate availability flag cannot narrow the names
    # this branch leaves unbound, which is how the NameError hid here.
    TS_PARSERS = {}

from indy.config import CODE_CHUNK_OVERLAP
from indy.config import CODE_CHUNK_SIZE
from indy.config import CODE_EXTENSIONS
from indy.config import CONFIG_EXTENSIONS
from indy.config import DOC_EXTENSIONS
from indy.config import PROSE_CHUNK_OVERLAP
from indy.config import PROSE_CHUNK_SIZE
from indy.config import WHOLE_FILE_MAX_CHARS

LANGUAGE_MAP = {
    '.py': 'python',
    '.go': 'go',
    '.js': 'javascript',
    '.mjs': 'javascript',
    '.cjs': 'javascript',
    '.ts': 'typescript',
    '.mts': 'typescript',
    '.cts': 'typescript',
    '.tsx': 'typescript',
    '.vue': 'vue',
    '.astro': 'astro',
    '.html': 'html',
    '.css': 'css',
    '.scss': 'scss',
    '.sh': 'bash',
    '.bash': 'bash',
    '.zsh': 'zsh',
    '.bats': 'bash',
    '.rs': 'rust',
    '.rb': 'ruby',
    '.lua': 'lua',
    '.tf': 'terraform',
    '.tfvars': 'terraform',
    '.hcl': 'hcl',
    '.sql': 'sql',
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


@dataclass
class Reference:
    source_file: str
    source_symbol: str | None  # None = module-level (imports, class-level code)
    target_symbol: str
    target_module: str | None  # best-effort: the object/module the symbol is called on
    ref_type: str  # "call" | "import" | "inherit"


FILENAME_LANGUAGES = {'Dockerfile': 'dockerfile'}

# Keyed by the program a shebang runs, with any version suffix dropped. `uv` is
# here because `#!/usr/bin/env -S uv run --script` is how an inline-dependency
# Python script starts.
SHEBANG_LANGUAGES = {'sh': 'bash', 'bash': 'bash', 'zsh': 'zsh', 'python': 'python', 'uv': 'python'}


def shebang_language(first_line: str) -> str | None:
    """The language a `#!` line runs, reaching past `env` and its flags to the program."""
    if not first_line.startswith('#!'):
        return None
    words = first_line[2:].split()
    if words and Path(words[0]).name == 'env':
        words = [word for word in words[1:] if not word.startswith('-') and '=' not in word]
    if not words:
        return None
    return SHEBANG_LANGUAGES.get(Path(words[0]).name.rstrip('0123456789.'))


def detect_language(file_path: str, content: str) -> str | None:
    """By suffix, else by a name like `Dockerfile`, else by a suffix-less script's shebang."""
    path = Path(file_path)
    if path.suffix:
        return LANGUAGE_MAP.get(path.suffix.lower())
    return FILENAME_LANGUAGES.get(path.name) or shebang_language(content.partition('\n')[0])


TS_EXTENSIONS = {'.go', '.ts', '.mts', '.cts', '.tsx', '.rs', '.lua'}


def chunk_file(file_path: str, content: str, repo: str) -> list[Chunk]:
    """Dispatch to the appropriate chunking strategy based on file extension."""
    language = detect_language(file_path, content)
    ext = Path(file_path).suffix.lower()

    if language == 'python':
        return chunk_python(file_path, content, repo)
    elif ext in TS_EXTENSIONS:
        return chunk_treesitter(file_path, content, repo, language)
    elif ext in DOC_EXTENSIONS:
        return chunk_prose(file_path, content, repo, language)
    elif ext in CONFIG_EXTENSIONS:
        return chunk_config(file_path, content, repo, language)
    elif ext in CODE_EXTENSIONS:
        return chunk_code(file_path, content, repo, language)
    else:
        return chunk_code(file_path, content, repo, language)


# ── Python AST chunker ────────────────────────────────────────────────────────


def path_to_module(file_path: str) -> str | None:
    """Convert a file path to a Python dotted module path, best effort."""
    parts = list(Path(file_path).with_suffix('').parts)
    if 'src' in parts:
        return '.'.join(parts[parts.index('src') + 1 :])
    # Fall back to just the stem
    return Path(file_path).stem


def extract_lines(lines: list[str], node: ast.AST) -> str:
    return '\n'.join(lines[node.lineno - 1 : node.end_lineno])  # type: ignore[attr-defined]


def split_large_function(
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
        text = extract_lines(lines, node)
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
        text = extract_lines(lines, nested_node)
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


def class_header_text(node: ast.ClassDef, lines: list[str]) -> str:
    """Return the class definition line + docstring + non-method body lines."""
    base_str = ', '.join(ast.unparse(b) for b in node.bases)
    header = [f'class {node.name}({base_str}):' if base_str else f'class {node.name}:']

    for item in node.body:
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        header.append(extract_lines(lines, item))

    return '\n'.join(header)


def chunk_python(file_path: str, content: str, repo: str) -> list[Chunk]:
    """AST-based chunking: one chunk per top-level function / class method.
    Falls back to recursive code splitting if the file fails to parse."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return chunk_code(file_path, content, repo, language='python')

    lines = content.splitlines()
    module = path_to_module(file_path)
    chunks: list[Chunk] = []

    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            text = extract_lines(lines, node)
            if len(text) > 1500:
                chunks.extend(split_large_function(node, lines, file_path, repo, module))
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
            header = class_header_text(node, lines)
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
                    text = extract_lines(lines, item)
                    if len(text) > 1500:
                        chunks.extend(split_large_function(item, lines, file_path, repo, module, parent_name=node.name))
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

CODE_DELIMITERS = ['\n\nfunc ', '\n\ndef ', '\nclass ', '\n\n', '\n', ' ']
PROSE_DELIMITERS = ['\n## ', '\n### ', '\n#### ', '\n\n', '\n']


def recursive_split(text: str, chunk_size: int, overlap: int, delimiters: list[str]) -> list[str]:
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
                result.extend(recursive_split(chunk, chunk_size, overlap, remaining_delimiters))
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
    pieces = recursive_split(content, CODE_CHUNK_SIZE, CODE_CHUNK_OVERLAP, CODE_DELIMITERS)
    return [Chunk(text=piece, file_path=file_path, repo=repo, language=language, symbol_type='prose') for piece in pieces]


def chunk_prose(file_path: str, content: str, repo: str, language: str | None) -> list[Chunk]:
    """Paragraph-aware chunker for Markdown and plain text."""
    pieces = recursive_split(content, PROSE_CHUNK_SIZE, PROSE_CHUNK_OVERLAP, PROSE_DELIMITERS)
    return [Chunk(text=piece, file_path=file_path, repo=repo, language=language, symbol_type='prose') for piece in pieces]


def chunk_config(file_path: str, content: str, repo: str, language: str | None) -> list[Chunk]:
    """Whole-file for small configs; code-split for large ones.

    Line count alone lets a single-line file through at any size — minified JSON and
    tokei output are one line and tens of kilobytes, which the embedding model rejects
    outright. Every other chunker caps its output, so this branch is the only one that
    can emit an oversized chunk.
    """
    if len(content.splitlines()) <= 300 and len(content) <= WHOLE_FILE_MAX_CHARS:
        if content.strip():
            return [Chunk(text=content, file_path=file_path, repo=repo, language=language, symbol_type='prose')]
        return []
    return chunk_code(file_path, content, repo, language)


# ── tree-sitter chunker ───────────────────────────────────────────────────────

# Node types that become individual chunks. When a match is found, recursion
# stops so nested functions aren't double-chunked.
TS_EXTRACT_TYPES: dict[str, set[str]] = {
    'go': {'function_declaration', 'method_declaration'},
    'typescript': {'function_declaration', 'method_definition'},
    'tsx': {'function_declaration', 'method_definition'},
    'rust': {'function_item'},
    # `local function f`, `function M.f` and `function M:f`. A function assigned
    # as a value, `M.f = function() end`, has no name to chunk it under.
    'lua': {'function_declaration'},
}

TS_SYMBOL_TYPE_MAP: dict[str, str] = {
    'function_declaration': 'function',
    'method_declaration': 'method',
    'method_definition': 'method',
    'function_item': 'function',
}


def ts_symbol_name(node: 'Node') -> str | None:
    name_node = node.child_by_field_name('name')
    if name_node is None or name_node.text is None:
        return None
    return name_node.text.decode('utf-8')


def ts_walk(
    node: 'Node',
    lines: list[str],
    file_path: str,
    repo: str,
    language: str | None,
    extract_types: set[str],
    chunks: list[Chunk],
) -> None:
    if node.type in extract_types:
        start = node.start_point[0]
        end = node.end_point[0] + 1
        text = '\n'.join(lines[start:end])
        if len(text) > 1500:
            # Large symbol — fall back to code splitter for this node only
            chunks.extend(chunk_code(file_path, text, repo, language))
        else:
            chunks.append(
                Chunk(
                    text=text,
                    file_path=file_path,
                    repo=repo,
                    language=language,
                    symbol_name=ts_symbol_name(node),
                    symbol_type=TS_SYMBOL_TYPE_MAP.get(node.type, 'function'),
                )
            )
        return  # don't recurse — avoids double-chunking nested definitions

    for child in node.children:
        if child.is_named:
            ts_walk(child, lines, file_path, repo, language, extract_types, chunks)


def chunk_treesitter(file_path: str, content: str, repo: str, language: str | None) -> list[Chunk]:
    """AST chunking via tree-sitter for Go, TypeScript, Rust, and Lua.
    Falls back to recursive code splitter if tree-sitter packages are not installed."""
    # .tsx files share the 'typescript' language label but need the tsx grammar
    lang_key = 'tsx' if file_path.endswith('.tsx') else language
    new_parser = TS_PARSERS.get(lang_key or '')
    if new_parser is None:
        return chunk_code(file_path, content, repo, language)

    extract_types = TS_EXTRACT_TYPES.get(lang_key or '', set())
    parser = new_parser()
    tree = parser.parse(bytes(content, 'utf-8'))

    chunks: list[Chunk] = []
    ts_walk(tree.root_node, content.splitlines(), file_path, repo, language, extract_types, chunks)

    return chunks if chunks else chunk_code(file_path, content, repo, language)


# ── Reference extraction ──────────────────────────────────────────────────────


def resolve_attr_name(node: ast.expr) -> tuple[str, str | None]:
    """Return (symbol_name, module_hint) for a Call's func node.

    For `storage.search_chunks(...)` → ("search_chunks", "storage").
    For `get_db()`                  → ("get_db", None).
    For chained `a.b.c()`          → ("c", "b").
    Returns ("", None) when the expression is not resolvable (e.g. lambdas).
    """
    if isinstance(node, ast.Name):
        return node.id, None
    if isinstance(node, ast.Attribute):
        module_hint = node.value.id if isinstance(node.value, ast.Name) else None
        return node.attr, module_hint
    return '', None


class ReferenceExtractor(ast.NodeVisitor):
    """Walk a Python AST and collect symbol references with caller context."""

    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self.refs: list[Reference] = []
        self._context: list[str] = []  # class/function name stack

    @property
    def _current_symbol(self) -> str | None:
        return '.'.join(self._context) if self._context else None

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.refs.append(
                Reference(
                    source_file=self.file_path,
                    source_symbol=self._current_symbol,
                    target_symbol=alias.asname or alias.name,
                    target_module=None,
                    ref_type='import',
                )
            )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ''
        for alias in node.names:
            self.refs.append(
                Reference(
                    source_file=self.file_path,
                    source_symbol=self._current_symbol,
                    target_symbol=alias.asname or alias.name,
                    target_module=module,
                    ref_type='import',
                )
            )
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for base in node.bases:
            name, module_hint = resolve_attr_name(base)
            if name:
                self.refs.append(
                    Reference(
                        source_file=self.file_path,
                        source_symbol=self._current_symbol,
                        target_symbol=name,
                        target_module=module_hint,
                        ref_type='inherit',
                    )
                )
        self._context.append(node.name)
        self.generic_visit(node)
        self._context.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._context.append(node.name)
        self.generic_visit(node)
        self._context.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Call(self, node: ast.Call) -> None:
        name, module_hint = resolve_attr_name(node.func)
        if name:
            self.refs.append(
                Reference(
                    source_file=self.file_path,
                    source_symbol=self._current_symbol,
                    target_symbol=name,
                    target_module=module_hint,
                    ref_type='call',
                )
            )
        self.generic_visit(node)


def extract_references(file_path: str, content: str) -> list[Reference]:
    """Extract symbol references from a Python file.
    Returns an empty list for non-Python files or files that fail to parse."""
    if not file_path.endswith('.py'):
        return []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []
    extractor = ReferenceExtractor(file_path)
    extractor.visit(tree)
    return extractor.refs
