# main.py
"""
Kairah Studio single-file backend (FastAPI)
- Stripe Checkout session creation + webhook handling
- PayPal order creation + webhook placeholder
- Firebase ID token verification (optional)
- Affiliate ledger (30% commission) in Firestore
- Mocked /generate endpoints (video/image/audio)
- Admin payout runner (protected by ADMIN_TOKEN header)
"""

import os
import json
import stripe
import requests
from typing import Optional, Dict, Any
from fastapi import FastAPI, Request, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime, timezone
import firebase_admin
from firebase_admin import credentials, auth, firestore

# Load env variables (Render sets them)
STRIPE_SECRET = os.getenv("STRIPE_SECRET", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
# STRIPE_PRICE_MAP should be a JSON string mapping keys to price IDs, e.g. {"pro_month":"price_abc", ...}
STRIPE_PRICE_MAP = json.loads(os.getenv("STRIPE_PRICE_MAP", "{}") or "{}")

PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")
PAYPAL_MODE = os.getenv("PAYPAL_MODE", "sandbox")  # sandbox or live

FIREBASE_CREDENTIALS_JSON = os.getenv("FIREBASE_CREDENTIALS_JSON", "")  # path or JSON string (optional)
FRONTEND_BASE = os.getenv("FRONTEND_BASE", "https://yourfrontend.example")
AFFILIATE_COMMISSION = float(os.getenv("AFFILIATE_COMMISSION", "0.30"))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "change-this-to-a-strong-token")

# Init Stripe (if provided)
if STRIPE_SECRET:
    stripe.api_key = STRIPE_SECRET

# Init Firebase admin if credentials provided
firebase_initialized = False
if FIREBASE_CREDENTIALS_JSON:
    try:
        if FIREBASE_CREDENTIALS_JSON.strip().startswith("{"):
            cred = credentials.Certificate(json.loads(FIREBASE_CREDENTIALS_JSON))
        else:
            cred = credentials.Certificate(FIREBASE_CREDENTIALS_JSON)
        firebase_admin.initialize_app(cred)
        firebase_initialized = True
    except Exception as e:
        print("Firebase init error (continuing without Firebase):", e)
else:
    # Try default app (works on GCP)
    try:
        firebase_admin.initialize_app()
        firebase_initialized = True
    except Exception:
        firebase_initialized = False

db = None
if firebase_initialized:
    db = firestore.client()

app = FastAPI(title="Kairah Studio Backend (single-file)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # lock down to your frontend domain in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------
# Models
# --------------------
class StripeSessionRequest(BaseModel):
    uid: Optional[str] = None
    price_key: str
    affiliate_id: Optional[str] = None
    mode: Optional[str] = "subscription"  # "subscription" or "payment"

class PayPalOrderRequest(BaseModel):
    uid: Optional[str] = None
    plan_key: str
    amount: float
    affiliate_id: Optional[str] = None

class GenerateRequest(BaseModel):
    uid: str
    prompt: str
    style: Optional[Dict[str, Any]] = None

# --------------------
# Utilities
# --------------------
def save_payment_record(record: Dict[str, Any]):
    rec = dict(record)
    rec["created_at"] = datetime.now(timezone.utc).isoformat()
    if db:
        db.collection("payments").add(rec)
    else:
        print("PAYMENT RECORD:", rec)

def ensure_user_doc(uid: str, email: str = ""):
    if not db: 
        return
    uref = db.collection("users").document(uid)
    if not uref.get().exists:
        uref.set({
            "email": email or "",
            "plan": "Free",
            "affiliate_id": None,
            "usage": {"videos": 0, "images": 0, "audio": 0},
            "created_at": firestore.SERVER_TIMESTAMP
        }, merge=True)

def verify_firebase_token(id_token: str):
    if not firebase_initialized:
        raise HTTPException(500, "Firebase not configured on server")
    try:
        decoded = auth.verify_id_token(id_token)
        return decoded
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid auth token: {str(e)}")

def get_paypal_access_token():
    if not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET:
        raise HTTPException(500, "PayPal not configured")
    token_url = "https://api-m.sandbox.paypal.com/v1/oauth2/token" if PAYPAL_MODE == "sandbox" else "https://api-m.paypal.com/v1/oauth2/token"
    resp = requests.post(token_url, data={"grant_type":"client_credentials"}, auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET))
    resp.raise_for_status()
    return resp.json()["access_token"]

