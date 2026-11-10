"""
File upload validation + storage abstraction for PrintDesk.

Storage backends (STORAGE_BACKEND):
- "local" (default): files on the server's disk. Fine for local development and
  tests, but on Azure App Service the disk can be wiped on redeploy/restart.
- "azure": a private Azure Blob Storage container, accessed with the app's
  managed identity (no keys or connection strings anywhere). Staff get
  short-lived, read-only user delegation SAS links.

Still open:
- Antivirus scanning is a no-op stub (see scan_file). Every upload is unscanned.
"""

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import BinaryIO, Optional
from urllib.parse import quote

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB -- print files (esp. TIFF) run large

# Signature (magic-byte) check per allowed extension, hand-rolled instead of
# depending on python-magic/libmagic: python-magic segfaulted (access
# violation) on import on the Windows dev machine, and our whitelist is only
# six formats -- a few header bytes cover it more reliably than a native
# dependency that may not even load on the deploy target.
_SIGNATURES = {
    "pdf": [b"%PDF-"],
    "jpg": [b"\xff\xd8\xff"],
    "jpeg": [b"\xff\xd8\xff"],
    "png": [b"\x89PNG\r\n\x1a\n"],
    "tif": [b"II*\x00", b"MM\x00*"],
    "tiff": [b"II*\x00", b"MM\x00*"],
    # Modern Illustrator files are PDFs with extra data; legacy AI/EPS are
    # plain PostScript. DOS EPS wraps a PostScript body in a binary header.
    "ai": [b"%PDF-", b"%!PS-Adobe"],
    "eps": [b"%!PS-Adobe", b"\xc5\xd0\xd3\xc6"],
}

