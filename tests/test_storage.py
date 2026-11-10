"""Storage backends without touching the internet.

The Azure backend gets a fake BlobServiceClient, so we can check what it would
send to Azure and how it builds download links.
"""
import base64
import io
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

import uploads
from uploads import AzureBlobStorage, LocalDiskStorage, build_storage, is_valid_storage_key

VALID_KEY = "0123456789abcdef0123456789abcdef.pdf"


# ---------- Storage key format ----------

@pytest.mark.parametrize("value", [VALID_KEY, "ffffffffffffffffffffffffffffffff.tiff"])
def test_server_generated_keys_are_valid(value):
    assert is_valid_storage_key(value)


@pytest.mark.parametrize("value", [
    "../" + VALID_KEY,
    VALID_KEY + "/../app.py",
    "0123456789ABCDEF0123456789ABCDEF.pdf",  # uppercase is never generated
    "0123456789abcdef.pdf",                  # too short
    "0123456789abcdef0123456789abcdef.exe",
    "0123456789abcdef0123456789abcdef.pdf.json",
    "",
    None,
    ["x"],
])
def test_anything_else_is_not_a_storage_key(value):
    assert not is_valid_storage_key(value)


# ---------- Fakes for the Azure SDK ----------

class FakeBlobClient:
    def __init__(self, blobs, name):
        self._blobs, self._name = blobs, name

    def exists(self):
        return self._name in self._blobs


class FakeContainerClient:
    def __init__(self, url):
        self.url = url
        self.blobs = {}

    def upload_blob(self, name, data, overwrite=False):
        if name in self.blobs and not overwrite:
            raise RuntimeError("blob already exists")
        self.blobs[name] = data.read() if hasattr(data, "read") else data

    def get_blob_client(self, name):
        return FakeBlobClient(self.blobs, name)


class FakeDelegationKey:
    """Same fields as azure.storage.blob.UserDelegationKey."""

    def __init__(self, start, expiry):
        self.signed_oid = "00000000-0000-0000-0000-000000000001"
        self.signed_tid = "00000000-0000-0000-0000-000000000002"
        self.signed_start = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        self.signed_expiry = expiry.strftime("%Y-%m-%dT%H:%M:%SZ")
        self.signed_service = "b"
        self.signed_version = "2025-01-05"
        self.signed_delegated_user_tid = None
        self.value = base64.b64encode(b"fake-delegation-key-bytes-012345").decode()


class FakeServiceClient:
    account_name = "stprintdesktest"

    def __init__(self):
        self.container = FakeContainerClient("https://stprintdesktest.blob.core.windows.net/designs")
        self.delegation_requests = []

    def get_container_client(self, name):
        assert name == "designs"
        return self.container

    def get_user_delegation_key(self, start, expiry):
        self.delegation_requests.append((start, expiry))
        return FakeDelegationKey(start, expiry)


@pytest.fixture
def azure():
    service = FakeServiceClient()
    storage = AzureBlobStorage("https://stprintdesktest.blob.core.windows.net", "designs", service_client=service)
    return storage, service


# ---------- AzureBlobStorage ----------

def test_azure_save_and_metadata(azure):
    storage, service = azure
    storage.save(io.BytesIO(b"%PDF-1.7"), VALID_KEY)
    storage.save_json(VALID_KEY, {"original_filename": "design.pdf"})
    assert service.container.blobs[VALID_KEY] == b"%PDF-1.7"
    assert b"design.pdf" in service.container.blobs[VALID_KEY + ".json"]


def test_azure_never_overwrites_an_existing_design(azure):
    storage, _ = azure
    storage.save(io.BytesIO(b"first"), VALID_KEY)
    with pytest.raises(RuntimeError):
        storage.save(io.BytesIO(b"second"), VALID_KEY)


def test_azure_exists_checks_key_format_before_calling_azure(azure):
    storage, _ = azure
    storage.save(io.BytesIO(b"%PDF-1.7"), VALID_KEY)
    assert storage.exists(VALID_KEY)
    assert not storage.exists("ffffffffffffffffffffffffffffffff.pdf")
    assert not storage.exists("../" + VALID_KEY)


def test_azure_link_is_read_only_https_and_expires(azure):
    storage, _ = azure
    url = storage.signed_url(VALID_KEY, expires_in=3600)
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "stprintdesktest.blob.core.windows.net"
    assert parsed.path == f"/designs/{VALID_KEY}"
    assert query["sp"] == ["r"]          # read permission only
    assert query["spr"] == ["https"]     # HTTPS only
    assert query["sr"] == ["b"]          # one blob, not the whole container
    assert "sig" in query and "se" in query


def test_azure_link_lifetime_is_capped_at_seven_days(azure):
    storage, service = azure
    storage.signed_url(VALID_KEY, expires_in=30 * 24 * 3600)  # ask for 30 days
    start, expiry = service.delegation_requests[-1]
    assert (expiry - start).total_seconds() <= 7 * 24 * 3600


# ---------- build_storage ----------

class FakeApp:
    root_path = "."
    secret_key = "test"


def test_local_backend_is_the_default(monkeypatch, tmp_path):
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    assert isinstance(build_storage(FakeApp()), LocalDiskStorage)


def test_azure_backend_needs_an_account_url(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "azure")
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_URL", raising=False)
    with pytest.raises(ValueError, match="AZURE_STORAGE_ACCOUNT_URL"):
        build_storage(FakeApp())


def test_azure_backend_is_selected_by_setting(monkeypatch):
    created = {}

    class Recorder:
        def __init__(self, account_url, container):
            created.update(account_url=account_url, container=container)

    monkeypatch.setenv("STORAGE_BACKEND", "azure")
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_URL", "https://example.blob.core.windows.net")
    monkeypatch.delenv("AZURE_STORAGE_CONTAINER", raising=False)
    monkeypatch.setattr(uploads, "AzureBlobStorage", Recorder)
    build_storage(FakeApp())
    assert created == {"account_url": "https://example.blob.core.windows.net", "container": "designs"}


def test_unknown_backend_is_rejected(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "dropbox")
    with pytest.raises(ValueError):
        build_storage(FakeApp())
