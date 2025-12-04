from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, Optional, List
from uuid import uuid4
from langgraph.types import Command
from langchain_core.messages import AIMessage, HumanMessage
from my_agent.agent import email_assistant, memory_store, llm
from db.mongodb import db

router = APIRouter(prefix="/api/agent", tags=["agent-v2"])

class ProcessEmailRequest(BaseModel):
    user_id: str
    order: Optional[str] = "newest"
    email_id: str 

class ResumeRequest(BaseModel):
    thread_id: str
    user_response: Dict[str, Any]

class SummarizeRequest(BaseModel):
    user_id: str

def _email_threads_collection():
    return db["email_threads"]

def fetch_unread_emails(user_id: str, order: str = "newest") -> List[Dict[str, Any]]:
    coll = _email_threads_collection()
    sort_dir = -1 if order == "newest" else 1
    unread_threads = list(
        coll.find({"user_id": user_id, "messages.is_unread": True}).sort("created_at", sort_dir)
    )
    emails: List[Dict[str, Any]] = []
    for thread in unread_threads:
        for msg in thread.get("messages", []):
            if msg.get("is_unread"):
                emails.append({
                    "id": msg.get("id"),
                    "from": msg.get("from", ""),
                    "to": msg.get("to", ""),
                    "subject": msg.get("subject", ""),
                    "body": msg.get("body_clean", "") or msg.get("body", ""),
                    "time": msg.get("date", ""),
                    "thread_id": thread.get("thread_id"),
                    "user_id": thread.get("user_id")
                })
    return emails


def _extract_interrupt(agent_result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(agent_result, dict):
        return None
    if "__interrupt__" in agent_result:
        return {"status": "interrupted", "interrupt_payload": agent_result.get("__interrupt__", [])}
    if agent_result.get("type") == "interrupt":
        return {"status": "interrupted", "interrupt_payload": agent_result.get("values", {})}
    return None

@router.get("/get-unread-emails")
async def get_unread_emails(user_id: str, order: str = "newest"):
    unread = fetch_unread_emails(user_id, order=order)
    return {"status": "success", "emails": unread, "count": len(unread)}

@router.post("/process-email")
async def process_email(req: ProcessEmailRequest):
    user_id = req.user_id
    email_id = req.email_id
    order = (req.order or "newest").lower()
    unread = fetch_unread_emails(user_id, order=order)

    if not unread:
        return {"status": "done", "message": "No unread emails found", "next": False}

    current_email = next((e for e in unread if e["id"] == email_id), None)
    thread_id = f"thread_{uuid4().hex[:12]}"

    result = email_assistant.invoke(
        input={"email_input": current_email, "messages": []},
        config={"configurable": {"thread_id": thread_id}},
        store=memory_store
    )
    interrupt = _extract_interrupt(result)
    if interrupt:
        mail_preview = {
            "from": current_email.get("from", ""),
            "to": current_email.get("to", ""),
            "subject": current_email.get("subject", ""),
            "body": current_email.get("body", ""),
        }
        return {
            "status": "interrupted",
            "thread_id": thread_id,
            "mail_preview": mail_preview,
            "interrupt_payload": interrupt.get("interrupt_payload", [])
        }
    return {
        "status": "completed",
        "message": "Email processed successfully",
        "classification_decision": result.get("classification_decision"),
        "next": True
    }

@router.post("/resume")
async def resume(req: ResumeRequest):
    config = {"configurable": {"thread_id": req.thread_id}}
    user_response_data = req.user_response

    if user_response_data.get("type") == "edit":
        snapshot = email_assistant.get_state(config)
        existing_messages = snapshot.values.get("messages", [])
        last_msg = existing_messages[-1] if existing_messages else None

        if last_msg and isinstance(last_msg, AIMessage) and last_msg.tool_calls:
            original_tool_call = last_msg.tool_calls[0]
            new_args = user_response_data.get("args", {})
            updated_tool_call = original_tool_call.copy()
            updated_tool_call["args"] = new_args
            updated_msg = AIMessage(
                id=last_msg.id,
                content=last_msg.content,
                tool_calls=[updated_tool_call],
                response_metadata=last_msg.response_metadata
            )
            email_assistant.update_state(config, {"messages": updated_msg})
            result = email_assistant.invoke(None, config=config, store=memory_store)
        else:
            result = email_assistant.invoke(
                Command(resume=user_response_data),
                config=config,
                store=memory_store
            )
    else:
        result = email_assistant.invoke(
            Command(resume=user_response_data),
            config=config,
            store=memory_store
        )

    if "__interrupt__" in result:
        interrupt_data = result.get("__interrupt__", [])
        interrupt_payload = []
        if isinstance(interrupt_data, list) and interrupt_data:
            interrupt_obj = interrupt_data[0]
            interrupt_payload = getattr(interrupt_obj, "value", interrupt_data)
        return {"status": "interrupted", "thread_id": req.thread_id, "interrupt_payload": interrupt_payload}

    return {
        "status": "completed",
        "message": "Email processed successfully",
        "classification_decision": result.get("classification_decision"),
        "next": True
    }

@router.post("/summarize")
async def summarize_inbox(req: SummarizeRequest):
    user_id = req.user_id

    unread_emails = fetch_unread_emails(user_id, order="newest")

    if not unread_emails:
        return {"status": "success", "summary": "You have no unread emails! 🎉"}

    top_emails = unread_emails[:10]
    email_text = ""
    for e in top_emails:
        clean_body = (e.get("body") or "").replace("\n", " ").strip()[:300]
        email_text += f"--- \nFROM: {e['from']}\nSUBJECT: {e['subject']}\nBODY: {clean_body}...\n\n"

    prompt = f"""
    You are an intelligent executive assistant. 
    Review the following {len(top_emails)} unread emails and provide a concise 'Executive Summary'.
    Format your response as a clean paragraph.
    EMAILS:
    {email_text}
    """

    response = llm.invoke([HumanMessage(content=prompt)])
    return {"status": "success", "summary": response.content}


#5:37pm