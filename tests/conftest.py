"""Test harness for the AVIGRONIX website.

Safety guarantees (enforced here, before the app is imported):

* Never the real database. Tests use a throwaway database named
  ``avigronix_test_<random>``. With ``TEST_MONGODB_URL`` set they run
  against that server (and drop the database afterwards); without it they
  use an in-memory mongomock-motor client, so no MongoDB is needed at all.
* Never real email. SMTP settings are replaced with dummy values *before*
  ``load_dotenv()`` runs (it never overrides existing variables), and
  ``smtplib.SMTP`` is swapped for a recorder in every test.
* Never the real ``app/uploads`` folder. The app resolves ``static/``,
  ``templates/`` and ``uploads/`` relative to the working directory, so the
  tests run from a temporary directory that symlinks the first two and has
  its own empty ``uploads/``.
"""
import base64
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "app"

# --------------------------------------------------------------------------
# Environment — must be in place before anything from the app is imported.
# --------------------------------------------------------------------------
TEST_DB_NAME = f"avigronix_test_{uuid.uuid4().hex[:8]}"
USE_REAL_MONGO = bool(os.environ.get("TEST_MONGODB_URL"))

os.environ["DATABASE_NAME"] = TEST_DB_NAME
os.environ["MONGODB_URL"] = os.environ.get("TEST_MONGODB_URL") or "mongodb://unused.invalid:27017"
os.environ["SMTP_HOST"] = "smtp.invalid"
os.environ["SMTP_PORT"] = "587"
os.environ["SMTP_USER"] = "noreply@example.com"
os.environ["SMTP_PASS"] = "not-a-real-password"
os.environ["ADMIN_API_KEY"] = ""          # individual tests set it explicitly
os.environ["SITE_URL"] = "https://avigronix.com"
os.environ.pop("ENABLE_API_DOCS", None)  # docs must be off by default

WORKDIR = Path(tempfile.mkdtemp(prefix="avx_test_"))
(WORKDIR / "static").symlink_to(APP_DIR / "static")
(WORKDIR / "templates").symlink_to(APP_DIR / "templates")

# Filled in by pytest_sessionstart (after pytest has resolved its own paths).
database = main = shop_module = None


def pytest_sessionstart(session):
    """Switch into the isolated working directory and import the app.

    Done here rather than at conftest import time so pytest has already
    resolved ``testpaths`` relative to the project root.
    """
    global database, main, shop_module
    os.chdir(WORKDIR)
    sys.path.insert(0, str(APP_DIR))

    import database as _database

    assert _database.DATABASE_NAME == TEST_DB_NAME, "refusing to run against a non-test database"
    assert _database.DATABASE_NAME != "shop_management"
    if not USE_REAL_MONGO:
        from mongomock_motor import AsyncMongoMockClient

        _database.client = AsyncMongoMockClient()
        _database.db = _database.client[TEST_DB_NAME]

    import main as _main  # binds database.db / database.client at import
    import routers.shop as _shop

    database, main, shop_module = _database, _main, _shop


UPLOADS = WORKDIR / "uploads"
UPLOAD_SUBDIRS = ("logos", "banners", "bank_qr", "payment_qr", "shop_qr")

# Smallest valid PNG (1x1), used wherever an image upload is needed.
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(WORKDIR, ignore_errors=True)


# --------------------------------------------------------------------------
# App + client
# --------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="session")
async def app_running():
    """Run the real lifespan (unique-index creation, client close) once."""
    async with main.app.router.lifespan_context(main.app):
        yield main.app
        if USE_REAL_MONGO:
            await database.client.drop_database(TEST_DB_NAME)


@pytest_asyncio.fixture
async def client(app_running):
    transport = httpx.ASGITransport(app=app_running)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture
def db():
    return database.db


# --------------------------------------------------------------------------
# Per-test isolation
# --------------------------------------------------------------------------
@pytest_asyncio.fixture(autouse=True)
async def _clean_state(app_running):
    main.limiter.reset()
    await database.db.shops.delete_many({})
    await database.db.subdomains.delete_many({})
    shutil.rmtree(UPLOADS, ignore_errors=True)
    for sub in UPLOAD_SUBDIRS:
        (UPLOADS / sub).mkdir(parents=True, exist_ok=True)
    yield


class _SentMail(list):
    """List of email.message.Message objects the app tried to send."""

    def bodies(self):
        out = []
        for msg in self:
            parts = msg.get_payload()
            out.append("".join(p.get_payload(decode=True).decode() for p in parts))
        return out


