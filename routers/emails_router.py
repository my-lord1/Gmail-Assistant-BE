from fastapi import APIRouter
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleAuthRequest
from googleapiclient.discovery import build
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import base64
import logging
from typing import Optional
from pydantic import BaseModel
from routers.settings import CLIENT_CONFIG, SCOPES
from routers.stores import save_token, get_token, update_access_token, delete_token
from db.mongodb import email_threads
import threading
from bs4 import BeautifulSoup
import re
from db.mongodb import user_profiles


router = APIRouter(prefix="/emails", tags=["Emails"])

class GmailRequest(BaseModel):
    user_id: str
    body_text: str
    to_email: Optional[str] = None
    subject: Optional[str] = None
    thread_id: Optional[str] = None
    reply_to_message_id: Optional[str] = None

token_lock = threading.Lock()

def fetch_primary_inbox_emails_threaded_sync(
    user_id: str,
    max_threads: int = 30, #later change this 20
    max_messages_per_thread: int = 10,
    include_read: bool = True
):
    
    tok = get_token(user_id)

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
        update_access_token(
            user_id,
            creds.token,
            creds.expiry.isoformat() if creds.expiry else None
        )

    service = build("gmail", "v1", credentials=creds)
    profile_info = service.users().getProfile(userId="me").execute()
    gmail_id = profile_info.get("emailAddress")

    try:
        oauth_service = build("oauth2", "v2", credentials=creds)
        user_info = oauth_service.userinfo().get().execute()
        user_name = user_info.get("given_name", "")
        profile_photo = user_info.get("picture", "")
    except Exception as e:
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

    def parse_email_html(html_content: str) -> str:
        if not html_content:
            return ""
        
        soup = BeautifulSoup(html_content, 'html.parser')
        
        for element in soup(["script", "style", "head"]):
            element.decompose()
        
        text = soup.get_text(separator='\n')
        
        lines = []
        for line in text.splitlines():
            cleaned = line.strip()
            if cleaned and cleaned != '&nbsp;':
                lines.append(cleaned)
        
        clean_text = '\n'.join(lines)
        clean_text = re.sub(r'\n{3,}', '\n\n', clean_text)
        
        return clean_text.strip()

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
            
            #make the room for boooooody_clean
            body_clean = parse_email_html(body_html)


            label_ids = msg.get("labelIds", [])
            is_unread = "UNREAD" in label_ids

            raw_date = h("Date")

            try:
                parsed_date = parsedate_to_datetime(raw_date)

                if parsed_date.tzinfo is None:
                    parsed_date = parsed_date.replace(tzinfo=timezone.utc)

                # Convert to IST
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
                "body_clean": body_clean
            }
            thread_msgs.append(msg_obj)

        def parse_date_safe(d):
            try:
                return parsedate_to_datetime(d)
            except Exception:
                return datetime.min

        thread_msgs.sort(key=lambda m: parse_date_safe(m.get("date")))

        all_threads.append({
            "userId": user_id,
            "threadId": thread_id,
            "message_count": len(thread_msgs),
            "subject": thread_msgs[0]["subject"] if thread_msgs else None,
            "participants": list({m["from"] for m in thread_msgs if m.get("from")}),
            "messages": thread_msgs
        })

    return {
        "thread_count": len(all_threads),
        "threads": all_threads,
        "user_info": {
            "user_id": user_id,
            "gmail_id": gmail_id,
            "profile_photo": profile_photo,
            "user_name": user_name
        }
    }


@router.get("/full-threaded/{user_id}")
async def get_full_threaded_emails(user_id: str):
    threads_docs = list(
        email_threads.find({"user_id": user_id})
    )

    if not threads_docs:
        return {
            "thread_count": 0,
            "threads": [],
            "user_info": None
        }

    def get_last_msg_date(thread_doc):
        messages = thread_doc.get("messages", [])
        if not messages:
            return datetime.min

        last_msg = messages[-1] 
        raw_date = last_msg.get("date")

        try:
            return parsedate_to_datetime(raw_date)
        except:
            return datetime.min

    threads_docs.sort(key=get_last_msg_date, reverse=True)

    transformed_threads = [
        {
            "threadId": t.get("thread_id"),
            "message_count": t.get("message_count", 0),
            "subject": t.get("subject", ""),
            "participants": t.get("participants", []),
            "messages": t.get("messages", [])
        }
        for t in threads_docs
    ]

    user_profile = user_profiles.find_one({"user_id": user_id})
    user_info = {
        "gmail_id": user_profile.get("gmail_id"),
        "profile_photo": user_profile.get("profile_photo"),
        "user_name": user_profile.get("user_name")
    } if user_profile else None

    return {
        "thread_count": len(transformed_threads),
        "threads": transformed_threads,
        "user_info": user_info
    }


def send_email_function(
    user_id: str,
    body_text: str,
    to_email: Optional[str] = None,
    subject: Optional[str] = None,
    thread_id: Optional[str] = None,
    reply_to_message_id: Optional[str] = None
):

    tok = get_token(user_id)
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
        update_access_token(
            user_id,
            creds.token,
            creds.expiry.isoformat() if creds.expiry else None
        )

    service = build("gmail", "v1", credentials=creds)
    is_reply = bool(reply_to_message_id and thread_id)
    if is_reply and not subject:
        try:
            original_msg = service.users().messages().get(
                userId="me",
                id=reply_to_message_id,
                format="metadata",
                metadataHeaders=["Subject"]
            ).execute()
            
            headers = original_msg.get("payload", {}).get("headers", [])
            original_subject = next(
                (h["value"] for h in headers if h["name"] == "Subject"),
                "No Subject"
            )
            
            if not original_subject.lower().startswith("re:"):
                subject = f"Re: {original_subject}"
            else:
                subject = original_subject
        except Exception as e:
            subject = "Re: (No Subject)"

    message = MIMEMultipart("alternative")
    recipient = to_email or tok.get("user_email")
    if not recipient:
        profile = service.users().getProfile(userId="me").execute()
        recipient = profile.get("emailAddress")

    message["to"] = recipient
    message["subject"] = subject if subject else "(No Subject)"
    if is_reply:
        message["In-Reply-To"] = reply_to_message_id
        message["References"] = reply_to_message_id

    message.attach(MIMEText(body_text, "plain"))
    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    body = {"raw": raw_message}
    if thread_id and thread_id.strip():
        body["threadId"] = thread_id

    sent_msg = service.users().messages().send(userId="me", body=body).execute()
    return {
        "status": "success",
        "message_id": sent_msg.get("id"),
        "thread_id": sent_msg.get("threadId")
    }

@router.post("/send")
async def send_email_endpoint(request: GmailRequest):
    return send_email_function(
        user_id=request.user_id,
        body_text=request.body_text,
        to_email=request.to_email,
        subject=request.subject,
        thread_id=request.thread_id,
        reply_to_message_id=request.reply_to_message_id
    )