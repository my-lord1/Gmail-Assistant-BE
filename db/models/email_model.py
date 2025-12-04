from pydantic import BaseModel, Field, EmailStr
from typing import List, Dict, Any
from datetime import datetime

class GmailMessage(BaseModel):
    id: str
    from_: str = Field(alias="from")
    to: str
    subject: str
    date: str
    sent_time: str
    is_unread: bool
    body_html: str
    body_clean: str

class GmailThread(BaseModel):
    user_id: str
    thread_id: str
    message_count: int
    subject: str
    participants: List[str]
    messages: List[GmailMessage]

class TokenSchema(BaseModel):
    user_id: str = Field(..., description="Unique Google user ID")
    user_email: EmailStr = Field(..., description="User's Gmail address")
    access_token: str = Field(..., description="OAuth access token for Gmail API")
    refresh_token: str = Field(..., description="OAuth refresh token to regenerate access token")
    scopes: List[str] = Field(default_factory=list, description="List of granted OAuth scopes")
    expiry: datetime = Field(..., description="Access token expiry time")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Record creation timestamp")

class StateSchema(BaseModel):
    state_key: str = Field(..., description="Unique random string generated for OAuth state")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Timestamp when this state was created")


class AgentMemory(BaseModel):
    namespace: str = Field(..., description="User or agent namespace (e.g. user_id)")
    key: str = Field(..., description="Unique key for this memory entry")
    value: Dict[str, Any] = Field(default_factory=dict, description="Stored memory data")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)