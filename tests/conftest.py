import pytest

import app as app_module
from app import app
from uploads import LocalDiskStorage


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def temp_storage(tmp_path, monkeypatch):
    """Store uploads in a temporary folder, so tests never touch real files."""
    storage = LocalDiskStorage(str(tmp_path), secret_key="test-secret")
    monkeypatch.setattr(app_module, "storage", storage)
    return storage
