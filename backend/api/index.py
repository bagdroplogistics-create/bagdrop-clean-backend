from fastapi import FastAPI, APIRouter, HTTPException, Query, BackgroundTasks, Depends
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
from datetime import datetime, timezone, timedelta

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

app.add_middleware( CORSMiddleware, allow_origins=[ "https://bagdrop-app.vercel.app", "http://localhost:3000" ], allow_credentials=True, allow_methods=["*"], allow_headers=["*"], ) 

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

# ============== Auth ==============
OTP_TTL_SECONDS = 5 * 60  # 5 minutes


def generate_otp() -> str:
    """MOCKED OTP: random 6-digit code returned to the client (no SMS sent)."""
    return f"{random.randint(0, 999999):06d}"


def normalize_phone(raw: str) -> str:
    if not raw:
        return raw
    s = "".join(ch for ch in raw if ch.isdigit() or ch == "+")
    if s.startswith("+"):
        return s
    # default to India country code if 10 digits
    if len(s) == 10:
        return "+91" + s
    return s


def make_token() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex


class OtpRequest(BaseModel):
    phone: str


class OtpVerify(BaseModel):
    phone: str
    code: str
    name: Optional[str] = None


class User(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    phone: str
    name: Optional[str] = ""
    email: Optional[str] = ""
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    
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
     
    lines += ["", f"Created at: {b.get('created_at')}", "", "-- Bagdrop Booking System"]
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

# ============== Routes ==============
@api_router.get("/")
async def root():
    return {"message": "Bagdrop API", "status": "ok"}


# ----- Auth -----
@api_router.post("/auth/request-otp")
async def request_otp(payload: OtpRequest):
    phone = normalize_phone(payload.phone)
    if not phone or len(phone) < 10:
        raise HTTPException(status_code=400, detail="Invalid phone number")
    # Store/replace OTP for this phone
    code = generate_otp()
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=OTP_TTL_SECONDS)).isoformat()
    await db.otps.update_one(
        {"phone": phone},
        {"$set": {
            "phone": phone,
            "code": code,
            "expires_at": expires_at,
            "created_at": now_iso(),
            "attempts": 0,
        }},
        upsert=True,
    )
    logger.info("MOCKED OTP for %s -> %s (expires %s)", phone, code, expires_at)
    # Return mock_otp so the user sees it during dev. In production this would be removed.
    return {"success": True, "phone": phone, "mock_otp": code, "mocked": True, "ttl": OTP_TTL_SECONDS}


@api_router.post("/auth/verify-otp")
async def verify_otp(payload: OtpVerify):
    phone = normalize_phone(payload.phone)
    record = await db.otps.find_one({"phone": phone})
    if not record:
        raise HTTPException(status_code=400, detail="No OTP requested for this number")
    expires_at = datetime.fromisoformat(record["expires_at"])
    if datetime.now(timezone.utc) > expires_at:
        raise HTTPException(status_code=400, detail="OTP expired, please request again")
    if record.get("attempts", 0) >= 5:
        raise HTTPException(status_code=429, detail="Too many attempts, please request a new OTP")
    if payload.code.strip() != record["code"]:
        await db.otps.update_one({"phone": phone}, {"$inc": {"attempts": 1}})
        raise HTTPException(status_code=400, detail="Incorrect OTP")

    # OTP valid — find or create user
    existing = await db.users.find_one({"phone": phone})
    if existing:
        user_doc = existing
        if payload.name and not user_doc.get("name"):
            await db.users.update_one({"id": user_doc["id"]}, {"$set": {"name": payload.name}})
            user_doc["name"] = payload.name
    else:
        user = User(phone=phone, name=payload.name or "")
        user_doc = user.dict()
        await db.users.insert_one(user_doc)

    # Issue session token
    token = make_token()
    await db.sessions.insert_one({
        "token": token,
        "user_id": user_doc["id"],
        "created_at": now_iso(),
    })
    # Consume OTP
    await db.otps.delete_one({"phone": phone})

    user_doc.pop("_id", None)
    return {"token": token, "user": user_doc}


async def get_current_user(authorization: Optional[str] = None) -> Optional[dict]:
    # FastAPI will inject from Header() below in the dependency wrapper
    return None  # placeholder, replaced via dependency


from fastapi import Header


async def require_user(authorization: Optional[str] = Header(default=None)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.split(" ", 1)[1].strip()
    session = await db.sessions.find_one({"token": token})
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    user = await db.users.find_one({"id": session["user_id"]})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    user.pop("_id", None)
    return user


@api_router.get("/auth/me")
async def me(user: dict = Depends(require_user)):
    return {"user": user}


@api_router.post("/auth/logout")
async def logout(authorization: Optional[str] = Header(default=None)):
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        await db.sessions.delete_one({"token": token})
    return {"ok": True}


@api_router.patch("/auth/profile")
async def update_profile(body: dict, user: dict = Depends(require_user)):
    update = {}
    for k in ("name", "email"):
        if k in body and body[k] is not None:
            update[k] = body[k]
    if update:
        await db.users.update_one({"id": user["id"]}, {"$set": update})
    fresh = await db.users.find_one({"id": user["id"]})
    fresh.pop("_id", None)
    return {"user": fresh}


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

# =========================
# Logging
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)

app.include_router(api_router)

# =========================
# Shutdown Event
# =========================
@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()

# =========================
# Vercel ASGI Handler
# =========================
