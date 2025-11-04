import requests
from typing import Optional, Dict, Any, List
from langchain_core.tools import tool
from bs4 import BeautifulSoup
import re
from routers.settings import BACKEND_URL
from fastapi import HTTPException
from google.oauth2.credentials import Credentials
from routers.stores import TOKEN_STORE
from routers.settings import CLIENT_CONFIG, SCOPES
from google.auth.transport.requests import Request as GoogleAuthRequest
from googleapiclient.discovery import build
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Callable, Any, Optional
from langchain_core.tools import BaseTool
from pydantic import BaseModel

#helper funcitons
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

def get_credentials(user_id: str) -> Credentials:
    if user_id not in TOKEN_STORE:
        raise ValueError("No authentication tokens found for this user. User needs to login first.")
    
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
    
    return creds

def mark_email_as_read(user_id: str, message_id: str) -> Dict[str, Any]:
    creds = get_credentials(user_id)
    service = build("gmail", "v1", credentials=creds)
    result = service.users().messages().modify(
        userId="me",
        id=message_id,
        body={"removeLabelIds": ["UNREAD"]}
    ).execute()

    return True

@tool
def fetch_emails(
    user_id: str,
    max_threads: int = 10,
    max_messages_per_thread: int = 10,
    include_read: bool = True
) -> Dict[str, Any]:
    """fetch emails of the userid"""
    try:
        url = f"{BACKEND_URL}/emails/full-threaded/{user_id}"
        params = {
            "max_threads": max_threads,
            "max_messages_per_thread": max_messages_per_thread,
            "include_read": include_read
        }
        
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        
        data = response.json()
            
        for thread in data.get("threads", []):
            for message in thread.get("messages", []):
                # Add clean body field
                if message.get("body_html"):
                    message["body"] = parse_email_html(message["body_html"])
                elif message.get("body_text"):
                    message["body"] = message["body_text"]
                else:
                    message["body"] = message.get("snippet", "")
                
                message.pop("body_html", None)
                message.pop("body_text", None)
                message.pop("snippet", None)

        return {
            "success": True,
            "data": data
        }
        
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return {
                "success": False,
                "error": "No authentication tokens found for this user. User needs to login first."
            }

@tool
def send_email(
    user_id: str,
    body_text: str,
    to_email: Optional[str] = None,
    subject: Optional[str] = None,
    thread_id: Optional[str] = None,
    reply_to_message_id: Optional[str] = None
) -> Dict[str, Any]:
    """Send an email to a recipient"""
    try:
        url = f"{BACKEND_URL}/emails/send"
        payload = {
            "user_id": user_id,
            "body_text": body_text
        }
        
        if to_email:
            payload["to_email"] = to_email
        if subject:
            payload["subject"] = subject
        if thread_id:
            payload["thread_id"] = thread_id
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        return {
            "success": True,
            "data": data
        }
        
    except requests.exceptions.HTTPError as e:
        raise HTTPException(status_code=500, detail=f"Failed to send email: {e}")

