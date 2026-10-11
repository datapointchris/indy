import json

import pytest

from indy.chunker import FILENAME_LANGUAGES
from indy.chunker import LANGUAGE_MAP
from indy.chunker import chunk_config
from indy.chunker import chunk_file
from indy.chunker import extract_references
from indy.chunker import shebang_language
from indy.config import CODE_EXTENSIONS
from indy.config import CODE_FILENAMES
from indy.config import CONFIG_EXTENSIONS
from indy.config import DOC_EXTENSIONS
from indy.config import WHOLE_FILE_MAX_CHARS


def test_every_indexed_suffix_and_name_has_a_language():
    """A file selected for indexing with no entry here is stored with no language, and `--language` never finds it."""
    assert set(LANGUAGE_MAP) == CODE_EXTENSIONS | DOC_EXTENSIONS | CONFIG_EXTENSIONS
    assert set(FILENAME_LANGUAGES) == CODE_FILENAMES


@pytest.mark.parametrize(
    ('line', 'language'),
    [
        ('#!/usr/bin/env bash\n', 'bash'),
        ('#!/bin/bash -e\n', 'bash'),
        ('#!/bin/sh\n', 'bash'),
        ('#!/usr/bin/env python3\n', 'python'),
        ('#!/usr/bin/python3.13\n', 'python'),
        ('#!/usr/bin/env -S uv run --script\n', 'python'),
        ('#!/usr/bin/env node\n', None),
        ('# not a shebang\n', None),
        ('', None),
    ],
)
def test_shebang_names_the_language_past_env_and_its_flags(line, language):
    assert shebang_language(line) == language


def test_a_suffix_less_python_script_is_chunked_by_its_functions():
    chunks = chunk_file('bin/tool', '#!/usr/bin/env python3\ndef main():\n    return 1\n', 'r')
    assert [(chunk.symbol_name, chunk.language) for chunk in chunks] == [('main', 'python')]


def test_lua_is_chunked_by_its_named_functions():
    content = 'local M = {}\nlocal function helper(x)\n  return x\nend\nfunction M.setup(opts)\n  M.opts = opts\nend\nreturn M\n'
    chunks = chunk_file('lua/plugin/init.lua', content, 'r')
    assert [(chunk.symbol_name, chunk.language) for chunk in chunks] == [('helper', 'lua'), ('M.setup', 'lua')]


def test_single_line_json_is_split_rather_than_kept_whole():
    """The regression: a one-line file passed the line check at any size.

    Shaped like real minified output rather than a repeated character, so the
    recursive splitter has the delimiters it would actually see.
    """
    content = json.dumps({f'file_{i}.py': {'code': i, 'comments': i * 2} for i in range(2000)})
    assert len(content.splitlines()) == 1
    assert len(content) > WHOLE_FILE_MAX_CHARS

    chunks = chunk_config('tokei.json', content, 'indy', 'json')

    assert len(chunks) > 1
    assert all(len(chunk.text) <= WHOLE_FILE_MAX_CHARS for chunk in chunks)


def test_small_config_is_still_kept_whole():
    content = 'name: indy\nversion: 1\n'
    chunks = chunk_config('config.yml', content, 'indy', 'yaml')
    assert len(chunks) == 1
    assert chunks[0].text == content


def test_empty_config_produces_nothing():
    assert chunk_config('empty.yml', '   \n\n', 'indy', 'yaml') == []


def test_a_name_used_as_a_value_is_a_reference_row():
    """A function placed in a dispatch table is never called by name, so a value is its only trace.

    An annotation's type names are not values, and the call inside one still runs.
    """
    content = (
        'def wire(param: Annotated[Kind, option()]):\n'
        '    local = make()\n'
        '    register(handler, name=local)\n'
        '    on_event = lambda event: event\n'
        '    return [handler, param, local, len, on_event]\n'
    )
    refs = [(ref.target_symbol, ref.ref_type) for ref in extract_references('m.py', content)]
    assert sorted(refs) == [('handler', 'reference'), ('handler', 'reference'), ('make', 'call'), ('option', 'call'), ('register', 'call')]


def test_module_level_code_is_attributed_to_its_assignment_target():
    content = "STEPS: dict = {'wslconfig': (anywhere(check), unprivileged(write_wslconfig))}\nDEFAULT = make()\nHANDLERS[key()] = handler\n"
    refs = {(ref.source_symbol, ref.target_symbol, ref.ref_type) for ref in extract_references('m.py', content)}
    assert refs == {
        ("STEPS['wslconfig']", 'anywhere', 'call'),
        ("STEPS['wslconfig']", 'check', 'reference'),
        ("STEPS['wslconfig']", 'unprivileged', 'call'),
        ("STEPS['wslconfig']", 'write_wslconfig', 'reference'),
        ('DEFAULT', 'make', 'call'),
        (None, 'HANDLERS', 'reference'),
        (None, 'key', 'call'),
        (None, 'handler', 'reference'),
    }


def test_an_inherit_row_names_the_subclass():
    refs = extract_references('m.py', 'class Base:\n    pass\n\n\nclass Child(Base):\n    pass\n')
    assert [(ref.source_symbol, ref.target_symbol) for ref in refs if ref.ref_type == 'inherit'] == [('Child', 'Base')]
