import pytest

from indy import storage


@pytest.fixture(autouse=True)
def private_overlay(tmp_path, monkeypatch):
    """Every test gets an overlay path of its own, so a machine running `indy watch` cannot leak its overlay into a read."""
    path = tmp_path / 'state' / 'overlay.db'
    monkeypatch.setattr(storage, 'OVERLAY_PATH', path)
    return path
