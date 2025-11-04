from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from datetime import datetime
from routers.settings import CLIENT_CONFIG, SCOPES, REDIRECT_URI, CLIENT_ID, FRONTEND_URL
from routers.stores import save_state_key, get_state_key, delete_state_key, save_token


router = APIRouter(prefix="/auth/google", tags=["Authentication"])

@router.get("/start")
def start_auth_flow(request: Request):
    flow = Flow.from_client_config(
        CLIENT_CONFIG,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )

    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )

    save_state_key(state)
    return {"authorization_url": authorization_url}

@router.get("/callback")
async def auth_callback(code: str, state: str):
    
    if not get_state_key(state):
        raise HTTPException(status_code=400, detail="Invalid or expired state parameter")
    delete_state_key(state)
    
    flow = Flow.from_client_config(
        CLIENT_CONFIG,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )

    try:
        flow.fetch_token(code=code)
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
        user_id = idinfo.get("sub")  # Google's unique user ID
        user_email = idinfo.get("email")

    save_token(
        user_id=user_id,
        user_email=user_email,
        access_token=credentials.token,
        refresh_token=credentials.refresh_token,
        scopes=list(credentials.scopes),
        expiry=credentials.expiry.isoformat() if credentials.expiry else None
    )
    
    return RedirectResponse(url=f"{FRONTEND_URL}/dashboard/{user_id}")