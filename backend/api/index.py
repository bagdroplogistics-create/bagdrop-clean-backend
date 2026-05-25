from fastapi import FastAPI, APIRouter, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

import os
import logging
import random
import smtplib
import ssl
import uuid

from email.message import EmailMessage
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime, timezone

# =========================
# Load Environment Variables
# =========================
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

# =========================
# FastAPI App
# =========================
app = FastAPI(title="Bagdrop Booking API")

# =========================
# CORS
# =========================
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://bagdrop-app.vercel.app",
        "http://localhost:3000",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# API Router
# =========================
api_router = APIRouter(prefix="/api")

# =========================
# MongoDB Connection
# =========================
mongo_url = os.environ.get("MONGO_URL")

if not mongo_url:
    raise Exception("MONGO_URL environment variable not found")

db_name = os.environ.get("DB_NAME", "bagdrop")

client = AsyncIOMotorClient(mongo_url)
db = client[db_name]

# =========================
# Helpers
# =========================
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_booking_code() -> str:
    return f"BD{random.randint(10000, 99999)}"


STATUS_FLOW = [
    "Booked",
    "Picked Up",
    "In Transit",
    "Out for Delivery",
    "Delivered",
]

# =========================
# Email Configuration
# =========================
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "info@bagdrop.co")

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get(
    "SMTP_FROM",
    SMTP_USER or "noreply@bagdrop.co"
)

# =========================
# Email Formatting
# =========================
def _format_booking_email(b: dict) -> str:
    lines = [
        f"New booking received - {b.get('code')}",
        "",
        f"Service:        {b.get('service_title')}",
        f"Booking ID:     {b.get('code')}",
        f"Status:         {b.get('status')}",
        "",
        "Customer",
        f"  Name:         {b.get('name')}",
        f"  Phone:        {b.get('phone')}",
        f"  Email:        {b.get('email') or '-'}",
        "",
        "Route",
        f"  Pickup:       {b.get('pickup_address')}",
        f"                {b.get('from_label') or b.get('from_location_id') or ''}",
        f"  Drop:         {b.get('drop_address')}",
        f"                {b.get('to_label') or b.get('to_location_id') or ''}",
        "",
        "Schedule",
        f"  Pickup date:  {b.get('date')} ({b.get('time_slot')})",
        f"  Drop date:    {b.get('drop_date') or '-'}",
        "",
        "Bags & Pricing",
        f"  Total bags:   {b.get('total_bags')}",
        f"  Total price:  Rs.{b.get('total_price')}",
        "",
        "Selections:",
    ]

    for k, v in (b.get("bag_selections") or {}).items():
        lines.append(f"  - {k}: {v}")

    lines += [
        "",
        f"Created at: {b.get('created_at')}",
        "",
        "-- Bagdrop Booking System",
    ]

    return "\n".join(lines)

# =========================
# Send Email
# =========================
def send_booking_email(b: dict) -> None:
    logger.info("Starting email send process...")

    logger.info(f"SMTP_HOST: {SMTP_HOST}")
    logger.info(f"SMTP_PORT: {SMTP_PORT}")
    logger.info(f"SMTP_USER: {SMTP_USER}")
    logger.info(f"ADMIN_EMAIL: {ADMIN_EMAIL}")

    if not (SMTP_HOST and SMTP_USER and SMTP_PASS):
        logger.info(
            "SMTP not configured - skipping email for booking %s",
            b.get("code")
        )
        return

    try:
        msg = EmailMessage()

        msg["Subject"] = (
            f"[Bagdrop] New Booking "
            f"{b.get('code')} - "
            f"{b.get('service_title')}"
        )

        msg["From"] = SMTP_FROM

        # TEMP direct hardcoded test
        msg["To"] = "info@bagdrop.co"

        if b.get("email"):
            msg["Cc"] = b["email"]

   
