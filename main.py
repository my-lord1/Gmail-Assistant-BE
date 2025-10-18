from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import requests
from google_auth_oauthlib.flow import Flow
from google.oauth2 import id_token 
from google.auth.transport import requests as google_requests
import os
from dotenv import load_dotenv
from datetime import datetime
load_dotenv(".env")


CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI") 
FRONTEND_MAIL_PAGE = os.getenv("FRONTEND_MAIL_PAGE") 
FRONTEND_URL = os.getenv("FRONTEND_URL") 

CLIENT_CONFIG = {
    "web": {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token", 
        "redirect_uris": [REDIRECT_URI]
    }
}

SCOPES = [
    'openid email',
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/userinfo.email',
    'https://www.googleapis.com/auth/userinfo.profile',
]


STATE_STORE = {} 
TOKEN_STORE = {} 

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/auth/google/start")
def start_auth_flow(request: Request):
    flow = Flow.from_client_config(
        CLIENT_CONFIG,
        scopes = SCOPES, 
        redirect_uri = REDIRECT_URI
    )

    authorization_url, state = flow.authorization_url(
        access_type = 'offline', 
        include_granted_scopes = 'true',
        prompt = 'consent' 
    )

    STATE_STORE[state] = {
        "created_at": datetime.now(),
    }

    return {"authorization_url": authorization_url}

@app.get("/auth/google/callback")
async def auth_callback(code: str, state: str):
    if state not in STATE_STORE:
        raise HTTPException(status_code=400, detail="Invalid or expired state parameter (CSRF attempt)")
    
    del STATE_STORE[state]
    
    flow = Flow.from_client_config(
        CLIENT_CONFIG,
        scopes = SCOPES,
        redirect_uri = REDIRECT_URI
    )

    try:
        flow.fetch_token(code = code)
    except Exception as e:
        print(f"Token exchange failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to exchange authorization code for tokens.")
    
    credentials = flow.credentials
    
    user_id = None
    if credentials.id_token:
            idinfo = id_token.verify_oauth2_token(
                credentials.id_token,
                google_requests.Request(),
                CLIENT_ID
            )
            user_id = idinfo.get("sub") # Google's unique user ID
            user_email = idinfo.get("email")

    TOKEN_STORE[user_id] = {
        "user_email": user_email,
        "refresh_token": credentials.refresh_token,
        "access_token": credentials.token, 
        "scopes": list(credentials.scopes),
        "expiry": credentials.expiry.isoformat() if credentials.expiry else None,
        "created_at": datetime.now().isoformat()
    }

    return RedirectResponse(url=f"{FRONTEND_MAIL_PAGE}")


@app.get("/")
def home():
    return {"message": "Google OAuth Backend running"}