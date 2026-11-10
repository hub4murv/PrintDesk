import io
import os

import pytest

from app import app
from uploads import UploadRejected, validate_upload

PDF = b"%PDF-1.7\n%test file\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
TIFF_INTEL = b"II*\x00" + b"\x00" * 32
TIFF_MOTOROLA = b"MM\x00*" + b"\x00" * 32
POSTSCRIPT = b"%!PS-Adobe-3.0 EPSF-3.0\n"
DOS_EPS = b"\xc5\xd0\xd3\xc6" + b"\x00" * 32
ZIP = b"PK\x03\x04" + b"\x00" * 32


# ---------- validate_upload (file type checks) ----------

@pytest.mark.parametrize("filename, data", [
    ("design.pdf", PDF),
    ("design.png", PNG),
    ("design.jpg", JPG),
    ("design.jpeg", JPG),
    ("design.tif", TIFF_INTEL),
    ("design.tiff", TIFF_MOTOROLA),
    ("design.ai", PDF),          # modern Illustrator files are PDFs
    ("design.ai", POSTSCRIPT),   # legacy Illustrator files are PostScript
    ("design.eps", POSTSCRIPT),
    ("design.eps", DOS_EPS),
    ("DESIGN.PDF", PDF),         # extension check is case-insensitive
])
def test_valid_design_files_are_accepted(filename, data):
    assert validate_upload(filename, data) == filename.rsplit(".", 1)[1].lower()


@pytest.mark.parametrize("filename", ["virus.exe", "page.html", "logo.svg", "archive.zip", "no_extension"])
def test_unsupported_extensions_are_rejected(filename):
    with pytest.raises(UploadRejected):
        validate_upload(filename, PDF)


def test_zip_renamed_to_pdf_is_rejected():
    with pytest.raises(UploadRejected):
        validate_upload("design.pdf", ZIP)


def test_content_that_does_not_match_extension_is_rejected():
    with pytest.raises(UploadRejected):
        validate_upload("design.png", PDF)


# ---------- /api/upload ----------

def upload(client, filename, data, key="posters"):
    return client.post(
        "/api/upload",
        data={"key": key, "file": (io.BytesIO(data), filename)},
        content_type="multipart/form-data",
    )


def stored_files(storage):
    return [name for name in os.listdir(storage.root) if not name.endswith(".json")]


def test_valid_upload_is_stored(client, temp_storage):
    response = upload(client, "poster.png", PNG)
    assert response.status_code == 200
    assert response.get_json() == {"filename": "poster.png", "size": len(PNG)}
    files = stored_files(temp_storage)
    assert len(files) == 1
    assert temp_storage.get_metadata(files[0])["original_filename"] == "poster.png"


def test_stored_name_is_random_not_the_customer_filename(client, temp_storage):
    # A hostile filename must never decide where the file is written.
    upload(client, "../../app.png", PNG)
    files = stored_files(temp_storage)
    assert len(files) == 1
    assert ".." not in files[0] and "app" not in files[0]


def test_upload_for_unknown_product_is_rejected(client, temp_storage):
    assert upload(client, "poster.png", PNG, key="does_not_exist").status_code == 400
    assert stored_files(temp_storage) == []


def test_upload_without_file_is_rejected(client, temp_storage):
    response = client.post("/api/upload", data={"key": "posters"}, content_type="multipart/form-data")
    assert response.status_code == 400


def test_empty_file_is_rejected(client, temp_storage):
    assert upload(client, "poster.png", b"").status_code == 400
    assert stored_files(temp_storage) == []


def test_spoofed_file_is_rejected_and_not_stored(client, temp_storage):
    assert upload(client, "poster.pdf", ZIP).status_code == 400
    assert stored_files(temp_storage) == []


def test_file_over_size_limit_returns_413(client, temp_storage, monkeypatch):
    monkeypatch.setitem(app.config, "MAX_CONTENT_LENGTH", 1024)
    response = upload(client, "poster.pdf", PDF + b"0" * 4096)
    assert response.status_code == 413
    assert "too large" in response.get_json()["error"]


# ---------- /staff/uploads/<token> (signed download links) ----------

def test_signed_link_downloads_the_file(client, temp_storage):
    upload(client, "poster.png", PNG)
    key = stored_files(temp_storage)[0]
    response = client.get(temp_storage.signed_url(key))
    assert response.status_code == 200
    assert response.data == PNG
    assert "poster.png" in response.headers["Content-Disposition"]


def test_tampered_link_is_rejected(client, temp_storage):
    upload(client, "poster.png", PNG)
    url = temp_storage.signed_url(stored_files(temp_storage)[0])
    assert client.get(url[:-2] + "xx").status_code == 404


def test_link_signed_with_another_key_is_rejected(client, temp_storage):
    from uploads import LocalDiskStorage

    upload(client, "poster.png", PNG)
    key = stored_files(temp_storage)[0]
    forged = LocalDiskStorage(temp_storage.root, secret_key="attacker-secret").signed_url(key)
    assert client.get(forged).status_code == 404


def test_signed_path_traversal_is_rejected(client, temp_storage):
    # Even a correctly signed token must not be able to point outside the upload folder.
    token_url = temp_storage.signed_url("../app.py")
    assert client.get(token_url).status_code == 404
