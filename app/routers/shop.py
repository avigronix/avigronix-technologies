from fastapi import FastAPI, APIRouter, Request, Form, UploadFile, File, HTTPException, Depends
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, EmailStr
from typing import Optional, List, Dict, Any
from pathlib import Path
from datetime import datetime
import uuid
import os
import shutil
from bson import ObjectId
import json

from database import db
from rate_limit import limiter

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

# Utility Functions
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


async def save_uploaded_file(file: UploadFile, upload_dir: str) -> str:
    """Save uploaded file and return file path.

    Uses a generated filename rather than the client-supplied one to avoid
    path traversal / overwriting other uploads on this pre-auth form.
    """
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_IMAGE_EXTENSIONS))}",
        )

    filename = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(upload_dir, filename)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return f"/uploads/{upload_dir.split('/')[-1]}/{filename}"

async def is_subdomain_available(subdomain: str) -> bool:
    """Check if subdomain is available"""
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
    return templates.TemplateResponse("register_business.html", {"request": request})

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

        bank_qr_path = None
        if bankQR:
            bank_qr_path = await save_uploaded_file(bankQR, "uploads/bank_qr")

        payment_qr_path = None
        if paymentQR:
            payment_qr_path = await save_uploaded_file(paymentQR, "uploads/payment_qr")

        shop_qr_path = None
        if shopQR:
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
            "preview_business.html",
            {"request": request, "shop_data": shop_data}
        )

    except HTTPException:
        raise

    except Exception as e:
        print("Preview Error:", e)
        raise HTTPException(
            status_code=500,
            detail=f"Error processing preview: {str(e)}"
        )


from fastapi import Form, UploadFile, File, HTTPException, APIRouter
from datetime import datetime
import os
from typing import Optional

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
async def register_business(request: Request):
    """Save business to MongoDB - JSON only"""
    try:
        # Parse JSON data from request body
        body = await request.body()
        json_data = json.loads(body)
        
        print("Received raw JSON data:", json_data)
        
        # Validate required fields
        if not json_data.get('shopName') or not json_data.get('domain'):
            raise HTTPException(status_code=400, detail="Shop name and domain are required")
        
        # Validate subdomain availability
        if not await is_subdomain_available(json_data['domain']):
            raise HTTPException(status_code=400, detail="Subdomain already taken")
        
        # Create shop document - map frontend field names to database field names
        shop_document = {
            "shop_name": json_data['shopName'],
            "subdomain": json_data['domain'],
            "contact_number": json_data['contactNumber'],
            "email": json_data['email'],
            "about": json_data['about'],
            "category": json_data['category'],
            "business_type": json_data['businessType'],
            "logo": json_data['logo'],
            "banner": json_data['banner'],
            "shop_status": "active",
            "shop_url": json_data.get('shop_url') or f"https://{json_data['domain']}.avigronix.com",
            "shop_qr": json_data.get('shopQR'),
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
            # Address information
            "address": json_data['address'],
            "city": json_data['city'],
            "state": json_data['state'],
            "pincode": json_data['pincode'],
            # Business information
            "gst_number": json_data.get('gstNumber'),
            "support_email": json_data.get('supportEmail'),
            "opening_hours": json_data.get('openingHours'),
            "shop_status": json_data.get('shopStatus') or "active",
            "delivery_available": json_data.get('deliveryAvailable') or False,
            # Social media
            "social_media": {
                "whatsapp": json_data.get('whatsapp'),
                "instagram": json_data.get('instagram'),
                "facebook": json_data.get('facebook'),
                "youtube": json_data.get('youtube'),
                "website": json_data.get('website')
            },
            # Payment information
            "payment_info": {
                "bank_name": json_data.get('bankName'),
                "account_holder": json_data.get('accountHolder'),
                "account_number": json_data.get('accountNumber'),
                "ifsc_code": json_data.get('ifscCode'),
                "upi_id": json_data.get('upiId'),
                "bank_qr": json_data.get('bankQR'),
                "payment_qr": json_data.get('paymentQR')
            },
            # Collections
            "products": [],
            "banners": []
        }
        
        # Remove None values
        shop_document = {k: v for k, v in shop_document.items() if v is not None}
        
        # Insert into MongoDB
        result = await db.shops.insert_one(shop_document)
        
        # Create subdomain mapping
        subdomain_doc = {
            "subdomain": json_data['domain'],
            "shop_id": str(result.inserted_id),
            "target_url": f"/shop/{json_data['domain']}",
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
        
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON data: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error registering business: {str(e)}")

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
        
        return templates.TemplateResponse("shop_public.html", {
            "request": request, 
            "shop_data": shop_data
        })
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading shop: {str(e)}")

@router.get("/api/shop/{subdomain}")
async def get_shop_data(subdomain: str):
    """API endpoint to get shop data (for potential mobile app)"""
    try:
        shop = await db.shops.find_one({"subdomain": subdomain, "shop_status": "active"})
        
        if not shop:
            raise HTTPException(status_code=404, detail="Shop not found")
        
        # Convert ObjectId to string
        shop["_id"] = str(shop["_id"])
        
        return JSONResponse(content=shop)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching shop data: {str(e)}")

@router.get("/api/shops")
async def list_all_shops(skip: int = 0, limit: int = 50):
    """API endpoint to list all shops (for admin purposes)"""
    try:
        shops = []
        cursor = db.shops.find({"shop_status": "active"}).skip(skip).limit(limit)
        
        async for shop in cursor:
            shop["_id"] = str(shop["_id"])
            shops.append(shop)
        
        return {"shops": shops, "total": len(shops)}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching shops: {str(e)}")

@router.put("/api/shop/{subdomain}/status")
async def update_shop_status(subdomain: str, status: str):
    """Update shop status (active/inactive)"""
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
        raise HTTPException(status_code=500, detail=f"Error updating shop status: {str(e)}")
