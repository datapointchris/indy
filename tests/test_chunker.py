import json

from indy.chunker import chunk_config
from indy.config import WHOLE_FILE_MAX_CHARS


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
