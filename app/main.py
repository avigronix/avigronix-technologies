from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, PlainTextResponse, FileResponse, JSONResponse, HTMLResponse
from routers import pages
from routers.shop_manages import router as shop_manage
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()


from routers.shop_manages import db

from urllib.parse import urlparse
import json
from pydantic import BaseModel, EmailStr
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib


app = FastAPI(
    title="AVIGRONIX TECHNOLOGIES",
    description="Transform Your Business With Our Digital Solutions",
    version="1.0.0"
)




SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")


def send_email(to: str, subject: str, body: str) -> bool:
    """
    Common reusable function:
    - to: receiver email
    - subject: mail subject
    - body: HTML/string body
    """
    try:
        msg = MIMEMultipart()
        msg["From"] = SMTP_USER
        msg["To"] = to
        msg["Subject"] = subject

        # body as HTML
        msg.attach(MIMEText(body, "html"))

        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
        server.quit()

        return True
    except Exception as e:
        print("Email Error:", e)
        return False
    
class ContactForm(BaseModel):
    full_name: str
    email: EmailStr
    phone: str
    company: str | None = None
    service: str
    message: str

def extract_subdomain(hostname: str) -> str:
    """Extract subdomain safely"""
    if not hostname:
        return ""

    hostname = hostname.split(":")[0]  # remove port if present

    # Localhost root — no subdomain
    if hostname in ["localhost", "127.0.0.1"]:
        return ""

    # subdomain.localhost
    if hostname.endswith(".localhost"):
        return hostname.split(".")[0]

    # ngrok
    if ".ngrok-free.app" in hostname:
        return hostname.split(".")[0]

    # Production domain
    if hostname.endswith(".avigronix.com"):
        parts = hostname.split(".")
        if len(parts) == 3:  
            sub = parts[0]
            if sub not in ["www", "avigronix"]:
                return sub

    return ""