# Dependency to read Firebase header (Authorization: Bearer <idToken>)
def get_current_user(authorization: Optional[str] = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid auth header format")
    id_token = parts[1]
    return verify_firebase_token(id_token)

# --------------------
# Routes
# --------------------
@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}

# Create Stripe Checkout Session
@app.post("/create-stripe-session")
def create_stripe_session(req: StripeSessionRequest):
    if not STRIPE_SECRET:
        raise HTTPException(500, "Stripe not configured")
    price_id = STRIPE_PRICE_MAP.get(req.price_key) or req.price_key
    if not price_id:
        raise HTTPException(400, "Invalid price_key")
    metadata = {"affiliate_id": req.affiliate_id or "", "uid": req.uid or ""}
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription" if req.mode == "subscription" else "payment",
            metadata=metadata,
            success_url=f"{FRONTEND_BASE}/payments/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{FRONTEND_BASE}/payments/cancel",
        )
        return {"id": session.id, "url": session.url}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# Stripe webhook handler
@app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(500, "Stripe webhook secret not configured")
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Signature verification failed: {str(e)}")
    typ = event["type"]
    data = event["data"]["object"]
    # Handle checkout.session.completed
    if typ == "checkout.session.completed":
        session = data
        metadata = session.get("metadata") or {}
        uid = metadata.get("uid")
        affiliate = metadata.get("affiliate_id")
        amount = None
        try:
            amount = session.get("amount_total") / 100.0 if session.get("amount_total") else None
        except:
            amount = None
        save_payment_record({"provider":"stripe","event": typ, "session": session.get("id"), "amount": amount, "uid": uid, "affiliate":affiliate})
        if uid and db:
            db.collection("users").document(uid).set({"plan":"Paid"}, merge=True)
            ensure_user_doc(uid)
        if affiliate and amount and db:
            aff_ref = db.collection("affiliates").document(affiliate)
            aff_ref.set({"affiliate_id": affiliate}, merge=True)
            aff_ref.collection("earnings").add({
                "amount": amount * AFFILIATE_COMMISSION,
                "gross": amount,
                "source":"stripe",
                "session_id": session.get("id"),
                "created_at": firestore.SERVER_TIMESTAMP,
                "paid": False
            })
    # invoice.payment_succeeded -> track recurring payments
    if typ == "invoice.payment_succeeded":
        invoice = data
        save_payment_record({"provider":"stripe","event": typ, "invoice_id": invoice.get("id"), "amount_paid": invoice.get("amount_paid")/100.0 if invoice.get("amount_paid") else None})
    return {"status":"received"}

# Create PayPal order
@app.post("/create-paypal-order")
def create_paypal_order(req: PayPalOrderRequest):
    token = get_paypal_access_token()
    url = "https://api-m.sandbox.paypal.com/v2/checkout/orders" if PAYPAL_MODE == "sandbox" else "https://api-m.paypal.com/v2/checkout/orders"
    body = {
        "intent":"CAPTURE",
        "purchase_units":[
            {
                "amount":{"currency_code":"USD","value": f"{req.amount:.2f}"},
                "custom_id": req.plan_key,
                "description": f"{req.plan_key} purchase"
            }
        ],
        "application_context":{
            "return_url": f"{FRONTEND_BASE}/paypal/success",
            "cancel_url": f"{FRONTEND_BASE}/paypal/cancel"
        }
    }
    resp = requests.post(url, json=body, headers={"Content-Type":"application/json","Authorization":f"Bearer {token}"})
    resp.raise_for_status()
    return resp.json()

