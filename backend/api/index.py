from fastapi import FastAPI, APIRouter, HTTPException, Query, BackgroundTasks
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import random
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
import uuid
from datetime import datetime, timezone

# =========================
# Load Environment Variables
# =========================
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

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
# FastAPI App
# =========================
app = FastAPI(title="Bagdrop Booking API")

api_router = APIRouter(prefix="/api")

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
        msg["To"] = ADMIN_EMAIL

        if b.get("email"):
            msg["Cc"] = b["email"]

        msg.set_content(_format_booking_email(b))

        ctx = ssl.create_default_context()

        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(
                SMTP_HOST,
                SMTP_PORT,
                context=ctx,
                timeout=15
            ) as s:
                s.login(SMTP_USER, SMTP_PASS)
                s.send_message(msg)
        else:
            with smtplib.SMTP(
                SMTP_HOST,
                SMTP_PORT,
                timeout=15
            ) as s:
                s.starttls(context=ctx)
                s.login(SMTP_USER, SMTP_PASS)
                s.send_message(msg)

        logger.info(
            "Sent booking email for %s to %s",
            b.get("code"),
            ADMIN_EMAIL
        )

    except Exception as e:
        logger.error(
            "Failed to send booking email for %s: %s",
            b.get("code"),
            e
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


@api_router.post("/bookings", response_model=Booking)
async def create_booking(
    payload: BookingCreate,
    background: BackgroundTasks
):
    code = generate_booking_code()

    for _ in range(5):
        exists = await db.bookings.find_one({
            "code": code
        })

        if not exists:
            break

        code = generate_booking_code()

    booking = Booking(
        **payload.dict(),
        code=code
    )

    await db.bookings.insert_one(
        booking.dict()
    )

    background.add_task(
        send_booking_email,
        booking.dict()
    )

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
# Register Router
# =========================
app.include_router(api_router)

# =========================
# CORS
# =========================
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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


