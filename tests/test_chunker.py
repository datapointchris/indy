import json

import pytest

from indy.chunker import FILENAME_LANGUAGES
from indy.chunker import LANGUAGE_MAP
from indy.chunker import chunk_config
from indy.chunker import chunk_file
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
