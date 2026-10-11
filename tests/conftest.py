import pytest

from indy import scip
from indy import storage


@pytest.fixture(autouse=True)
def private_overlay_dir(tmp_path, monkeypatch):
    """Every test writes its overlays and its scip-python install under its own directory rather than this machine's cache."""
    path = tmp_path / 'cache' / 'overlays'
    monkeypatch.setattr(storage, 'OVERLAY_DIR', path)
    monkeypatch.setattr(scip, 'SCIP_PYTHON_DIR', tmp_path / 'cache' / 'scip-python')
    return path
