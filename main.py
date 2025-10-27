from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import stripe, paypalrestsdk, firebase_admin
from firebase_admin import credentials, auth

app = FastAPI(title="Kairah Studio Backend", description="Handles auth, payments, media generation, and affiliates.")

# Initialize Firebase
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)

# Stripe setup
stripe.api_key = "your_stripe_secret_key"

# PayPal setup
paypalrestsdk.configure({
  "mode": "live",
  "client_id": "your_paypal_client_id",
  "client_secret": "your_paypal_secret"
})

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    return {"message": "Welcome to Kairah Studio Backend"}

@app.post("/generate")
def generate_media(request: Request):
    """Mock generation for video/image/audio"""
    # Validate plan + limit
    return {"status": "success", "output_url": "https://kairah.vercel.app/sample.mp4"}

@app.post("/subscribe/paypal")
def paypal_subscribe(request: Request):
    """Handle PayPal subscription webhook"""
    return {"status": "received"}

@app.post("/subscribe/stripe")
def stripe_subscribe(request: Request):
    """Handle Stripe subscription webhook"""
    return {"status": "received"}

@app.post("/affiliate/track")
def affiliate_track(request: Request):
    """Track referral and commission"""
    return {"commission": "30%", "payment_split": "70/30"}

@app.post("/support")
def support_message(request: Request):
    """Forward message to keishapoa@gmail.com"""
    return {"status": "sent"}

