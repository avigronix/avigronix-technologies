"""The open "Launch your business" shop flow, its hardening, and the admin APIs."""
import pytest

import routers.shop as shop_module
from conftest import (
    EMPTY_FILE,
    TINY_PNG,
    browser_files,
    build_multipart,
    preview_form,
    register_payload,
    uploaded_files,
)


async def post_preview(client, fields=None, files=None):
    body, headers = build_multipart(fields or preview_form(), files or browser_files())
    return await client.post("/shop/preview", content=body, headers=headers)


# --------------------------------------------------------------------------
# End-to-end flow
# --------------------------------------------------------------------------
async def test_full_flow_preview_register_public_pages(client, db):
    r = await post_preview(client, preview_form(domain="flow-shop"))
    assert r.status_code == 200, r.text
    assert "Publish Website Now" in r.text
    # both mandatory uploads were stored under uploads/, optional ones skipped
    files = uploaded_files()
    assert len([f for f in files if f.startswith("logos/")]) == 1
    assert len([f for f in files if f.startswith("banners/")]) == 1
    assert not any(f.startswith(("payment_qr/", "bank_qr/", "shop_qr/")) for f in files)

    r = await client.post("/shop/register-business", json=register_payload(domain="flow-shop"))
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True
    assert r.json()["shop_url"] == "https://flow-shop.avigronix.com"

    shop = await db.shops.find_one({"subdomain": "flow-shop"})
    assert shop is not None
    assert await db.subdomains.find_one({"subdomain": "flow-shop"}) is not None

    # public page via /shop/{subdomain}
    r = await client.get("/shop/flow-shop")
    assert r.status_code == 200
    assert "Test Shop" in r.text

    # public page via the subdomain root route
    r = await client.get("/", headers={"host": "flow-shop.localhost"})
    assert r.status_code == 200
    assert "Test Shop" in r.text
    assert "Official Website" in r.text


async def test_unknown_subdomain_shows_shop_not_found(client):
    r = await client.get("/", headers={"host": "nosuchshop.localhost"})
    assert r.status_code == 200
    assert "not found" in r.text.lower()


async def test_inactive_shop_is_not_public(client, db, admin_key):
    await client.post("/shop/register-business", json=register_payload(domain="sleepy-shop"))
    r = await client.put(
        "/shop/api/shop/sleepy-shop/status", params={"status": "inactive"}, headers={"X-Admin-Key": admin_key}
    )
    assert r.status_code == 200
    assert (await client.get("/shop/sleepy-shop")).status_code == 404


# --------------------------------------------------------------------------
# Regression: optional file inputs left empty (browser sends filename="")
# --------------------------------------------------------------------------
@pytest.mark.parametrize("empty_field", ["paymentQR", "bankQR", "shopQR"])
async def test_empty_optional_file_fields_like_a_real_browser(client, empty_field):
    files = browser_files()
    files[empty_field] = EMPTY_FILE
    r = await post_preview(client, preview_form(domain=f"empty-{empty_field.lower()}"), files)
    assert r.status_code == 200, r.text


async def test_all_optional_files_empty_then_register(client):
    r = await post_preview(client, preview_form(domain="all-empty"), browser_files())
    assert r.status_code == 200, r.text
    r = await client.post("/shop/register-business", json=register_payload(domain="all-empty"))
    assert r.status_code == 200


async def test_optional_file_actually_provided_is_saved(client):
    files = browser_files(paymentQR=("qr.png", "image/png", TINY_PNG))
    r = await post_preview(client, preview_form(domain="with-qr"), files)
    assert r.status_code == 200, r.text
    assert len([f for f in uploaded_files() if f.startswith("payment_qr/")]) == 1


# --------------------------------------------------------------------------
# Upload validation
# --------------------------------------------------------------------------
async def test_upload_over_5mb_rejected_and_nothing_left_on_disk(client):
    too_big = b"\x89PNG" + b"0" * (5 * 1024 * 1024)
    r = await post_preview(client, preview_form(domain="big-logo"), browser_files(logo=("big.png", "image/png", too_big)))
    assert r.status_code == 400
    assert "5 MB" in r.json()["detail"]
    assert uploaded_files() == []