# PayPal webhook placeholder
@app.post("/webhook/paypal")
async def paypal_webhook(request: Request):
    payload = await request.json()
    # In production: validate webhook signature
    save_payment_record({"provider":"paypal", "event": payload.get("event_type"), "resource": payload.get("resource")})
    return {"status":"ok"}

# --------------------
# Mocked generate endpoints (replace with real AI adapters)
# --------------------
@app.post("/generate/video")
def generate_video(req: GenerateRequest, token=Depends(get_current_user)):
    uid = req.uid
    ensure_user_doc(uid, token.get("email", ""))
    # enforce free plan rule if needed
    udoc = db.collection("users").document(uid).get().to_dict() if db else {}
    plan = udoc.get("plan","Free") if udoc else "Free"
    if plan == "Free":
        if udoc and udoc.get("usage",{}).get("videos",0) >= 1:
            raise HTTPException(403, "Free plan limit reached. Upgrade for more.")
        url = "https://samplelib.com/lib/preview/mp4/sample-5s.mp4"
    else:
        url = "https://samplelib.com/lib/preview/mp4/sample-30s.mp4"
    if db:
        db.collection("users").document(uid).update({"usage.videos": firestore.Increment(1)})
        db.collection("jobs").add({
            "uid": uid, "type": "video", "prompt": req.prompt, "style": req.style or {}, "result_url": url, "status": "ready", "created_at": firestore.SERVER_TIMESTAMP
        })
    return {"url": url, "status":"ready"}

@app.post("/generate/image")
def generate_image(req: GenerateRequest, token=Depends(get_current_user)):
    uid = req.uid
    ensure_user_doc(uid, token.get("email",""))
    url = "https://via.placeholder.com/1024x576.png?text=AI+Image+Preview"
    if db:
        db.collection("users").document(uid).update({"usage.images": firestore.Increment(1)})
        db.collection("jobs").add({"uid":uid,"type":"image","prompt":req.prompt,"result_url":url,"status":"ready","created_at":firestore.SERVER_TIMESTAMP})
    return {"url":url,"status":"ready"}

@app.post("/generate/audio")
def generate_audio(req: GenerateRequest, token=Depends(get_current_user)):
    uid = req.uid
    ensure_user_doc(uid, token.get("email",""))
    url = "https://samplelib.com/lib/preview/mp3/sample-3s.mp3"
    if db:
        db.collection("users").document(uid).update({"usage.audio": firestore.Increment(1)})
        db.collection("jobs").add({"uid":uid,"type":"audio","prompt":req.prompt,"result_url":url,"status":"ready","created_at":firestore.SERVER_TIMESTAMP})
    return {"url":url,"status":"ready"}

# --------------------
# Admin: Run affiliate payouts (mark earnings paid & record payout)
# --------------------
@app.post("/admin/affiliate/payouts")
def run_affiliate_payouts(admin_token: Optional[str] = Header(None)):
    if admin_token != ADMIN_TOKEN:
        raise HTTPException(401, "Unauthorized")
    results = []
    if not db:
        return {"warning": "Firestore not configured", "payouts": results}
    affs = db.collection("affiliates").stream()
    for a in affs:
        aid = a.id
        earnings_q = db.collection("affiliates").document(aid).collection("earnings").where("paid","==", False).stream()
        total = 0.0
        ids = []
        for e in earnings_q:
            ed = e.to_dict()
            amt = float(ed.get("amount",0) or 0)
            total += amt
            ids.append(e.id)
        if total >= 500.0:
            db.collection("affiliate_payouts").add({"affiliate_id":aid,"amount":total,"created_at":firestore.SERVER_TIMESTAMP,"status":"pending"})
            for eid in ids:
                db.collection("affiliates").document(aid).collection("earnings").document(eid).update({"paid":True})
            results.append({"affiliate":aid,"amount":total})
    return {"payouts":results}

# --------------------
# Additional helper: list price map
# --------------------
@app.get("/prices")
def list_prices():
    return {"prices": STRIPE_PRICE_MAP}
