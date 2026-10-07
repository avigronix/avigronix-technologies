from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException, Depends, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
from pathlib import Path
from datetime import datetime
import uuid
import os
import re
import time
import secrets
import logging
from pymongo.errors import DuplicateKeyError
from starlette.concurrency import run_in_threadpool

from image_sanitize import InvalidImageError, strip_metadata

from database import db
from rate_limit import limiter
from render_utils import make_templates

logger = logging.getLogger("avigronix.shop")

# Router for shop endpoints
router = APIRouter(prefix="/shop", tags=["shop"])
templates = make_templates()

# Ensure upload directories exist
UPLOAD_SUBDIRS = ("logos", "banners", "bank_qr", "payment_qr", "shop_qr")
for _sub in UPLOAD_SUBDIRS:
    os.makedirs(os.path.join("uploads", _sub), exist_ok=True)

# Preview uploads are saved straight into uploads/ and only become "owned"
# once the visitor publishes. Ones never followed by a registration are
# removed after this long.
ORPHAN_UPLOAD_MAX_AGE_SECONDS = 24 * 60 * 60


async def _referenced_upload_paths() -> set:
    """Every uploads/... path any registered shop (active or not) points to."""
    refs = set()
    projection = {"logo": 1, "banner": 1, "shop_qr": 1, "payment_info.bank_qr": 1, "payment_info.payment_qr": 1}
    async for shop in db.shops.find({}, projection):
        payment = shop.get("payment_info") or {}
        for value in (shop.get("logo"), shop.get("banner"), shop.get("shop_qr"),
                      payment.get("bank_qr"), payment.get("payment_qr")):
            if isinstance(value, str) and value.lstrip("/").startswith("uploads/"):
                refs.add(value.lstrip("/"))
    return refs


async def cleanup_orphaned_uploads(max_age_seconds: int = ORPHAN_UPLOAD_MAX_AGE_SECONDS) -> list:
    """Delete upload files older than `max_age_seconds` that no shop
    references. If the shop lookup fails, nothing is deleted."""
    referenced = await _referenced_upload_paths()
    cutoff = time.time() - max_age_seconds
    removed = []
    for sub in UPLOAD_SUBDIRS:
        folder = os.path.join("uploads", sub)
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            rel_path = f"uploads/{sub}/{name}"
            if name.startswith(".") or rel_path in referenced or not os.path.isfile(rel_path):
                continue
            try:
                if os.path.getmtime(rel_path) > cutoff:
                    continue
                os.remove(rel_path)
                removed.append(rel_path)
            except OSError:
                pass  # another worker got there first, or it vanished
    if removed:
        logger.info("Removed %d orphaned preview upload(s)", len(removed))
    return removed

# ---------------------------------------------------------------------------
# Admin auth for the internal/admin shop APIs (list-all-shops, status toggle).
# The public registration/preview/storefront flow below does NOT use this —
# it stays open by design.
# ---------------------------------------------------------------------------
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")