async def test_oversized_later_file_cleans_up_earlier_saved_files(client):
    """logo is saved first; if banner then fails, the logo must not be left behind."""
    too_big = b"\x89PNG" + b"0" * (5 * 1024 * 1024)
    r = await post_preview(
        client, preview_form(domain="big-banner"), browser_files(banner=("big.png", "image/png", too_big))
    )
    assert r.status_code == 400
    assert uploaded_files() == []


async def test_exactly_5mb_is_accepted(client):
    # a real PNG padded with trailing bytes to exactly the limit
    exactly = TINY_PNG + b"\0" * (5 * 1024 * 1024 - len(TINY_PNG))
    r = await post_preview(client, preview_form(domain="edge-size"), browser_files(logo=("edge.png", "image/png", exactly)))
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("filename", ["evil.html", "shell.php", "noext", "image.svg", "x.png.exe"])
async def test_unsupported_file_type_rejected(client, filename):
    r = await post_preview(
        client, preview_form(domain="bad-type"), browser_files(logo=(filename, "image/png", TINY_PNG))
    )
    assert r.status_code == 400
    assert "Unsupported file type" in r.json()["detail"]
    assert uploaded_files() == []


async def test_uploaded_file_gets_server_generated_name(client):
    r = await post_preview(
        client, preview_form(domain="names"), browser_files(logo=("../../../etc/passwd.png", "image/png", TINY_PNG))
    )
    assert r.status_code == 200
    logo = [f for f in uploaded_files() if f.startswith("logos/")][0]
    assert "passwd" not in logo and ".." not in logo


# --------------------------------------------------------------------------
# Subdomain rules
# --------------------------------------------------------------------------
@pytest.mark.parametrize("reserved", ["admin", "www", "api", "shop", "avigronix", "blog", "static", "uploads"])
async def test_reserved_subdomains_rejected(client, reserved):
    r = await post_preview(client, preview_form(domain=reserved))
    assert r.status_code == 400
    r = await client.post("/shop/register-business", json=register_payload(domain=reserved))
    assert r.status_code == 400


@pytest.mark.parametrize("bad", ["Upper", "has space", "under_score", "dot.dot", "semi;colon", ""])
async def test_invalid_subdomain_format_rejected(client, bad):
    r = await post_preview(client, preview_form(domain=bad))
    assert r.status_code in (400, 422)
    r = await client.post("/shop/register-business", json=register_payload(domain=bad))
    assert r.status_code in (400, 422)


async def test_duplicate_subdomain_rejected(client):
    assert (await client.post("/shop/register-business", json=register_payload(domain="dupe"))).status_code == 200
    r = await client.post("/shop/register-business", json=register_payload(domain="dupe"))
    assert r.status_code == 400
    assert "taken" in r.json()["detail"]
    r = await post_preview(client, preview_form(domain="dupe"))
    assert r.status_code == 400


async def test_duplicate_subdomain_race_is_caught_by_unique_index(client, monkeypatch):
    """Even if the availability pre-check is bypassed (two requests racing),
    the unique index turns the second insert into a clean 'taken' error."""
    assert (await client.post("/shop/register-business", json=register_payload(domain="racer"))).status_code == 200

    async def always_available(_):
        return True

    monkeypatch.setattr(shop_module, "is_subdomain_available", always_available)
    r = await client.post("/shop/register-business", json=register_payload(domain="racer"))
    assert r.status_code == 400
    assert "taken" in r.json()["detail"]


async def test_register_requires_fields(client):
    r = await client.post("/shop/register-business", json={"shopName": "x"})
    assert r.status_code == 422


