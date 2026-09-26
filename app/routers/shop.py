from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException, Depends, Header
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional, List
from pathlib import Path
from datetime import datetime
import uuid
import os
import secrets
import logging
from pymongo.errors import DuplicateKeyError

from database import db
from rate_limit import limiter

logger = logging.getLogger("avigronix.shop")

# Router for shop endpoints
router = APIRouter(prefix="/shop", tags=["shop"])
templates = Jinja2Templates(directory="templates")
templates.env.globals["current_year"] = datetime.now().year

# Ensure upload directories exist
os.makedirs("uploads/logos", exist_ok=True)
os.makedirs("uploads/banners", exist_ok=True)
os.makedirs("uploads/bank_qr", exist_ok=True)
os.makedirs("uploads/payment_qr", exist_ok=True)
os.makedirs("uploads/shop_qr", exist_ok=True)

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
class Product(BaseModel):
    id: str
    image: str
    title: str
    description: str
    price: float
    discountPrice: Optional[float] = None
    rating: float = 0.0
    category: str

class Banner(BaseModel):
    id: str
    title: str
    image: str
    redirectUrl: str
    active: bool = True

class ShopDetails(BaseModel):
    shop_name: str
    subdomain: str
    contact_number: str
    email: str
    about: str
    category: str
    business_type: str
    logo: str
    banner: str
    products: List[Product] = []
    banners: List[Banner] = []
    shop_status: str = "active"
    created_at: datetime = datetime.now()
    updated_at: datetime = datetime.now()

class ShopDetailsResponse(BaseModel):
    id: str
    shop_name: str
    subdomain: str
    contact_number: str
    email: str
    about: str
    category: str
    business_type: str
    logo: str
    banner: str
    shop_url: str
    created_at: datetime

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
    Streams the upload in chunks and aborts (deleting the partial file) if
    it exceeds MAX_UPLOAD_SIZE, so a single request can't fill the disk.
    """
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_IMAGE_EXTENSIONS))}",
        )

    filename = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(upload_dir, filename)

    size = 0
    with open(file_path, "wb") as buffer:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_SIZE:
                buffer.close()
                os.remove(file_path)
                raise HTTPException(
                    status_code=400,
                    detail=f"'{file.filename}' is too large. Maximum allowed size is 5 MB.",
                )
            buffer.write(chunk)

    return f"/uploads/{upload_dir.split('/')[-1]}/{filename}"

async def is_subdomain_available(subdomain: str) -> bool:
    """Check if subdomain is available"""
    if subdomain.lower() in RESERVED_SUBDOMAINS:
        return False
    existing_shop = await db.shops.find_one({"subdomain": subdomain})
    return existing_shop is None

async def generate_shop_qr_code(shop_url: str) -> str:
    """Generate QR code for shop URL"""
    # For now, using a QR code service URL
    # You can implement actual QR generation later
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={shop_url}"
    return qr_url

# Routes
@router.get("/register", response_class=HTMLResponse)
async def show_registration_form(request: Request):
    """Show business registration form"""
    return templates.TemplateResponse(request, "register_business.html")

import re
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
    try:
        # -------------------------
        # Validate subdomain
        # -------------------------
        if not re.match(r"^[a-z0-9-]+$", domain):
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
        banner_path = await save_uploaded_file(banner, "uploads/banners")

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

        payment_qr_path = None
        if paymentQR and paymentQR.filename:
            payment_qr_path = await save_uploaded_file(paymentQR, "uploads/payment_qr")

        shop_qr_path = None
        if shopQR and shopQR.filename:
            shop_qr_path = await save_uploaded_file(shopQR, "uploads/shop_qr")

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
        # print(shop_data,"shop_datashop_datashop_data")
        return templates.TemplateResponse(
            request,
            "preview_business.html",
            {"shop_data": shop_data}
        )

    except HTTPException:
        raise

    except Exception as e:
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
            "shop_status": "active",
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

        # Insert into MongoDB (unique index on `subdomain` created at startup
        # catches the race the pre-check above can't)
        try:
            result = await db.shops.insert_one(shop_document)
        except DuplicateKeyError:
            raise HTTPException(status_code=400, detail="Subdomain already taken")

        # Create subdomain mapping
        subdomain_doc = {
            "subdomain": data.domain,
            "shop_id": str(result.inserted_id),
            "target_url": f"/shop/{data.domain}",
            "active": True,
            "created_at": datetime.now()
        }
        await db.subdomains.insert_one(subdomain_doc)

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
