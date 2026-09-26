"""Uploaded images never keep EXIF/GPS or other metadata; API docs are off."""
import io
import os
import subprocess
import sys
import time

import pytest
from PIL import Image

from conftest import APP_DIR, ROOT, UPLOADS, browser_files, build_multipart, preview_form, uploaded_files
from image_sanitize import metadata_keys

GPS = {1: "N", 2: (28.0, 35.0, 21.0), 3: "E", 4: (77.0, 21.0, 22.0)}


def photo(fmt="JPEG", size=(8, 6), mode="RGB", orientation=None, extra=None):
    """An image carrying camera EXIF incl. GPS coordinates, like a phone photo."""
    img = Image.new(mode, size, "red")
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"          # Make
    exif[0x0110] = "PhoneModel X"        # Model
    exif[0x0132] = "2026:09:20 10:11:12"  # DateTime
    exif[0x8825] = GPS                    # GPSInfo
    if orientation:
        exif[0x0112] = orientation
    buf = io.BytesIO()
    kwargs = {"exif": exif.tobytes()}
    if fmt == "PNG" and extra:
        from PIL.PngImagePlugin import PngInfo

        info = PngInfo()
        for k, v in extra.items():
            info.add_text(k, v)
        kwargs["pnginfo"] = info
    img.save(buf, fmt, **kwargs)
    data = buf.getvalue()
    assert "exif" in metadata_keys(data)  # the fixture really carries EXIF
    return data


async def upload_logo(client, filename, data, domain="exif-shop"):
    body, headers = build_multipart(preview_form(domain=domain), browser_files(logo=(filename, "image/*", data)))
    return await client.post("/shop/preview", content=body, headers=headers)


def saved_logo():
    return next((UPLOADS / "logos").iterdir())


# --------------------------------------------------------------------------
# New uploads
# --------------------------------------------------------------------------
@pytest.mark.parametrize("fmt,ext", [("JPEG", ".jpg"), ("JPEG", ".jpeg"), ("PNG", ".png"), ("WEBP", ".webp")])
async def test_upload_strips_exif_and_gps(client, fmt, ext):
    r = await upload_logo(client, "phone-photo" + ext, photo(fmt))
    assert r.status_code == 200, r.text
    path = saved_logo()
    data = path.read_bytes()
    assert metadata_keys(data) == []
    with Image.open(path) as img:
        assert not img.getexif()
        assert not img.getexif().get_ifd(0x8825)
    for leak in (b"PhoneMaker", b"PhoneModel", b"2026:09:20"):
        assert leak not in data


async def test_png_text_chunks_are_removed(client):
    data = photo("PNG", extra={"Author": "Owner Name", "Comment": "home address 12 Street"})
    r = await upload_logo(client, "logo.png", data)
    assert r.status_code == 200
    saved = saved_logo().read_bytes()
    assert b"Owner Name" not in saved and b"home address" not in saved
    assert metadata_keys(saved) == []


async def test_bank_and_payment_qr_uploads_are_stripped_too(client):
    files = browser_files(paymentQR=("qr.jpg", "image/jpeg", photo()), bankQR=("bank.png", "image/png", photo("PNG")))
    body, headers = build_multipart(preview_form(domain="qr-exif"), files)
    assert (await client.post("/shop/preview", content=body, headers=headers)).status_code == 200
    for sub in ("payment_qr", "bank_qr", "logos", "banners"):
        for f in (UPLOADS / sub).iterdir():
            assert metadata_keys(f.read_bytes()) == [], f


async def test_phone_orientation_is_applied_before_exif_is_dropped(client):
    # Orientation 6 = "rotate 90° clockwise to display"; stored 8x6 shows as 6x8
    r = await upload_logo(client, "portrait.jpg", photo(size=(8, 6), orientation=6))
    assert r.status_code == 200
    with Image.open(saved_logo()) as img:
        assert img.size == (6, 8)


async def test_png_transparency_is_kept(client):
    img = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    assert (await upload_logo(client, "t.png", buf.getvalue())).status_code == 200
    with Image.open(saved_logo()) as saved:
        assert saved.mode == "RGBA" and saved.getpixel((0, 0))[3] == 0


