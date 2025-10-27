from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Update with your frontend URL later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    return {"message": "Kairah Studio Backend is live ✨"}

@app.get("/plans")
def get_plans():
    return {
        "plans": [
            {"name": "Free", "price": 0, "features": ["6s clip", "watermark", "Preview tools"]},
            {"name": "Pro", "price": 19, "features": ["1min videos", "1080p export", "Merge 3 clips"]},
            {"name": "Diamond", "price": 49, "features": ["4K export", "10 videos/mo", "No watermark"]},
            {"name": "Cinematic", "price": 99, "features": ["Full studio features", "Unlimited renders"]},
            {"name": "Lifetime", "price": 500, "features": ["Lifetime access", "All future updates"]},
        ]
    }