@app.middleware("http")
async def shop_subdomain_middleware(request: Request, call_next):
    origin = request.headers.get("origin")
    host = request.headers.get("host", "")

    # Extract subdomain
    origin_sub = extract_subdomain(urlparse(origin).hostname if origin else "")
    host_sub = extract_subdomain(host)
    subdomain = origin_sub or host_sub

    print(f"🌐 Host={host} | Origin={origin} | Subdomain={subdomain}")

    request.state.subdomain = subdomain
    request.state.shop = None
    request.state.isAvailableSubDomain = False

    # Allow OPTIONS (CORS Preflight)
    if request.method == "OPTIONS":
        response = await call_next(request)
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            response.headers["Access-Control-Allow-Methods"] = "*"
        return response

    # Subdomain exists → fetch shop
    if subdomain:
        shop = await db.shops.find_one({
            "subdomain": subdomain,
            "shop_status": "active"
        })

        if not shop:
            return templates.TemplateResponse(
                "not_found.html",
                {
                    "request": request,
                    "subdomain": subdomain,
                    "msg": f"Shop '{subdomain}' not found"
                }
            )

        # Attach in request.state
        request.state.shop = shop
        request.state.isAvailableSubDomain = True

    # Call the next route
    response = await call_next(request)

    # Add CORS headers for local development only
    if origin and ("localhost" in origin or "127.0.0.1" in origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"

    return response

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templates
templates = Jinja2Templates(directory="templates")


templates.env.globals['current_year'] = datetime.now().year
templates.env.globals['site_url'] = "https://avigronix.com"

# Include routers
app.include_router(pages.router)
app.include_router(shop_manage)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    # If request is from subdomain → return shop data JSON
    if getattr(request.state, "isAvailableSubDomain", False):
        # print(request.state.shop ,"request.state.shop ")
        return templates.TemplateResponse(
            "public_shop.html",
            {"request": request, "shop_data": request.state.shop }
        )
        return JSONResponse({
            "msg": "Shop data fetched successfully",
            "status": 200,
            "isAvailableSubDomain": True,
            "data": request.state.shop   # ← DB result sent to frontend
        })

    # Otherwise show normal homepage
    return templates.TemplateResponse("index.html", {"request": request})

# @app.get("/", response_class=HTMLResponse)
# async def home(request: Request):
#     return templates.TemplateResponse("index.html", {"request": request})

@app.get("/uploads/{sub_folder}/{filename}")
async def get_image(sub_folder: str, filename: str):

    file_path = os.path.join("uploads", sub_folder, filename)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Image not found")
    
    return FileResponse(file_path)

@app.get("/static/{sub_folder}/{filename}")
async def get_image(sub_folder: str, filename: str):

    file_path = os.path.join("uploads", sub_folder, filename)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Image not found")
    
    return FileResponse(file_path)

@app.get("/sitemap.xml", include_in_schema=False)
def sitemap():
    return FileResponse("static/sitemap.xml", media_type="application/xml")

@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots():
    content = """
User-agent: *
Allow: /

Sitemap: https://avigronix.com/sitemap.xml
"""
    return content.strip()

@app.post("/api/contact")
async def send_contact_message(data: ContactForm):

    # -----------------------------
    # 1. ADMIN MAIL
    # -----------------------------
    admin_subject = f"📬 New Contact Inquiry from {data.full_name}"

    admin_body = f"""
    <div style="font-family: Arial, sans-serif; padding: 20px; color: #333;">
        <h2 style="color: #0F62FE;">New Contact Form Submission</h2>
        <p>You have received a new inquiry from your website.</p>
        <hr style="border:none;border-top:1px solid #eee;margin:20px 0;" />

        <p><strong>Name:</strong> {data.full_name}</p>
        <p><strong>Email:</strong> {data.email}</p>
        <p><strong>Phone:</strong> {data.phone}</p>
        <p><strong>Company:</strong> {data.company or "Not Provided"}</p>
        <p><strong>Service Interested:</strong> {data.service}</p>

        <p style="margin-top:15px;"><strong>Message:</strong></p>
        <div style="background:#f7f7f7;padding:12px;border-radius:6px;">
            {data.message}
        </div>

        <br>
        <p style="font-size: 13px; color: #777;">
            Sent automatically from AVIGRONIX TECHNOLOGIES contact form.
        </p>
    </div>
    """

    ok1 = send_email(
        to="itwebtechinfo@gmail.com",
        subject=admin_subject,
        body=admin_body
    )

    if not ok1:
        raise HTTPException(500, "Failed to send admin email")


    # -----------------------------
    # 2. USER ACKNOWLEDGMENT MAIL
    # -----------------------------
    user_subject = "Thank You for Contacting AVIGRONIX TECHNOLOGIES"

    user_body = f"""
    <div style="font-family: Arial, sans-serif; padding: 20px; color: #333;">
        <h2 style="color:#0F62FE;">We Received Your Message, {data.full_name}!</h2>

        <p>Thank you for reaching out to us. Our team will get back to you within 24 hours.</p>

        <h3 style="margin-top:20px;">Your Submitted Details:</h3>

        <p><strong>Email:</strong> {data.email}</p>
        <p><strong>Phone:</strong> {data.phone}</p>
        <p><strong>Service Interested:</strong> {data.service}</p>

        <p><strong>Your Message:</strong></p>
        <div style="background:#f7f7f7;padding:12px;border-radius:6px;">
            {data.message}
        </div>

        <br>
        <p style="color:#666;font-size:14px;">
            You can contact us anytime at 
            <a href="mailto:itwebtechinfo@gmail.com">itwebtechinfo@gmail.com</a>.
            <br><br>
            — AVIGRONIX TECHNOLOGIES
        </p>
    </div>
    """

    ok2 = send_email(
        to=data.email,  
        subject=user_subject,
        body=user_body
    )

    if not ok2:
        raise HTTPException(500, "Failed to send acknowledgment email")

    return {"status": 200, "msg": "Message sent successfully"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)