@tool
def check_calendar(
    user_id: str,
    dates: List[str]
) -> Dict[str, Any]:
    """check the calendar for the these dates"""
    try:        
        creds = get_credentials(user_id)
        service = build("calendar", "v3", credentials=creds)
        
        # Prepare date ranges
        ist = timezone(timedelta(hours=5, minutes=30))
        date_ranges = []
        
        # Parse provided dates
        for date_str in dates:
            try:
                dt = datetime.strptime(date_str.strip(), "%d-%m-%Y")
                dt = dt.replace(tzinfo=ist)
                date_ranges.append({
                    "date": date_str.strip(),
                    "start": dt.replace(hour=0, minute=0, second=0),
                    "end": dt.replace(hour=23, minute=59, second=59)
                })
            except ValueError:
                return {
                    "success": False,
                    "error": f"Invalid date format: {date_str}. Use DD-MM-YYYY format."
                }
        
        if not date_ranges:
            return {
                "success": False,
                "error": "No valid dates provided."
            }
        
        # Fetch events for each date
        all_events = []
        events_by_date = {}
        
        for date_range in date_ranges:
            time_min = date_range["start"].isoformat()
            time_max = date_range["end"].isoformat()
            
            try:
                events_result = service.events().list(
                    calendarId='primary',
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy='startTime'
                ).execute()
                
                events = events_result.get('items', [])
                date_key = date_range["date"]
                events_by_date[date_key] = []
                
                for event in events:
                    start = event['start'].get('dateTime', event['start'].get('date'))
                    end = event['end'].get('dateTime', event['end'].get('date'))
                    
                    # Parse and format times
                    try:
                        if 'T' in start:  # DateTime format
                            start_dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
                            end_dt = datetime.fromisoformat(end.replace('Z', '+00:00'))
                            start_dt = start_dt.astimezone(ist)
                            end_dt = end_dt.astimezone(ist)
                            formatted_start = start_dt.strftime("%I:%M %p")
                            formatted_end = end_dt.strftime("%I:%M %p")
                            time_display = f"{formatted_start} - {formatted_end}"
                            is_all_day = False
                        else:  # Date only (all-day event)
                            time_display = "All day"
                            is_all_day = True
                    except Exception:
                        time_display = "Time not available"
                        is_all_day = False
                    
                    event_data = {
                        "id": event.get("id"),
                        "summary": event.get("summary", "No title"),
                        "description": event.get("description", ""),
                        "location": event.get("location", ""),
                        "start": start,
                        "end": end,
                        "time_display": time_display,
                        "is_all_day": is_all_day,
                        "attendees": [
                            {
                                "email": attendee.get("email"),
                                "response_status": attendee.get("responseStatus", "needsAction")
                            }
                            for attendee in event.get("attendees", [])
                        ],
                        "link": event.get("htmlLink")
                    }
                    
                    events_by_date[date_key].append(event_data)
                    all_events.append(event_data)
            
            except Exception as e:
                events_by_date[date_key] = []
        
        # Generate availability summary
        availability_summary = {}
        for date_key, events in events_by_date.items():
            if not events:
                availability_summary[date_key] = "Free all day"
            elif len(events) == 1 and events[0]["is_all_day"]:
                availability_summary[date_key] = f"All-day event: {events[0]['summary']}"
            else:
                availability_summary[date_key] = f"{len(events)} event(s) scheduled"
        
        return {
            "success": True,
            "data": {
                "total_events": len(all_events),
                "date_range_checked": len(date_ranges),
                "events_by_date": events_by_date,
                "availability_summary_text": "\n".join([f"{k}: {v}" for k, v in availability_summary.items()]),
                "all_events": all_events
            }
        }
        
    except ImportError as e:
        return {
            "success": False,
            "error": f"Failed to import required modules: {str(e)}"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Unexpected error: {str(e)}"
        }

@tool
def schedule_meeting(
    user_id: str,
    attendees: List[str],
    title: str,
    start_time: str,
    end_time: str,
    timezone: str,
    description: Optional[str] = ""
) -> Dict[str, Any]:
    """schedule meetings for that date"""
    try:
        creds = get_credentials(user_id)
        service = build("calendar", "v3", credentials=creds)

        # Step 2: Validate datetime inputs
        try:
            start_dt = datetime.fromisoformat(start_time)
            end_dt = datetime.fromisoformat(end_time)
            if start_dt >= end_dt:
                return {
                    "success": False,
                    "error": "Start time must be earlier than end time."
                }
        except ValueError:
            return {
                "success": False,
                "error": "Invalid datetime format. Use ISO 8601 format (YYYY-MM-DDTHH:MM:SS)."
            }

        # Step 3: Build event payload
        event = {
            "summary": title,
            "description": description or "",
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": timezone
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": timezone
            },
            "attendees": [{"email": email.strip()} for email in attendees if email.strip()],
            "reminders": {
                "useDefault": True
            },
        }

        # Step 4: Create event in calendar
        created_event = service.events().insert(
            calendarId="primary",
            body=event,
            sendUpdates="all"  # ensures invites are emailed
        ).execute()

        # Step 5: Build response
        return True

    except ImportError as e:
        return {
            "success": False,
            "error": f"Missing required imports: {str(e)}"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to schedule meeting: {str(e)}"
        }

@tool
class Done(BaseModel):
    """E-mail has been sent."""
    done: bool

@tool
class Question(BaseModel):
      """Question to ask user."""
      content: str

def get_tools(tool_names: Optional[List[str]]) -> List[BaseTool]:
    all_tools ={"fetch_emails_tool": fetch_emails,
                "send_email_tool": send_email,
                "check_calendar_tool": check_calendar,
                "schedule_meeting_tool": schedule_meeting,
                "Done": Done,
                "Question": Question}

    if tool_names is None:
        return list(all_tools.values())
    
    return [all_tools[name] for name in tool_names if name in all_tools]

def get_tools_by_name(tools: Optional[List[BaseTool]] = None) -> Dict[str, BaseTool]:
    if tools is None:
        tools = get_tools() 
    
    return {tool.name: tool for tool in tools}