# Rejected outright even if the extension matches -- an archive renamed to a
# whitelisted extension is a classic spoofing / zip-bomb vector, and none of
# our allowed formats legitimately start this way.
_BLOCKED_SIGNATURES = [b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"]

ALLOWED_EXTENSIONS = set(_SIGNATURES.keys())

# Storage keys are generated server-side: 32 hex characters + an allowed extension.
_STORAGE_KEY_RE = re.compile(r"^[0-9a-f]{32}\.(%s)$" % "|".join(sorted(ALLOWED_EXTENSIONS)))


def is_valid_storage_key(value) -> bool:
    """True only for keys in the exact format the server creates.

    The browser sends the key back when placing an order, so it must never be
    trusted as a path (no "../", no other folders, no other file types).
    """
    return isinstance(value, str) and bool(_STORAGE_KEY_RE.fullmatch(value))
_SNIFF_BYTES = 16


class UploadRejected(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(message)


def _extension_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def validate_upload(filename: str, data: bytes) -> str:
    """Check extension + magic bytes. Returns the validated extension or raises UploadRejected."""
    ext = _extension_of(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise UploadRejected("Unsupported file type. Allowed: PDF, JPG, PNG, AI, EPS, TIFF.")

    header = data[:_SNIFF_BYTES]

    if any(header.startswith(sig) for sig in _BLOCKED_SIGNATURES):
        raise UploadRejected("This file's contents don't match a supported design format.")

    if not any(header.startswith(sig) for sig in _SIGNATURES[ext]):
        raise UploadRejected(
            f"This file's contents don't match a .{ext} file. "
            "Renaming a file won't help -- please upload the original design file."
        )

    return ext


@dataclass
class ScanResult:
    clean: bool
    engine: str


def scan_file(data: bytes) -> ScanResult:
    """Antivirus scan hook. NOT wired up yet -- no ClamAV/clamd available.
    Swap this body for a clamd/clamscan call before any of this touches a
    production server; until then, every upload is unscanned and callers
    must not claim otherwise.
    """
    logger.warning("scan_file: AV scanning not configured -- upload was NOT scanned")
    return ScanResult(clean=True, engine="none")


class Storage:
    def save(self, fileobj: BinaryIO, key: str) -> None:
        raise NotImplementedError

    def save_json(self, key: str, obj: dict) -> None:
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError

    def signed_url(self, key: str, expires_in: int = 3600) -> str:
        raise NotImplementedError


class LocalDiskStorage(Storage):
    """Prototype-only backend. Files live outside the Flask static/ web root
    and are never served directly -- retrieval always goes through the
    signed `/staff/uploads/<token>` route in app.py, the same shape as the
    SAS links of the Azure backend.
    """

    def __init__(self, root: str, secret_key: str):
        self.root = root
        os.makedirs(self.root, exist_ok=True)
        self._serializer = URLSafeTimedSerializer(secret_key, salt="upload-download")

    def _path_for(self, key: str) -> str:
        return os.path.join(self.root, key)

    def save(self, fileobj: BinaryIO, key: str) -> None:
        with open(self._path_for(key), "wb") as f:
            f.write(fileobj.read())

    def save_json(self, key: str, obj: dict) -> None:
        with open(self._path_for(key) + ".json", "w", encoding="utf-8") as f:
            json.dump(obj, f)

    def get_metadata(self, key: str) -> dict:
        path = self._path_for(key) + ".json"
        if not os.path.isfile(path):
            return {}
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def path_for(self, key: str) -> str:
        return self._path_for(key)

    def exists(self, key: str) -> bool:
        return is_valid_storage_key(key) and os.path.isfile(self._path_for(key))

    def signed_url(self, key: str, expires_in: int = 3600) -> str:
        token = self._serializer.dumps(key)
        return f"/staff/uploads/{token}"

    def resolve_token(self, token: str, max_age: int = 86400) -> Optional[str]:
        """Returns the storage key for a valid, unexpired token, else None."""
        try:
            key = self._serializer.loads(token, max_age=max_age)
        except (BadSignature, SignatureExpired):
            return None
        # Keys are generated server-side (uuid-based); reject anything else
        # defensively rather than trusting the decoded value blindly.
        if "/" in key or "\\" in key or ".." in key or not os.path.isfile(self._path_for(key)):
            return None
        return key


class AzureBlobStorage(Storage):
    """Private Azure Blob Storage container, accessed with Microsoft Entra ID.

    On App Service, DefaultAzureCredential picks up the app's managed identity,
    so there are no keys or connection strings in code or settings. Locally it
    falls back to your `az login` session (you'd need a Storage Blob Data role).

    Required roles for the identity:
    - Storage Blob Data Contributor on the container (read/write blobs)
    - Storage Blob Delegator on the storage account (create user delegation SAS links)
    """

    # A user delegation key (and any SAS signed with it) can live at most 7 days.
    MAX_LINK_SECONDS = 7 * 24 * 3600 - 15 * 60

    def __init__(self, account_url: str, container: str, credential=None, service_client=None):
        from azure.storage.blob import BlobServiceClient  # only needed for this backend

        if service_client is None:
            if credential is None:
                from azure.identity import DefaultAzureCredential
                credential = DefaultAzureCredential()
            service_client = BlobServiceClient(account_url=account_url, credential=credential)
        self._service = service_client
        self.container = container
        self._container = service_client.get_container_client(container)

    def save(self, fileobj: BinaryIO, key: str) -> None:
        self._container.upload_blob(name=key, data=fileobj, overwrite=False)

    def save_json(self, key: str, obj: dict) -> None:
        self._container.upload_blob(
            name=key + ".json",
            data=json.dumps(obj).encode("utf-8"),
            overwrite=True,
        )

    def exists(self, key: str) -> bool:
        return is_valid_storage_key(key) and self._container.get_blob_client(key).exists()

    def signed_url(self, key: str, expires_in: int = 3600) -> str:
        """Read-only link to one blob, signed with a user delegation key (no account key)."""
        from azure.storage.blob import BlobSasPermissions, generate_blob_sas

        now = datetime.now(timezone.utc)
        start = now - timedelta(minutes=5)  # tolerate small clock differences
        expiry = now + timedelta(seconds=min(expires_in, self.MAX_LINK_SECONDS))
        delegation_key = self._service.get_user_delegation_key(start, expiry)
        sas = generate_blob_sas(
            account_name=self._service.account_name,
            container_name=self.container,
            blob_name=key,
            user_delegation_key=delegation_key,
            permission=BlobSasPermissions(read=True),
            start=start,
            expiry=expiry,
            protocol="https",
        )
        return f"{self._container.url}/{quote(key)}?{sas}"


def build_storage(app) -> Storage:
    backend = os.environ.get("STORAGE_BACKEND", "local")
    if backend == "azure":
        account_url = os.environ.get("AZURE_STORAGE_ACCOUNT_URL")
        if not account_url:
            raise ValueError("STORAGE_BACKEND=azure needs AZURE_STORAGE_ACCOUNT_URL")
        container = os.environ.get("AZURE_STORAGE_CONTAINER", "designs")
        return AzureBlobStorage(account_url, container)
    if backend != "local":
        raise ValueError(f"Unknown STORAGE_BACKEND: {backend!r}")

    root = os.environ.get("UPLOAD_DIR") or os.path.join(app.root_path, "uploads_private")
    logger.warning(
        "STORAGE_BACKEND=local: uploads are stored on the server's disk. Use "
        "STORAGE_BACKEND=azure in production, the disk can be wiped on redeploy."
    )
    return LocalDiskStorage(root, secret_key=app.secret_key)