html_content = f"""
<html>
<head>
  <style>
    body {{
      font-family: Arial, sans-serif;
      background-color: #f4f4f4;
      padding: 20px;
      color: #333333;
    }}

    .container {{
      max-width: 700px;
      margin: auto;
      background: #ffffff;
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 2px 10px rgba(0,0,0,0.1);
    }}

    .header {{
      background: #f97316;
      color: white;
      padding: 24px;
      text-align: center;
    }}

    .header h1 {{
      margin: 0;
      font-size: 28px;
    }}

    .content {{
      padding: 24px;
    }}

    .section {{
      margin-bottom: 24px;
    }}

    .section h2 {{
      font-size: 18px;
      margin-bottom: 12px;
      color: #f97316;
      border-bottom: 1px solid #eee;
      padding-bottom: 6px;
    }}

    .row {{
      margin-bottom: 8px;
    }}

    .label {{
      font-weight: bold;
      display: inline-block;
      width: 140px;
    }}

    .footer {{
      background: #fafafa;
      padding: 20px;
      text-align: center;
      font-size: 13px;
      color: #777;
    }}

    .badge {{
      display: inline-block;
      background: #16a34a;
      color: white;
      padding: 6px 12px;
      border-radius: 20px;
      font-size: 13px;
      margin-top: 10px;
    }}
  </style>
</head>

<body>
  <div class="container">

    <div class="header">
      <h1>Bagdrop Booking</h1>
      <div class="badge">{b.get('status')}</div>
    </div>

    <div class="content">

      <div class="section">
        <h2>Booking Information</h2>

        <div class="row">
          <span class="label">Booking ID:</span>
          {b.get('code')}
        </div>

        <div class="row">
          <span class="label">Service:</span>
          {b.get('service_title')}
        </div>

        <div class="row">
          <span class="label">Pickup Date:</span>
          {b.get('date')}
        </div>

        <div class="row">
          <span class="label">Time Slot:</span>
          {b.get('time_slot')}
        </div>
      </div>

      <div class="section">
        <h2>Customer Details</h2>

        <div class="row">
          <span class="label">Name:</span>
          {b.get('name')}
        </div>

        <div class="row">
          <span class="label">Phone:</span>
          {b.get('phone')}
        </div>

        <div class="row">
          <span class="label">Email:</span>
          {b.get('email')}
        </div>
      </div>

      <div class="section">
        <h2>Route Details</h2>

        <div class="row">
          <span class="label">Pickup:</span>
          {b.get('pickup_address')}
        </div>

        <div class="row">
          <span class="label">From:</span>
          {b.get('from_label')}
        </div>

        <div class="row">
          <span class="label">Drop:</span>
          {b.get('drop_address')}
        </div>

        <div class="row">
          <span class="label">To:</span>
          {b.get('to_label')}
        </div>
      </div>

      <div class="section">
        <h2>Pricing</h2>

        <div class="row">
          <span class="label">Total Bags:</span>
          {b.get('total_bags')}
        </div>

        <div class="row">
          <span class="label">Total Price:</span>
          ₹{b.get('total_price')}
        </div>
      </div>

    </div>

    <div class="footer">
      © 2026 Bagdrop Logistics — Secure Airport Baggage Delivery
    </div>

  </div>
</body>
</html>
"""

msg.set_content(_format_booking_email(b))
msg.add_alternative(html_content, subtype="html")



        ctx = ssl.create_default_context()

        logger.info("Connecting to SMTP server...")

        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(
                SMTP_HOST,
                SMTP_PORT,
                context=ctx,
                timeout=15
            ) as s:
                logger.info("SMTP SSL connected")

                s.login(SMTP_USER, SMTP_PASS)

                logger.info("SMTP login successful")

                s.send_message(msg)

                logger.info("Email sent successfully")

        else:
            with smtplib.SMTP(
                SMTP_HOST,
                SMTP_PORT,
                timeout=15
            ) as s:

                logger.info("SMTP connected")

                s.starttls(context=ctx)

                logger.info("TLS started")

                s.login(SMTP_USER, SMTP_PASS)

                logger.info("SMTP login successful")

                s.send_message(msg)

                logger.info("Email sent successfully")

        logger.info(
            "Sent booking email for %s to %s",
            b.get("code"),
            ADMIN_EMAIL
        )

    except Exception as e:
        logger.error(
            "Failed to send booking email for %s: %s",
            b.get("code"),
            str(e)
        )


# =========================
# Pydantic Models
# =========================
class BookingCreate(BaseModel):
    client_id: Optional[str] = "guest"

    service_id: str
    service_title: str
    service_color: str = "orange"

    from_location_id: Optional[str] = None
    to_location_id: Optional[str] = None

    from_label: Optional[str] = None
    to_label: Optional[str] = None

    pickup_address: str
    drop_address: str

    date: str
    drop_date: Optional[str] = ""

    time_slot: str

    bag_selections: Dict[str, int] = Field(default_factory=dict)

    total_bags: int
    total_price: int

    name: str
    phone: str
    email: Optional[str] = ""