async def require_admin_key(x_admin_key: str = Header(default="", alias="X-Admin-Key")):
    """Guards admin-only shop endpoints with a shared-secret header.

    Uses secrets.compare_digest to avoid leaking the key via a timing
    side-channel, and always rejects if ADMIN_API_KEY isn't configured
    (so a blank/unset env var can't accidentally leave the endpoint open).
    """
    if not ADMIN_API_KEY or not secrets.compare_digest(x_admin_key, ADMIN_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing admin API key")

# Reserved subdomains: names that would be confusing, impersonate a real
# system route, or collide with the main site's own pages if someone
# registered a shop under them.
RESERVED_SUBDOMAINS = {
    "admin", "www", "api", "mail", "email", "login", "support", "help",
    "shop", "static", "uploads", "avigronix", "app", "dashboard", "root",
    "system", "test", "dev", "staging", "about", "services", "team",
    "contact", "projects", "faq", "blog", "register", "preview",
    "assets", "cdn", "ftp", "smtp", "null", "undefined",
}

# Pydantic Models
class PublicShopAPIResponse(BaseModel):
    """Exactly the fields shop_public.html shows on the public /shop/{subdomain}
    page — used to make sure the JSON API can never leak more than the page
    itself already does (no payment info, address, socials, or _id)."""
    shop_name: str
    contact_number: str
    email: str
    about: str
    category: str
    business_type: str
    logo: str
    banner: str

# Utility Functions
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 5 MB per file


async def save_uploaded_file(file: UploadFile, upload_dir: str) -> str:
    """Save uploaded file and return file path.

    Uses a generated filename rather than the client-supplied one to avoid
    path traversal / overwriting other uploads on this pre-auth form.
    Reads the upload in chunks and aborts as soon as it exceeds
    MAX_UPLOAD_SIZE, then re-encodes the image so no EXIF/GPS or other
    metadata is ever written to disk (the files are served publicly).
    """
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_IMAGE_EXTENSIONS))}",
        )

    data = bytearray()
    while chunk := await file.read(1024 * 1024):
        data += chunk
        if len(data) > MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"'{file.filename}' is too large. Maximum allowed size is 5 MB.",
            )

    try:
        clean = await run_in_threadpool(strip_metadata, bytes(data), ext)
    except InvalidImageError:
        raise HTTPException(
            status_code=400,
            detail=f"'{file.filename}' is not a valid image file.",
        )

    filename = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(upload_dir, filename)
    with open(file_path, "wb") as buffer:
        buffer.write(clean)

    return f"/uploads/{upload_dir.split('/')[-1]}/{filename}"

SUBDOMAIN_PATTERN = re.compile(r"^[a-z0-9-]+$")


def _discard_uploads(url_paths: list) -> None:
    """Delete files saved earlier in a request that then failed, so a
    rejected submission never leaves orphans on disk."""
    for url_path in url_paths:
        try:
            os.remove(url_path.lstrip("/"))
        except OSError:
            pass


_transactions_supported: Optional[bool] = None


async def _supports_transactions() -> bool:
    """Multi-document transactions need a replica set (or mongos). Detected
    once from the server's `hello` reply; any error means "no"."""
    global _transactions_supported
    if _transactions_supported is None:
        try:
            hello = await db.client.admin.command("hello")
            _transactions_supported = bool(hello.get("setName")) or hello.get("msg") == "isdbgrid"
        except Exception:
            _transactions_supported = False
    return _transactions_supported


async def _insert_shop_with_mapping(shop_document: dict, make_mapping) -> object:
    """Insert the shop and its subdomain mapping so that either both exist
    or neither does: one transaction where the server supports it,
    otherwise remove the shop again if the second write fails."""
    if await _supports_transactions():
        async with await db.client.start_session() as session:
            async with session.start_transaction():
                result = await db.shops.insert_one(shop_document, session=session)
                await db.subdomains.insert_one(make_mapping(result.inserted_id), session=session)
        return result

    result = await db.shops.insert_one(shop_document)
    try:
        await db.subdomains.insert_one(make_mapping(result.inserted_id))
    except Exception:
        await db.shops.delete_one({"_id": result.inserted_id})
        raise
    return result


async def is_subdomain_available(subdomain: str) -> bool:
    """Check if subdomain is available"""
    if subdomain.lower() in RESERVED_SUBDOMAINS:
        return False
    existing_shop = await db.shops.find_one({"subdomain": subdomain})
    return existing_shop is None

# Routes
@router.get("/register", response_class=HTMLResponse)
async def show_registration_form(request: Request):
    """Show business registration form"""
    return templates.TemplateResponse(request, "register_business.html")

