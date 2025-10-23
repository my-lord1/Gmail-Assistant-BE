from fastapi import FastAPI, Request, HTTPException, Body
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import requests
from google_auth_oauthlib.flow import Flow
from google.oauth2 import id_token 
from google.auth.transport import requests as google_requests
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
from google.oauth2.credentials import Credentials
import base64
from googleapiclient.discovery import build
import logging
from typing import Optional, Iterator, Any, Dict
from google.auth.transport.requests import Request as GoogleAuthRequest
from email.utils import parsedate_to_datetime
from pydantic import BaseModel


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
class GmailRequest(BaseModel):
    user_id: str
    to_email: str
    subject: str
    body_text: str
    thread_id: Optional[str] = None
    reply_to_message_id: Optional[str] = None

    
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

#try to fetch the profile photos too
@app.get("/emails/full-threaded/{user_id}")
async def fetch_primary_inbox_emails_threaded(
    user_id: str,
    max_threads: int = 20,
    max_messages_per_thread: int = 10,
    include_read: bool = True
):
    if user_id not in TOKEN_STORE:
        raise HTTPException(status_code=404, detail="No tokens found for this user_id")

    tok = TOKEN_STORE[user_id]
    creds = Credentials(
        token=tok.get("access_token"),
        refresh_token=tok.get("refresh_token"),
        token_uri=CLIENT_CONFIG["web"]["token_uri"],
        client_id=CLIENT_CONFIG["web"]["client_id"],
        client_secret=CLIENT_CONFIG["web"]["client_secret"],
        scopes=SCOPES,
    )

    if not creds.valid:
        creds.refresh(GoogleAuthRequest())
        TOKEN_STORE[user_id]["access_token"] = creds.token
        TOKEN_STORE[user_id]["expiry"] = creds.expiry.isoformat() if creds.expiry else None

    service = build("gmail", "v1", credentials=creds)
    profile_info = service.users().getProfile(userId="me").execute()
    gmail_id = profile_info.get("emailAddress")

    try:
        oauth_service = build("oauth2", "v2", credentials=creds)
        user_info = oauth_service.userinfo().get().execute()
        user_name = user_info.get("given_name", "")
        profile_photo = user_info.get("picture", "")
    except Exception as e:
        print(f"Error getting profile photo: {e}")
        user_name = ""
        profile_photo = ""

    q = "in:inbox category:primary"
    if not include_read:
        q += " is:unread"

    threads_resp = service.users().threads().list(
        userId="me",
        q=q,
        maxResults=max_threads
    ).execute()

    threads = threads_resp.get("threads", [])
    all_threads = []

    def decode_part(part):
        body_data = part.get("body", {}).get("data")
        if not body_data:
            return ""
        return base64.urlsafe_b64decode(body_data.encode("utf-8")).decode("utf-8", errors="ignore")

    def extract_mime_parts(payload):
        body_text, body_html = "", ""
        mime_type = payload.get("mimeType", "")
        if "multipart" in mime_type:
            for part in payload.get("parts", []):
                t, h = extract_mime_parts(part)
                body_text += t
                body_html += h
        else:
            if mime_type == "text/plain":
                body_text += decode_part(payload)
            elif mime_type == "text/html":
                body_html += decode_part(payload)
        return body_text, body_html

    for thread in threads:
        thread_id = thread.get("id")
        thread_data = service.users().threads().get(userId="me", id=thread_id, format="full").execute()

        thread_msgs = []
        for msg in thread_data.get("messages", [])[:max_messages_per_thread]:
            payload = msg.get("payload", {})
            headers = payload.get("headers", [])

            def h(name):
                return next((h["value"] for h in headers if h["name"].lower() == name.lower()), None)

            body_text, body_html = extract_mime_parts(payload)

            label_ids = msg.get("labelIds", [])
            is_unread = "UNREAD" in label_ids

            raw_date = h("Date")
            try:
                parsed_date = parsedate_to_datetime(raw_date)
                ist = timezone(timedelta(hours=5, minutes=30))
                parsed_date = parsed_date.astimezone(ist)
                now = datetime.now(ist)
                if parsed_date.date() == now.date():
                    sent_time = parsed_date.strftime("Today, %I:%M %p")
                elif parsed_date.date() == (now - timedelta(days=1)).date():
                    sent_time = parsed_date.strftime("Yesterday, %I:%M %p")
                else:
                    sent_time = parsed_date.strftime("%d %b, %I:%M %p")
            except Exception:
                sent_time = raw_date or "Unknown"

            msg_obj = {
                "id": msg.get("id"),
                "snippet": msg.get("snippet"),
                "from": h("From"),
                "to": h("To"),
                "subject": h("Subject"),
                "date": raw_date,
                "sent_time": sent_time,       
                "is_unread": is_unread,        
                "body_text": body_text.strip(),
                "body_html": body_html.strip(),
            }
            thread_msgs.append(msg_obj)

        # Sort messages by date ascending
        def parse_date_safe(d):
            try:
                return parsedate_to_datetime(d)
            except Exception:
                return datetime.min

        thread_msgs.sort(key=lambda m: parse_date_safe(m.get("date")))

        all_threads.append({
            "threadId": thread_id,
            "message_count": len(thread_msgs),
            "subject": thread_msgs[0]["subject"] if thread_msgs else None,
            "participants": list({m["from"] for m in thread_msgs if m.get("from")}),
            "messages": thread_msgs
        })

    return JSONResponse(content={
        "thread_count": len(all_threads), 
        "threads": all_threads,
        "user_info": {
        "gmail_id": gmail_id,
        "profile_photo": profile_photo,
        "user_name": user_name
    }})

@app.post("/emails/send")
async def send_email( request: GmailRequest):

    if request.user_id not in TOKEN_STORE:
        raise HTTPException(status_code=404, detail="No tokens found for this user_id")
    
    tok = TOKEN_STORE[request.user_id]
    creds = Credentials(
        token=tok.get("access_token"),
        refresh_token=tok.get("refresh_token"),
        token_uri=CLIENT_CONFIG["web"]["token_uri"],
        client_id=CLIENT_CONFIG["web"]["client_id"],
        client_secret=CLIENT_CONFIG["web"]["client_secret"],
        scopes=SCOPES,
    )

    if not creds.valid:
        creds.refresh(GoogleAuthRequest())
        TOKEN_STORE[request.user_id]["access_token"] = creds.token
        TOKEN_STORE[request.user_id]["expiry"] = creds.expiry.isoformat() if creds.expiry else None

    service = build("gmail", "v1", credentials=creds)

    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    message = MIMEMultipart("alternative")
    message["to"] = request.to_email
    message["from"] = tok["user_email"]
    message["subject"] = request.subject

    if request.thread_id and request.reply_to_message_id:
        message["In-Reply-To"] = request.reply_to_message_id
        message["References"] = request.reply_to_message_id

    message.attach(MIMEText(request.body_text, "plain"))
    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    body = {"raw": raw_message}

    if request.thread_id and request.thread_id.strip():
        body["threadId"] = request.thread_id

    try:
        sent_msg = service.users().messages().send(userId="me", body=body).execute()
        return {
            "status": "success",
            "message_id": sent_msg.get("id"),
            "thread_id": sent_msg.get("threadId")
        }
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to send email: {e}")

