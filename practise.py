from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import requests
from google_auth_oauthlib.flow import Flow
from google.oauth2 import id_token 
from google.auth.transport import requests as google_requests
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import base64
from email import message_from_bytes
from email.message import Message
import logging
from typing import Optional, Iterator, Any, Dict


load_dotenv(".env")


CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI") 

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
    'openid',
    'email',
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/userinfo.email',
    'https://www.googleapis.com/auth/userinfo.profile',
]


STATE_STORE = {} 
TOKEN_STORE = {} 
print("Current TOKEN_STORE keys:", list(TOKEN_STORE.keys()))
logger = logging.getLogger(__name__)
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_credentials(user_id: str) -> Credentials:
    """Retrieves and builds Google Credentials from the global TOKEN_STORE."""
    user_data = TOKEN_STORE.get(user_id)
    if not user_data:
        raise ValueError("No valid credentials found.")

    creds = Credentials(
        token=user_data.get('access_token'),
        refresh_token=user_data.get('refresh_token'),
        token_uri='https://oauth2.googleapis.com/token',
        client_id=os.getenv("CLIENT_ID"),
        client_secret=os.getenv("CLIENT_SECRET"),
        scopes=user_data.get('scopes')
    )
    return creds

def parse_time(send_time_str: str) -> datetime:
    """Placeholder for robust time parsing utility."""
    # This would use dateutil.parser in a production environment
    return datetime.now().astimezone()

def extract_message_part(payload: Dict[str, Any]) -> str:
    """Robustly extracts the plain text body from the Gmail API payload."""
    # Simplified logic: attempts to find the first text/plain part
    if 'parts' in payload:
        for part in payload['parts']:
            if part['mimeType'] == 'text/plain' and 'data' in part['body']:
                data = part['body']['data']
                return base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
    elif 'body' in payload and 'data' in payload['body']:
        data = payload['body']['data']
        return base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
    return "..."

def fetch_and_filter_emails(
    email_address: str,
    user_id: str,
    minutes_since: int = 60,
    include_read: bool = False,
    skip_filters: bool = False,
) -> Iterator[Dict[str, Any]]:
    """Core logic to fetch, filter, and process primary inbox emails."""
    
    # 1. Get credentials and service
    creds = get_credentials(user_id)
    service = build("gmail", "v1", credentials=creds)
    
    # 2. Construct PRIMARY INBOX query
    after = int((datetime.now() - timedelta(minutes=minutes_since)).timestamp())
    query = f"(to:{email_address} OR from:{email_address}) after:{after} in:inbox"
    query += " -category:promotions -category:social -category:updates -category:forums"
    if not include_read:
        query += " is:unread"
    
    # 3. Retrieve all matching messages
    messages = []
    next_page_token = None
    while True:
        results = service.users().messages().list(userId="me", q=query, pageToken=next_page_token).execute()
        if "messages" in results: messages.extend(results["messages"])
        next_page_token = results.get("nextPageToken")
        if not next_page_token: break
    
    # 4. Process and filter
    for message in messages:
        try:
            msg = service.users().messages().get(userId="me", id=message["id"]).execute()
            thread = service.users().threads().get(userId="me", id=msg["threadId"]).execute()
            messages_in_thread = thread["messages"]
            
            # Sort messages chronologically
            messages_in_thread.sort(key=lambda m: int(m.get("internalDate", 0)) if "internalDate" in m else m["id"])
            last_message = messages_in_thread[-1]
            
            # Check user response (Agent filtering logic)
            last_from = next((h["value"] for h in last_message["payload"]["headers"] if h["name"] == "From"), "")
            if not skip_filters and email_address in last_from:
                yield {"id": message["id"], "thread_id": msg["threadId"], "user_respond": True}
                continue
            
            # Final processing check
            from_header = next((h["value"] for h in msg["payload"]["headers"] if h["name"] == "From"), "")
            is_from_user = email_address in from_header
            is_latest_in_thread = message["id"] == last_message["id"]
            should_process = skip_filters or (not is_from_user and is_latest_in_thread)
            
            if not should_process: continue
            
            # 5. Extract and Yield Data
            process_headers = msg["payload"].get("headers", [])
            subject = next((h["value"] for h in process_headers if h["name"] == "Subject"), "No Subject")
            from_email = next((h["value"] for h in process_headers if h["name"] == "From"), "")
            to_email = next((h["value"] for h in process_headers if h["name"] == "To"), "")
            send_time = next((h["value"] for h in process_headers if h["name"] == "Date"), str(datetime.now()))
            
            yield {
                "from_email": from_email.strip(),
                "to_email": to_email.strip(),
                "subject": subject,
                "page_content": extract_message_part(msg["payload"]), # Clean body
                "id": message["id"],
                "thread_id": message["threadId"],
                "send_time": parse_time(send_time).isoformat(),
                "user_respond": False,
            }
        except Exception as e:
            logger.warning(f"Failed to process message {message.get('id', 'N/A')}: {str(e)}")
    



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
        #prompt = 'consent' 
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

    return RedirectResponse(url=f"{FRONTEND_URL}/dashboard/{user_id}")


@app.get("/mails/agent-data/{user_id}")
def fetch_primary_agent_mails(user_id: str):
    """
    API endpoint that fetches processed and filtered primary inbox mail data for the Agent/Frontend Display.
    """
    try:
        # 1. Get user email (from TOKEN_STORE, assuming OAuth already ran)
        user_data = TOKEN_STORE.get(user_id)
        if not user_data or not user_data.get("user_email"):
            raise HTTPException(status_code=401, detail="User email or credentials missing.")
        
        user_email_address = user_data["user_email"]
        
        # 2. Call the core logic and consume the generator
        processed_emails = list(fetch_and_filter_emails(
            email_address=user_email_address,
            user_id=user_id,
            # Uses default filters: 60 minutes, unread, no skip filters
        ))
        
        return {
            "status": "success",
            "user_id": user_id,
            "count": len(processed_emails),
            "emails": processed_emails
        }
        
    except ValueError as e:
        # Handles credential/token errors
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        logger.error(f"Primary mail fetch failed for {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve and process mail data.")

@app.get("/")
def home():
    return {"message": "Google OAuth Backend running"}

#8:30
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import requests
from google_auth_oauthlib.flow import Flow
from google.oauth2 import id_token 
from google.auth.transport import requests as google_requests
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
import base64
from googleapiclient.discovery import build
import logging
from typing import Optional, Iterator, Any, Dict

logger = logging.getLogger(__name__)
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
    'openid',
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
    print("--- DEBUG: TOKEN_STORE WRITE CHECK (CALLBACK) ---")
    print(f"User ID: {user_id}")
    print(f"Stored Data: {TOKEN_STORE.get(user_id)}")
    print("--------------------------------------------------")

    return RedirectResponse(url=f"{FRONTEND_URL}/dashboard/{user_id}")


def extract_message_part(payload: Dict[str, Any]) -> str:
    """
    Recursively extract text content from email payload.
    Handles both simple and multipart MIME messages.
    """
    if payload.get("body", {}).get("data"):
        data = payload["body"]["data"]
        decoded = base64.urlsafe_b64decode(data).decode("utf-8")
        return decoded
        
    if payload.get("parts"):
        text_parts = []
        for part in payload["parts"]:
            content = extract_message_part(part)
            if content:
                text_parts.append(content)
        return "\n".join(text_parts)
        
    return ""