@router.post("/preview", response_class=HTMLResponse)
@limiter.limit("10/10minutes")
async def preview_business(
    request: Request,

    # ---- BASIC INFO ----
    shopName: str = Form(...),
    domain: str = Form(...),
    about: str = Form(...),
    category: str = Form(...),
    businessType: str = Form(...),

    # ---- CONTACT ----
    contactNumber: str = Form(...),
    email: str = Form(...),
    supportEmail: str = Form(None),
    gstNumber: str = Form(None),

    # ---- BRANDING ----
    logo: UploadFile = File(...),
    banner: UploadFile = File(...),

    # ---- ADDRESS ----
    address: str = Form(...),
    city: str = Form(...),
    state: str = Form(...),
    pincode: str = Form(...),
    location: str = Form(None),

    # ---- SOCIAL ----
    whatsapp: str = Form(None),
    instagram: str = Form(None),
    facebook: str = Form(None),
    youtube: str = Form(None),
    website: str = Form(None),

    # ---- BANK ----
    accountHolder: str = Form(None),
    bankName: str = Form(None),
    accountNumber: str = Form(None),
    ifscCode: str = Form(None),
    upiId: str = Form(None),
    bankQR: UploadFile | None = File(None),

    # ---- QR ----
    paymentQR: UploadFile | None = File(None),
    shopQR: UploadFile | None = File(None),

    # ---- SETTINGS ----
    openingHours: str = Form(None),
    shopStatus: str = Form(None),
    deliveryAvailable: str = Form(None),
):
    """Preview business before saving to database"""
    saved = []
    try:
        # -------------------------
        # Validate subdomain
        # -------------------------
        if not SUBDOMAIN_PATTERN.match(domain):
            raise HTTPException(
                status_code=400,
                detail="Subdomain can contain only lowercase letters, numbers, and hyphens."
            )

        # Check if subdomain free
        if not await is_subdomain_available(domain):
            raise HTTPException(
                status_code=400,
                detail="Subdomain already taken. Please choose another one."
            )

        # -------------------------
        # Save uploaded files
        # -------------------------
        logo_path = await save_uploaded_file(logo, "uploads/logos")
        saved.append(logo_path)
        banner_path = await save_uploaded_file(banner, "uploads/banners")
        saved.append(banner_path)

        # A browser sends a part for every <input type="file"> in the form
        # even when the visitor leaves it empty — that comes through as a
        # real UploadFile with filename="" and size 0, not None, and
        # UploadFile has no __bool__, so a plain `if bankQR:` is always
        # truthy and would call save_uploaded_file() on an empty file,
        # rejecting the whole submission. Only treat it as "provided" when
        # it actually has a filename.
        bank_qr_path = None
        if bankQR and bankQR.filename:
            bank_qr_path = await save_uploaded_file(bankQR, "uploads/bank_qr")
            saved.append(bank_qr_path)

        payment_qr_path = None
        if paymentQR and paymentQR.filename:
            payment_qr_path = await save_uploaded_file(paymentQR, "uploads/payment_qr")
            saved.append(payment_qr_path)

        shop_qr_path = None
        if shopQR and shopQR.filename:
            shop_qr_path = await save_uploaded_file(shopQR, "uploads/shop_qr")
            saved.append(shop_qr_path)

        # -------------------------
        # Prepare data for preview
        # -------------------------
        shop_data = {
            # Basic Info
            "shopName": shopName,
            "domain": domain,
            "shop_url": f"https://{domain}.avigronix.com",
            "about": about,
            "category": category,
            "businessType": businessType,

            # Contact
            "contactNumber": contactNumber,
            "email": email,
            "supportEmail": supportEmail,
            "gstNumber": gstNumber,

            # Branding
            "logo": logo_path,
            "banner": banner_path,

            # Address
            "address": address,
            "city": city,
            "state": state,
            "pincode": pincode,
            "location": location,

            # Social Links
            "whatsapp": whatsapp,
            "instagram": instagram,
            "facebook": facebook,
            "youtube": youtube,
            "website": website,

            # Bank
            "accountHolder": accountHolder,
            "bankName": bankName,
            "accountNumber": accountNumber,
            "ifscCode": ifscCode,
            "upiId": upiId,
            "bankQR": bank_qr_path,

            # QR Codes
            "paymentQR": payment_qr_path,
            "shopQR": shop_qr_path,

            # Settings
            "openingHours": openingHours,
            "shopStatus": shopStatus,
            "deliveryAvailable": deliveryAvailable,
        }
        return templates.TemplateResponse(
            request,
            "preview_business.html",
            {"shop_data": shop_data}
        )

    except HTTPException:
        _discard_uploads(saved)
        raise

    except Exception as e:
        _discard_uploads(saved)
        logger.error("Error processing shop preview: %s", e)
        raise HTTPException(
            status_code=500,
            detail="Error processing preview. Please try again."
        )