class Booking(BookingCreate):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    code: str = Field(default_factory=generate_booking_code)

    status: str = "Booked"
    stage_index: int = 0

    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class StatusUpdate(BaseModel):
    status: Optional[str] = None
    stage_index: Optional[int] = None

# =========================
# Mongo Helpers
# =========================
def serialize(doc: dict) -> dict:
    if not doc:
        return doc

    doc.pop("_id", None)
    return doc


async def find_booking_by_any_id(identifier: str):
    doc = await db.bookings.find_one({
        "code": identifier.upper()
    })

    if doc:
        return doc

    return await db.bookings.find_one({
        "id": identifier
    })

# =========================
# Routes
# =========================
@api_router.get("/")
async def root():
    return {
        "message": "Bagdrop API",
        "status": "ok"
    }

@api_router.get("/test")
async def test():
    return {
        "working": True
    }


@api_router.post("/bookings", response_model=Booking)
async def create_booking(
    payload: BookingCreate,
    background: BackgroundTasks
):
    code = generate_booking_code()

    booking = Booking(
        **payload.dict(),
        code=code
    )

    await db.bookings.insert_one(
        booking.dict()
    )

    try:
        background.add_task(
            send_booking_email,
            booking.dict()
        )
    except Exception as email_error:
        logger.error(f"EMAIL ERROR: {str(email_error)}")

    return booking


@api_router.get("/bookings", response_model=List[Booking])
async def list_bookings(
    client_id: Optional[str] = Query("guest")
):
    cursor = db.bookings.find({
        "client_id": client_id
    }).sort("created_at", -1)

    docs = await cursor.to_list(500)

    return [
        Booking(**serialize(d))
        for d in docs
    ]


@api_router.get("/bookings/{identifier}", response_model=Booking)
async def get_booking(identifier: str):
    doc = await find_booking_by_any_id(identifier)

    if not doc:
        raise HTTPException(
            status_code=404,
            detail="Booking not found"
        )

    return Booking(**serialize(doc))


@api_router.patch(
    "/bookings/{identifier}/status",
    response_model=Booking
)
async def update_status(
    identifier: str,
    body: StatusUpdate
):
    doc = await find_booking_by_any_id(identifier)

    if not doc:
        raise HTTPException(
            status_code=404,
            detail="Booking not found"
        )

    update = {
        "updated_at": now_iso()
    }

    if body.stage_index is not None:
        idx = max(
            0,
            min(body.stage_index, len(STATUS_FLOW) - 1)
        )

        update["stage_index"] = idx
        update["status"] = STATUS_FLOW[idx]

    elif body.status is not None:
        update["status"] = body.status

        if body.status in STATUS_FLOW:
            update["stage_index"] = STATUS_FLOW.index(
                body.status
            )

    await db.bookings.update_one(
        {"id": doc["id"]},
        {"$set": update}
    )

    fresh = await db.bookings.find_one({
        "id": doc["id"]
    })

    return Booking(**serialize(fresh))


@api_router.delete("/bookings/{identifier}")
async def cancel_booking(identifier: str):
    doc = await find_booking_by_any_id(identifier)

    if not doc:
        raise HTTPException(
            status_code=404,
            detail="Booking not found"
        )

    await db.bookings.update_one(
        {"id": doc["id"]},
        {
            "$set": {
                "status": "Cancelled",
                "updated_at": now_iso(),
            }
        },
    )

    return {
        "ok": True,
        "id": doc["id"]
    }


@api_router.get("/track/{code}", response_model=Booking)
async def track(code: str):
    doc = await find_booking_by_any_id(code)

    if not doc:
        raise HTTPException(
            status_code=404,
            detail="Booking not found"
        )

    return Booking(**serialize(doc))

# =========================
# CORS
# =========================
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://bagdrop-app.vercel.app",
        "http://localhost:3000",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# Register Router
# =========================
app.include_router(api_router)



# =========================
# Logging
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)

# =========================
# Shutdown Event
# =========================
@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()

# =========================
# Vercel ASGI Handler
# =========================

app.include_router(api_router)