# --------------------------------------------------------------------------
# Public shop JSON API never leaks private/internal data
# --------------------------------------------------------------------------
async def test_public_shop_api_only_exposes_public_fields(client):
    await client.post("/shop/register-business", json=register_payload(domain="api-shop", gstNumber="GSTX"))
    r = await client.get("/shop/api/shop/api-shop")
    assert r.status_code == 200
    data = r.json()
    assert set(data) == {
        "shop_name", "contact_number", "email", "about", "category", "business_type", "logo", "banner",
    }
    raw = r.text
    for secret in ["payment_info", "account_number", "000111222333", "ifsc", "upi", "_id", "gst", "GSTX",
                   "address", "created_at", "shop_status", "subdomain"]:
        assert secret not in raw


async def test_public_shop_api_unknown_is_404(client):
    assert (await client.get("/shop/api/shop/nope")).status_code == 404


# --------------------------------------------------------------------------
# Admin endpoints
# --------------------------------------------------------------------------
async def test_admin_list_rejected_without_key(client, admin_key):
    assert (await client.get("/shop/api/shops")).status_code == 401


async def test_admin_list_rejected_with_wrong_key(client, admin_key):
    assert (await client.get("/shop/api/shops", headers={"X-Admin-Key": "wrong"})).status_code == 401


async def test_admin_rejects_everything_when_key_unset(client, monkeypatch):
    monkeypatch.setattr(shop_module, "ADMIN_API_KEY", "")
    assert (await client.get("/shop/api/shops")).status_code == 401
    assert (await client.get("/shop/api/shops", headers={"X-Admin-Key": ""})).status_code == 401
    r = await client.put("/shop/api/shop/x/status", params={"status": "inactive"}, headers={"X-Admin-Key": ""})
    assert r.status_code == 401


async def test_admin_list_with_correct_key(client, admin_key):
    await client.post("/shop/register-business", json=register_payload(domain="listed"))
    r = await client.get("/shop/api/shops", headers={"X-Admin-Key": admin_key})
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["shops"][0]["subdomain"] == "listed"


async def test_admin_list_limit_is_capped(client, admin_key):
    r = await client.get("/shop/api/shops", params={"limit": 100000}, headers={"X-Admin-Key": admin_key})
    assert r.status_code == 200


async def test_admin_status_requires_key(client, admin_key):
    await client.post("/shop/register-business", json=register_payload(domain="st"))
    assert (await client.put("/shop/api/shop/st/status", params={"status": "inactive"})).status_code == 401
    r = await client.put("/shop/api/shop/st/status", params={"status": "inactive"}, headers={"X-Admin-Key": "wrong"})
    assert r.status_code == 401


async def test_admin_status_invalid_value_rejected(client, admin_key):
    await client.post("/shop/register-business", json=register_payload(domain="st2"))
    r = await client.put("/shop/api/shop/st2/status", params={"status": "deleted"}, headers={"X-Admin-Key": admin_key})
    assert r.status_code == 400


async def test_admin_status_toggle(client, admin_key):
    await client.post("/shop/register-business", json=register_payload(domain="st3"))
    r = await client.put("/shop/api/shop/st3/status", params={"status": "inactive"}, headers={"X-Admin-Key": admin_key})
    assert r.status_code == 200
    r = await client.put("/shop/api/shop/st3/status", params={"status": "active"}, headers={"X-Admin-Key": admin_key})
    assert r.status_code == 200
    assert (await client.get("/shop/st3")).status_code == 200


async def test_errors_do_not_leak_exception_text(client, monkeypatch):
    class BrokenCollection:
        async def find_one(self, *a, **kw):
            raise RuntimeError("INTERNAL-DETAIL-xyz")

    class BrokenDB:
        shops = BrokenCollection()

    monkeypatch.setattr(shop_module, "db", BrokenDB())
    r = await client.get("/shop/api/shop/anything")
    assert r.status_code == 500
    assert "INTERNAL-DETAIL-xyz" not in r.text
    r = await client.get("/shop/anything")
    assert r.status_code == 500
    assert "INTERNAL-DETAIL-xyz" not in r.text