@pytest.fixture(autouse=True)
def sent_mail(monkeypatch):
    """Replace smtplib.SMTP inside the app with a recorder — no network, ever."""
    sent = _SentMail()

    class FakeSMTP:
        def __init__(self, host, port, *a, **kw):
            assert host == "smtp.invalid", "tests must never talk to a real SMTP server"

        def starttls(self):
            pass

        def login(self, user, password):
            pass

        def send_message(self, msg):
            sent.append(msg)

        def quit(self):
            pass

    monkeypatch.setattr(main.smtplib, "SMTP", FakeSMTP)
    return sent


@pytest.fixture
def admin_key(monkeypatch):
    key = "test-admin-key-" + uuid.uuid4().hex
    monkeypatch.setattr(shop_module, "ADMIN_API_KEY", key)
    return key


# --------------------------------------------------------------------------
# Browser-faithful multipart builder
# --------------------------------------------------------------------------
def build_multipart(fields, files):
    """Build a multipart/form-data body exactly the way a browser does.

    ``files`` maps field name -> (filename, content_type, bytes). An empty
    filename with empty content is what a browser sends for an
    ``<input type="file">`` the visitor left untouched — it is *not* omitted.
    """
    boundary = "----AvxTestBoundary" + uuid.uuid4().hex
    body = b""
    for name, value in fields.items():
        body += (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n"
        ).encode()
    for name, (filename, ctype, content) in files.items():
        body += (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
            f"filename=\"{filename}\"\r\nContent-Type: {ctype}\r\n\r\n"
        ).encode()
        body += content + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return body, {"Content-Type": f"multipart/form-data; boundary={boundary}"}


EMPTY_FILE = ("", "application/octet-stream", b"")


def preview_form(domain="test-shop-one", **overrides):
    """The same fields register_business.html submits, with sane defaults."""
    fields = {
        "shopName": "Test Shop",
        "domain": domain,
        "about": "A shop used by the automated tests",
        "category": "Retail",
        "businessType": "Retail",
        "contactNumber": "+919876543210",
        "email": "owner@example.com",
        "supportEmail": "",
        "gstNumber": "",
        "address": "1 Test Street",
        "city": "Noida",
        "state": "Uttar Pradesh",
        "pincode": "201301",
        "location": "",
        "whatsapp": "",
        "website": "",
        "instagram": "",
        "facebook": "",
        "youtube": "",
        "accountHolder": "",
        "bankName": "",
        "accountNumber": "",
        "ifscCode": "",
        "upiId": "",
    }
    fields.update(overrides)
    return fields


def browser_files(**overrides):
    files = {
        "logo": ("logo.png", "image/png", TINY_PNG),
        "banner": ("banner.png", "image/png", TINY_PNG),
        # the three optional file inputs, left empty like a real visitor would
        "paymentQR": EMPTY_FILE,
        "bankQR": EMPTY_FILE,
        "shopQR": EMPTY_FILE,
    }
    files.update(overrides)
    return files


def register_payload(domain="test-shop-one", **overrides):
    """The JSON preview_business.html's publish button sends."""
    payload = {
        "shopName": "Test Shop",
        "domain": domain,
        "shop_url": f"https://{domain}.avigronix.com",
        "about": "A shop used by the automated tests",
        "category": "Retail",
        "businessType": "Retail",
        "contactNumber": "+919876543210",
        "email": "owner@example.com",
        "supportEmail": None,
        "gstNumber": None,
        "logo": "/uploads/logos/x.png",
        "banner": "/uploads/banners/x.png",
        "address": "1 Test Street",
        "city": "Noida",
        "state": "Uttar Pradesh",
        "pincode": "201301",
        "location": None,
        "whatsapp": None,
        "instagram": None,
        "facebook": None,
        "youtube": None,
        "website": None,
        "accountHolder": "Owner Name",
        "bankName": "Test Bank",
        "accountNumber": "000111222333",
        "ifscCode": "TEST0000001",
        "upiId": "owner@upi",
        "bankQR": None,
        "paymentQR": None,
        "shopQR": None,
        "openingHours": None,
        "shopStatus": None,
    }
    payload.update(overrides)
    return payload


def uploaded_files():
    return sorted(str(p.relative_to(UPLOADS)) for p in UPLOADS.rglob("*") if p.is_file())
