"""
File upload validation + storage abstraction for PrintDesk.

Storage defaults to local disk (STORAGE_BACKEND=local, the default) so the
prototype works with zero external accounts. Swap to Cloudflare R2
(STORAGE_BACKEND=r2 + the R2_* env vars below) once real credentials exist --
the Storage interface is identical either way, so nothing in app.py changes.

Still open (flagged in CLAUDE.md, not solved here):
- Antivirus scanning is a no-op stub (see scan_file) -- no ClamAV/clamd
  wired up yet. Every upload is currently unscanned.
- No PII retention policy for stored customer files.
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import BinaryIO, Optional

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

    def signed_url(self, key: str, expires_in: int = 3600) -> str:
        raise NotImplementedError


class LocalDiskStorage(Storage):
    """Prototype-only backend. Files live outside the Flask static/ web root
    and are never served directly -- retrieval always goes through the
    signed `/staff/uploads/<token>` route in app.py, the same shape a real
    R2 presigned URL takes. Set STORAGE_BACKEND=r2 with the R2_* env vars
    to move to real object storage without touching any calling code.
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


class R2Storage(Storage):
    """Cloudflare R2 (S3-compatible) backend -- inactive until
    STORAGE_BACKEND=r2 and the env vars below are set:
      R2_ACCOUNT_ID, R2_BUCKET_NAME, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY
    """

    def __init__(self):
        import boto3  # imported lazily so boto3 stays optional until R2 is wired up

        account_id = os.environ["R2_ACCOUNT_ID"]
        self.bucket = os.environ["R2_BUCKET_NAME"]
        self.client = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            region_name="auto",
        )

    def save(self, fileobj: BinaryIO, key: str) -> None:
        self.client.upload_fileobj(fileobj, self.bucket, key)

    def save_json(self, key: str, obj: dict) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key=key + ".json",
            Body=json.dumps(obj).encode("utf-8"),
            ContentType="application/json",
        )

    def signed_url(self, key: str, expires_in: int = 3600) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )


def build_storage(app) -> Storage:
    backend = os.environ.get("STORAGE_BACKEND", "local")
    if backend == "r2":
        return R2Storage()
    if backend != "local":
        raise ValueError(f"Unknown STORAGE_BACKEND: {backend!r}")

    root = os.environ.get("UPLOAD_DIR") or os.path.join(app.root_path, "uploads_private")
    logger.warning(
        "STORAGE_BACKEND=local: files are stored on local disk. Fine for demoing "
        "this prototype, but must move to R2 (STORAGE_BACKEND=r2 + R2_* env vars) "
        "before this ever handles a real customer file."
    )
    return LocalDiskStorage(root, secret_key=app.secret_key)
