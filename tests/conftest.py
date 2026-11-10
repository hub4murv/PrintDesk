import io

import pytest

import app as app_module
from app import app
from uploads import LocalDiskStorage

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


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


@pytest.fixture
def uploaded_file(temp_storage):
    """A design that is already in storage. Returns its file_id (storage key)."""
    key = "0123456789abcdef0123456789abcdef.png"
    temp_storage.save(io.BytesIO(PNG), key)
    temp_storage.save_json(key, {"original_filename": "design.png", "product": "business_cards"})
    return key