async def test_non_image_with_image_extension_rejected(client):
    r = await upload_logo(client, "evil.png", b"<script>alert(1)</script>")
    assert r.status_code == 400
    assert "not a valid image" in r.json()["detail"]
    assert uploaded_files() == []


async def test_content_is_converted_to_the_type_its_extension_claims(client):
    """A JPEG named .png is stored as a real PNG, so the Content-Type the
    server sends (from the extension) always matches the bytes."""
    r = await upload_logo(client, "actually-jpeg.png", photo("JPEG"))
    assert r.status_code == 200
    with Image.open(saved_logo()) as img:
        assert img.format == "PNG"


async def test_decompression_bomb_rejected(client):
    # tiny file, enormous bitmap (10000 x 10000 = 100 MP)
    buf = io.BytesIO()
    Image.new("1", (10000, 10000)).save(buf, "PNG")
    assert len(buf.getvalue()) < 5 * 1024 * 1024
    r = await upload_logo(client, "bomb.png", buf.getvalue())
    assert r.status_code == 400
    assert uploaded_files() == []


# --------------------------------------------------------------------------
# One-off script for files uploaded before this existed
# --------------------------------------------------------------------------
def run_script(uploads, *args):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "strip_upload_metadata.py"), "--uploads", str(uploads), *args],
        capture_output=True, text=True, check=True,
    ).stdout


def test_strip_script(tmp_path):
    uploads = tmp_path / "uploads"
    for sub in ("logos", "banners", "payment_qr"):
        (uploads / sub).mkdir(parents=True)
    old = time.time() - 3 * 86400
    files = {
        "logos/Shop Logo 1.jpg": photo(),                  # spaces in name, like real uploads
        "banners/banner.png": photo("PNG", extra={"Author": "Owner"}),
        "payment_qr/qr.webp": photo("WEBP"),
    }
    clean = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(clean, "PNG")
    files["logos/already-clean.png"] = clean.getvalue()
    files["logos/notes.png"] = b"not really an image"
    for rel, data in files.items():
        (uploads / rel).write_bytes(data)
        os.utime(uploads / rel, (old, old))

    # dry run changes nothing
    out = run_script(uploads, "--dry-run")
    assert "Would clean 3 file(s)" in out
    for rel, data in files.items():
        assert (uploads / rel).read_bytes() == data

    out = run_script(uploads)
    assert "Cleaned 3 file(s); 1 already had no metadata; 1 skipped." in out
    for rel in ("logos/Shop Logo 1.jpg", "banners/banner.png", "payment_qr/qr.webp"):
        data = (uploads / rel).read_bytes()
        assert metadata_keys(data) == [], rel
        assert b"PhoneMaker" not in data and b"Owner" not in data
        assert abs(os.stat(uploads / rel).st_mtime - old) < 2      # mtime kept
    assert (uploads / "logos/already-clean.png").read_bytes() == files["logos/already-clean.png"]
    assert (uploads / "logos/notes.png").read_bytes() == b"not really an image"   # left untouched
    assert not list(uploads.rglob(".strip-*"))                                  # no temp files left

    # running again is a no-op
    assert "Cleaned 0 file(s)" in run_script(uploads)


# --------------------------------------------------------------------------
# API docs
# --------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
async def test_api_docs_disabled_by_default(client, path):
    r = await client.get(path)
    assert r.status_code == 404
    assert "/shop/api/shops" not in r.text


def _docs_settings(value):
    env = {k: v for k, v in os.environ.items() if k != "ENABLE_API_DOCS"}
    if value is not None:
        env["ENABLE_API_DOCS"] = value
    code = "import main; print(main.app.docs_url, main.app.redoc_url, main.app.openapi_url)"
    return subprocess.run([sys.executable, "-c", code], cwd=os.getcwd(), env=env,
                          capture_output=True, text=True, check=True).stdout.split()


@pytest.mark.parametrize("value,expected", [
    (None, ["None", "None", "None"]),
    ("false", ["None", "None", "None"]),
    ("1", ["None", "None", "None"]),
    ("true", ["/docs", "/redoc", "/openapi.json"]),
    ("TRUE", ["/docs", "/redoc", "/openapi.json"]),
])
def test_api_docs_only_with_explicit_env_flag(value, expected, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(APP_DIR))
    assert _docs_settings(value) == expected
