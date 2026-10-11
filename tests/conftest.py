import pytest

from indy import storage


@pytest.fixture(autouse=True)
def private_overlay_dir(tmp_path, monkeypatch):
    """Every test writes its overlays under its own directory rather than this machine's cache."""
    path = tmp_path / 'cache' / 'overlays'
    monkeypatch.setattr(storage, 'OVERLAY_DIR', path)
    return path