# Pydantic model matching the exact JSON structure
class ShopRegistration(BaseModel):
    shopName: str
    domain: str
    shop_url: Optional[str] = None
    about: str
    category: str
    businessType: str
    contactNumber: str
    email: str
    supportEmail: Optional[str] = None
    gstNumber: Optional[str] = None
    logo: str
    banner: str
    address: str
    city: str
    state: str
    pincode: str
    location: Optional[str] = None
    whatsapp: Optional[str] = None
    instagram: Optional[str] = None
    facebook: Optional[str] = None
    youtube: Optional[str] = None
    website: Optional[str] = None
    accountHolder: Optional[str] = None
    bankName: Optional[str] = None
    accountNumber: Optional[str] = None
    ifscCode: Optional[str] = None
    upiId: Optional[str] = None
    bankQR: Optional[str] = None
    paymentQR: Optional[str] = None
    shopQR: Optional[str] = None
    openingHours: Optional[str] = None
    shopStatus: Optional[str] = None
    deliveryAvailable: Optional[bool] = None

@router.post("/register-business")
@limiter.limit("10/10minutes")
async def register_business(request: Request, data: ShopRegistration):
    """Save business to MongoDB.

    `request` is required here for slowapi's rate limiter to find the
    caller's IP — it's not otherwise used now that validation goes through
    the ShopRegistration model below.

    Uses the ShopRegistration model above for real validation (required
    fields, types) instead of hand-parsing the JSON body — field names
    match exactly what preview_business.html's `shopData` object sends.
    """
    try:
        if not SUBDOMAIN_PATTERN.match(data.domain):
            raise HTTPException(
                status_code=400,
                detail="Subdomain can contain only lowercase letters, numbers, and hyphens."
            )

        # Validate subdomain availability
        if not await is_subdomain_available(data.domain):
            raise HTTPException(status_code=400, detail="Subdomain already taken")

        # Create shop document - map frontend field names to database field names
        shop_document = {
            "shop_name": data.shopName,
            "subdomain": data.domain,
            "contact_number": data.contactNumber,
            "email": data.email,
            "about": data.about,
            "category": data.category,
            "business_type": data.businessType,
            "logo": data.logo,
            "banner": data.banner,
            "shop_url": data.shop_url or f"https://{data.domain}.avigronix.com",
            "shop_qr": data.shopQR,
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
            # Address information
            "address": data.address,
            "city": data.city,
            "state": data.state,
            "pincode": data.pincode,
            # Business information
            "gst_number": data.gstNumber,
            "support_email": data.supportEmail,
            "opening_hours": data.openingHours,
            "shop_status": data.shopStatus or "active",
            "delivery_available": data.deliveryAvailable or False,
            # Social media
            "social_media": {
                "whatsapp": data.whatsapp,
                "instagram": data.instagram,
                "facebook": data.facebook,
                "youtube": data.youtube,
                "website": data.website,
            },
            # Payment information
            "payment_info": {
                "bank_name": data.bankName,
                "account_holder": data.accountHolder,
                "account_number": data.accountNumber,
                "ifsc_code": data.ifscCode,
                "upi_id": data.upiId,
                "bank_qr": data.bankQR,
                "payment_qr": data.paymentQR,
            },
            # Collections
            "products": [],
            "banners": []
        }

        # Remove None values
        shop_document = {k: v for k, v in shop_document.items() if v is not None}

        def subdomain_mapping(shop_id):
            return {
                "subdomain": data.domain,
                "shop_id": str(shop_id),
                "target_url": f"/shop/{data.domain}",
                "active": True,
                "created_at": datetime.now()
            }

        # Insert shop + subdomain mapping together (unique index on
        # `subdomain` created at startup catches the race the pre-check
        # above can't)
        try:
            result = await _insert_shop_with_mapping(shop_document, subdomain_mapping)
        except DuplicateKeyError:
            raise HTTPException(status_code=400, detail="Subdomain already taken")

        return {
            "success": True,
            "message": "Business registered successfully",
            "shop_id": str(result.inserted_id),
            "shop_url": shop_document["shop_url"],
            "shop_qr": shop_document.get("shop_qr")
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error registering business: %s", e)
        raise HTTPException(status_code=500, detail="Error registering business. Please try again.")

@router.get("/{subdomain}", response_class=HTMLResponse)
async def public_shop_page(subdomain: str, request: Request):
    """Public shop page accessible via subdomain"""
    try:
        # Find shop by subdomain
        shop = await db.shops.find_one({"subdomain": subdomain, "shop_status": "active"})
        
        if not shop:
            raise HTTPException(status_code=404, detail="Shop not found or inactive")
        
        # Convert ObjectId to string for JSON serialization
        shop["_id"] = str(shop["_id"])
        
        # Prepare shop data for template
        shop_data = {
            "shop_name": shop["shop_name"],
            "subdomain": shop["subdomain"],
            "contact_number": shop["contact_number"],
            "email": shop["email"],
            "about": shop["about"],
            "category": shop["category"],
            "business_type": shop["business_type"],
            "logo": shop["logo"],
            "banner": shop["banner"],
            "products": shop.get("products", []),
            "banners": shop.get("banners", []),
            "shop_url": shop.get("shop_url", f"https://{subdomain}.avigronix.com"),
            "created_at": shop["created_at"]
        }
        
        return templates.TemplateResponse(request, "shop_public.html", {
            "shop_data": shop_data
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error loading shop '%s': %s", subdomain, e)
        raise HTTPException(status_code=500, detail="Error loading shop. Please try again.")

@router.get("/api/shop/{subdomain}", response_model=PublicShopAPIResponse)
async def get_shop_data(subdomain: str):
    """API endpoint to get shop data (for potential mobile app).

    Public by design (same as the storefront page), but only ever returns
    the exact fields shop_public.html already renders for any visitor —
    never payment/bank details, address, social links, or internal/_id
    fields.
    """
    try:
        shop = await db.shops.find_one({"subdomain": subdomain, "shop_status": "active"})

        if not shop:
            raise HTTPException(status_code=404, detail="Shop not found")

        return {
            "shop_name": shop["shop_name"],
            "contact_number": shop["contact_number"],
            "email": shop["email"],
            "about": shop["about"],
            "category": shop["category"],
            "business_type": shop["business_type"],
            "logo": shop["logo"],
            "banner": shop["banner"],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error fetching shop data for '%s': %s", subdomain, e)
        raise HTTPException(status_code=500, detail="Error fetching shop data. Please try again.")

@router.get("/api/shops", dependencies=[Depends(require_admin_key)])
async def list_all_shops(skip: int = 0, limit: int = 50):
    """Admin-only: list all shops. Requires the X-Admin-Key header."""
    try:
        limit = min(limit, 50)
        shops = []
        cursor = db.shops.find({"shop_status": "active"}).skip(skip).limit(limit)

        async for shop in cursor:
            shop["_id"] = str(shop["_id"])
            shops.append(shop)

        return {"shops": shops, "total": len(shops)}

    except Exception as e:
        logger.error("Error fetching shops list: %s", e)
        raise HTTPException(status_code=500, detail="Error fetching shops. Please try again.")

@router.put("/api/shop/{subdomain}/status", dependencies=[Depends(require_admin_key)])
async def update_shop_status(subdomain: str, status: str):
    """Admin-only: update shop status (active/inactive). Requires the X-Admin-Key header."""
    try:
        if status not in ["active", "inactive"]:
            raise HTTPException(status_code=400, detail="Status must be 'active' or 'inactive'")
        
        result = await db.shops.update_one(
            {"subdomain": subdomain},
            {"$set": {"shop_status": status, "updated_at": datetime.now()}}
        )
        
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="Shop not found")
        
        return {"message": f"Shop status updated to {status}"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error updating status for shop '%s': %s", subdomain, e)
        raise HTTPException(status_code=500, detail="Error updating shop status. Please try again.")
