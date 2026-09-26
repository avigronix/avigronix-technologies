"""Orphaned-upload cleanup and atomic shop registration (A7)."""
import os
import time

import pytest

import routers.shop as shop_module
from conftest import TINY_PNG, UPLOADS, register_payload, uploaded_files

DAY = 24 * 60 * 60


def make_upload(rel_path, age_seconds):
    path = UPLOADS.parent / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(TINY_PNG)
    t = time.time() - age_seconds
    os.utime(path, (t, t))
    return rel_path


# --------------------------------------------------------------------------
# Orphaned preview uploads
# --------------------------------------------------------------------------
async def test_cleanup_removes_only_old_unreferenced_files(client, db):
    old_orphan = make_upload("uploads/logos/old-orphan.png", 2 * DAY)
    new_orphan = make_upload("uploads/banners/new-orphan.png", 60)
    # every kind of file a registered shop can reference, all old
    kept = [
        make_upload("uploads/logos/shop-logo.png", 30 * DAY),
        make_upload("uploads/banners/shop-banner.png", 30 * DAY),
        make_upload("uploads/shop_qr/shop-qr.png", 30 * DAY),
        make_upload("uploads/bank_qr/bank-qr.png", 30 * DAY),
        make_upload("uploads/payment_qr/pay-qr.png", 30 * DAY),
        make_upload("uploads/logos/inactive-shop-logo.png", 30 * DAY),
    ]
    await db.shops.insert_one({
        "subdomain": "owner", "shop_status": "active",
        "logo": "/uploads/logos/shop-logo.png", "banner": "/uploads/banners/shop-banner.png",
        "shop_qr": "/uploads/shop_qr/shop-qr.png",
        "payment_info": {"bank_qr": "/uploads/bank_qr/bank-qr.png", "payment_qr": "/uploads/payment_qr/pay-qr.png"},
    })
    # registered-but-deactivated shops still own their files
    await db.shops.insert_one({"subdomain": "sleeping", "shop_status": "inactive",
                               "logo": "/uploads/logos/inactive-shop-logo.png"})

    removed = await shop_module.cleanup_orphaned_uploads()

    assert removed == [old_orphan]
    remaining = uploaded_files()
    assert old_orphan.removeprefix("uploads/") not in remaining
    assert new_orphan.removeprefix("uploads/") in remaining
    for rel in kept:
        assert rel.removeprefix("uploads/") in remaining


async def test_cleanup_deletes_nothing_if_shop_lookup_fails(client, monkeypatch):
    make_upload("uploads/logos/old-orphan.png", 2 * DAY)

    class BrokenCursor:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise ConnectionError("db down")

    class BrokenShops:
        def find(self, *a, **kw):
            return BrokenCursor()

    class BrokenDB:
        shops = BrokenShops()

    monkeypatch.setattr(shop_module, "db", BrokenDB())
    with pytest.raises(ConnectionError):
        await shop_module.cleanup_orphaned_uploads()
    assert uploaded_files() == ["logos/old-orphan.png"]


async def test_cleanup_ignores_hidden_files_and_folders(client):
    make_upload("uploads/logos/.gitkeep", 10 * DAY)
    (UPLOADS / "logos" / "subdir").mkdir()
    assert await shop_module.cleanup_orphaned_uploads() == []


async def test_registered_shop_files_survive_cleanup_end_to_end(client):
    """Preview → register → cleanup: the published shop's files are kept."""
    from conftest import browser_files, build_multipart, preview_form

    body, headers = build_multipart(preview_form(domain="keeper"), browser_files())
    assert (await client.post("/shop/preview", content=body, headers=headers)).status_code == 200
    logo, banner = sorted(f for f in uploaded_files() if f.startswith(("logos/", "banners/")))
    # age them past the cutoff
    for rel in (logo, banner):
        t = time.time() - 2 * DAY
        os.utime(UPLOADS / rel, (t, t))
    payload = register_payload(domain="keeper", logo=f"/uploads/{logo}", banner=f"/uploads/{banner}")
    assert (await client.post("/shop/register-business", json=payload)).status_code == 200

    assert await shop_module.cleanup_orphaned_uploads() == []
    assert (await client.get(f"/uploads/{logo}")).status_code == 200


# --------------------------------------------------------------------------
# A7: shop + subdomain mapping are written together or not at all
# --------------------------------------------------------------------------
@pytest.fixture
def failing_mapping_insert(monkeypatch, db):
    """Make every insert into the `subdomains` collection fail."""
    collection_cls = type(db.subdomains)
    original = collection_cls.insert_one

    async def insert_one(self, document, *args, **kwargs):
        if self.name == "subdomains":
            raise RuntimeError("simulated failure writing the subdomain mapping")
        return await original(self, document, *args, **kwargs)

    monkeypatch.setattr(collection_cls, "insert_one", insert_one)


@pytest.fixture(params=["fallback-rollback", "transaction"])
async def write_mode(request, monkeypatch):
    shop_module._transactions_supported = None
    native = await shop_module._supports_transactions()
    if request.param == "transaction" and not native:
        pytest.skip("this MongoDB is not a replica set, so transactions aren't available here")
    monkeypatch.setattr(shop_module, "_transactions_supported", request.param == "transaction")
    yield request.param
    shop_module._transactions_supported = None


async def test_failed_mapping_write_leaves_no_orphan_shop(client, db, write_mode, failing_mapping_insert):
    r = await client.post("/shop/register-business", json=register_payload(domain="half-written"))
    assert r.status_code == 500
    assert "simulated failure" not in r.text
    assert await db.shops.find_one({"subdomain": "half-written"}) is None
    assert await db.subdomains.find_one({"subdomain": "half-written"}) is None
    # and the subdomain is free again for a retry
    assert await shop_module.is_subdomain_available("half-written")


async def test_successful_registration_writes_both(client, db, write_mode):
    r = await client.post("/shop/register-business", json=register_payload(domain="both-written"))
    assert r.status_code == 200
    shop = await db.shops.find_one({"subdomain": "both-written"})
    mapping = await db.subdomains.find_one({"subdomain": "both-written"})
    assert shop and mapping and mapping["shop_id"] == str(shop["_id"])


async def test_duplicate_still_reported_as_taken_in_each_mode(client, write_mode, monkeypatch):
    assert (await client.post("/shop/register-business", json=register_payload(domain="twice"))).status_code == 200

    async def always_available(_):
        return True

    monkeypatch.setattr(shop_module, "is_subdomain_available", always_available)
    r = await client.post("/shop/register-business", json=register_payload(domain="twice"))
    assert r.status_code == 400 and "taken" in r.json()["detail"]
