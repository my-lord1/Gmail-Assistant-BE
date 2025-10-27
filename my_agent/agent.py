import os
from dotenv import load_dotenv
from typing import Literal
from langgraph.types import interrupt, Command
from langgraph.store.base import BaseStore
from langgraph.graph import StateGraph, START, END
from langchain_google_genai import ChatGoogleGenerativeAI
from my_agent.tools import get_tools, get_tools_by_name
from my_agent.schema import RouterSchema, State
from my_agent.prompts import triage_system_prompt, default_background, triage_system_prompt, triage_user_prompt, default_triage_instructions, MEMORY_UPDATE_INSTRUCTIONS, default_cal_preferences, default_response_preferences, agent_system_prompt_hitl_memory, GMAIL_TOOLS_PROMPT
from my_agent.utils import parse_gmail
from langgraph.store.memory import InMemoryStore


load_dotenv()

tools = get_tools(["send_email_tool", "check_calendar_tool", "schedule_meeting_tool"])
tools_by_name = get_tools_by_name(tools)

llm = ChatGoogleGenerativeAI(model = "gemini-2.5-pro", api_key = os.getenv("GOOGLE_API_KEY"), temperature = 0)
llm_router = llm.with_structured_output(RouterSchema)
llm_with_tools = llm.bind_tools(tools, tool_choice = "auto")

#tocheck the llm working or not
#result = llm.invoke("write is 3+ 2")
#print(result) #print the whole result
#print(result.content) 

def get_memory(store, namespace, default_content=None):
    """Get memory from the store or initialize with default if it doesn't exist."""
    user_preferences = store.get(namespace, "user_preferences")
    if user_preferences:
        return user_preferences.value
    else:
        store.put(namespace, "user_preferences", default_content)
        user_preferences = default_content
    
    return user_preferences

def update_memory(store, namespace, messages):
    """Update memory profile in the store."""
    user_preferences = store.get(namespace, "user_preferences")
    current_profile = user_preferences.value if user_preferences else ""
    result = llm.invoke(
        [
            {"role": "system", 
             "content": MEMORY_UPDATE_INSTRUCTIONS.format(current_profile = current_profile, namespace = namespace)}
        ] + messages
    )

    store.put(namespace, "user_preferences", result.user_preferences)

#1st node - 
def triage_router(state: State, store: BaseStore) -> Command[Literal["triage_interrupt_handler", "response_agent", "__end__"]]:
    """Analyze email content to decide if we should respond, notify, or ignore.

    The triage step prevents the assistant from wasting time on:
    - Marketing emails and spam
    - Company-wide announcements
    - Messages meant for other teams
    """
    from_, to, subject, body_clean, id_ = parse_gmail(state["email_input"])
    user_prompt = triage_user_prompt.format(
        author=from_,
        to=to,
        subject=subject,
        body=body_clean,
        id=id_,
    )

    triage_instructions = get_memory(store, ("email_assistant", "triage_preferences"), triage_instructions)
    print(triage_instructions)
    system_prompt = triage_system_prompt.format(
        background= default_background,
        triage_instructions= default_triage_instructions,
    )

    result = llm_router.invoke(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
    )

    classification = result.classification
    if classification == "respond":
        goto = "response_agent"
        update = {
            "classification_decision": result.classification,
            "messages": [{"role": "user",
                            "content": f"Respond to the email:"
                        }],
        }
        #take care of this update 
    elif classification == "ignore":
        goto = END
        update = {
            "classification_decision": classification,
        }
    elif classification == "notify":
        goto = "triage_interrupt_handler"
        update = {
            "classification_decision": classification,
        }
    else:
        raise ValueError(f"Invalid classification: {classification}")
    
    return Command(goto=goto, update=update)

#2nd node -
def triage_interrupt_handler(state: State, store: BaseStore) -> Command[Literal["response_agent", "__end__"]]:
    """Handles interrupts from the triage step"""
    from_, to, subject, body_clean, id_ = parse_gmail(state["email_input"])
    email_markdown = {
        "author":from_,
        "to":to,
        "subject":subject,
        "body":body_clean,
        "id":id_,
    }

    messages = [{"role": "user",
            "content": f"Email to notify user about: {email_markdown}"
            }]
    
    request = {
        "action_request": {
            "action": f"Email Assistant: {state['classification_decision']}",
            "args": {}
        },
        "config": {
            "allow_ignore": True,  
            "allow_respond": True,
            "allow_edit": False, 
            "allow_accept": False,  
        },
        # Email to show in frontend or you can pass it in args for fronenf
        "description": email_markdown,
    }
    
    response = interrupt([request])[0]
    if response["type"] == "response":
        user_input = response["args"]
        messages.append({"role": "user",
                        "content": f"User wants to reply to the email. Use this feedback to respond: {user_input}"
                        })
        
        update_memory(store, ("email_assistant", "triage_preferences"), [{
            "role": "user",
            "content": f"The user decided to respond to the email, so update the triage preferences to capture this."
        }] + messages)
        goto = "response_agent"

    elif response["type"] == "ignore":
        messages.append({"role": "user",
                        "content": f"The user decided to ignore the email even though it was classified as notify. Update triage preferences to capture this."
                        })
        
        update_memory(store, ("email_assistant", "triage_preferences"), messages)
        goto = END

    else:
        raise ValueError(f"Invalid response: {response}")
    
    update = {
        "messages": messages,
    }

    return Command(goto=goto, update=update)

#3rd node -
def llm_call(state: State, store: BaseStore):
    """LLM decides whether to call a tool or not"""
    cal_preferences = get_memory(store, ("email_assistant", "cal_preferences"), default_cal_preferences)
    response_preferences = get_memory(store, ("email_assistant", "response_preferences"), default_response_preferences)

    return {
        "messages": [
            llm_with_tools.invoke(
                [
                    {"role": "system", "content": agent_system_prompt_hitl_memory.format(
                        tools_prompt = GMAIL_TOOLS_PROMPT,
                        background = default_background,
                        response_preferences = response_preferences,
                        cal_preferences = cal_preferences
                    )}
                ]+state["messages"]
            )
        ]
    }


