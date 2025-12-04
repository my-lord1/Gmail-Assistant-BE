import os
from dotenv import load_dotenv
from typing import Literal
from langgraph.types import interrupt, Command
from langgraph.store.base import BaseStore
from langgraph.graph import StateGraph, START, END
from langchain_google_genai import ChatGoogleGenerativeAI
from my_agent.tools import get_tools, get_tools_by_name, mark_email_as_read
from my_agent.schema import RouterSchema, State, StateInput
from my_agent.prompts import triage_system_prompt, default_background, triage_system_prompt, triage_user_prompt, default_triage_instructions, MEMORY_UPDATE_INSTRUCTIONS, default_cal_preferences, default_response_preferences, agent_system_prompt_hitl_memory, GMAIL_TOOLS_PROMPT, MEMORY_UPDATE_INSTRUCTIONS_REINFORCEMENT
from my_agent.utils import parse_gmail
from langsmith import traceable
from db.mongodb import db
from db.mongodb_store import MongoDBStore
from db.mongodb import mongo_saver

load_dotenv()
checkpointer = mongo_saver
memory_store = MongoDBStore(db)


tools = get_tools(["send_email", "check_calendar", "schedule_meeting", "Question", "Done"])


tools_by_name = get_tools_by_name(tools)

llm = ChatGoogleGenerativeAI(model = "gemini-2.5-pro", api_key = os.getenv("GOOGLE_API_KEY"), temperature = 0)
llm_router = llm.with_structured_output(RouterSchema)
llm_with_tools = llm.bind_tools(tools, tool_choice = "auto")

#tocheck the llm working or not
#result = llm.invoke("write is 3+ 2")
#print(result) #print the whole result
#print(result.content) 

def get_memory(store, namespace, default_content=None):
    """Fetch or safely initialize memory without causing duplicate inserts."""
    
    user_preferences = store.get(namespace, "user_preferences")
    if user_preferences:
        return user_preferences

    try:
        store.put(namespace, "user_preferences", default_content)
        return default_content
    except:
        existing = store.get(namespace, "user_preferences")
        return existing if existing else default_content

def update_memory(store, namespace, messages):
    """Update memory profile in the store."""
    
    user_preferences = store.get(namespace, "user_preferences")
    if user_preferences is None:
        current_profile = ""
    elif hasattr(user_preferences, 'value'):
        current_profile = user_preferences.value
    else:
        current_profile = str(user_preferences)
    
    result = llm.invoke(
        [
            {"role": "system", 
             "content": MEMORY_UPDATE_INSTRUCTIONS.format(current_profile=current_profile, namespace=namespace)}
        ] + messages
    )

    store.put(namespace, "user_preferences", result.content if hasattr(result, 'content') else str(result))

#1st node 
@traceable
def triage_router(state: State, store: BaseStore) -> Command[Literal["triage_interrupt_handler", "response_agent", "__end__", "mark_as_read_node"]]:
    """Analyze email content to decide if we should respond, notify, or ignore."""

    from_, to, subject, body_clean, id_ = parse_gmail(state["email_input"])

    user_prompt = triage_user_prompt.format(
        author=from_, to=to, subject=subject, body=body_clean, id=id_,
    )

    triage_instructions = get_memory(memory_store, ("email_assistant", "triage_preferences"), default_triage_instructions)

    system_prompt = triage_system_prompt.format(
        background=default_background,
        triage_instructions=triage_instructions,
    )

    result = llm_router.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ])

    classification = getattr(result, "classification", None)

    if classification == "respond":
        goto = "response_agent"
        update = {
            "classification_decision": classification,
            "messages": state.get("messages", []) + [
                {"role": "user", "content": f"Respond to the email: {user_prompt}"}
            ],
            "email_input": state.get("email_input"),
        }
    elif classification == "ignore":
        goto = "mark_as_read_node"
        update = {"classification_decision": classification, "email_input": state.get("email_input")}
    elif classification == "notify":
        goto = "triage_interrupt_handler"
        update = {"classification_decision": classification, "email_input": state.get("email_input")}
        
    cmd = Command(goto=goto, update=update)
    return cmd


#2nd node -
@traceable
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
        "description": {
            "question": "What should we do with this email?",
            "options": ["response", "ignore"]
  }
    }
    
    response = interrupt([request])
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
    
    update = {
        "messages": messages,
    }

    return Command(goto=goto, update=update)

#subagent
# response_agent - node1
@traceable
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

# response_agent - node3
@traceable
def interrupt_handler(state: State, store: BaseStore) -> Command[Literal["llm_call", "__end__"]]:
    """Creates an interrupt for human review of tool calls """

    result = []
    goto = "llm_call"
    hitl_tools = ["send_email", "schedule_meeting", "Question"]

    for tool_call in state["messages"][-1].tool_calls:
        if tool_call["name"] not in hitl_tools:
            tool = tools_by_name[tool_call["name"]]
            observation = tool.invoke(tool_call["args"])
            result.append({"role": "tool", "content": observation, "tool_call_id": tool_call["id"]})
    
    hitl_tool_call = next((tc for tc in state["messages"][-1].tool_calls if tc["name"] in hitl_tools), None)
    
    if not hitl_tool_call:
        return Command(goto="llm_call", update={"messages": state["messages"] + result})
    
    tool_call = hitl_tool_call
    email_input = state["email_input"]
    from_, to, subject, body_clean, id_ = parse_gmail(email_input)
    
    description = {
        "author": from_,
        "to": to,
        "subject": subject,
        "body": body_clean,
        "id": id_,
    }
    
    config = {
        "allow_ignore": True,
        "allow_respond": True,
        "allow_edit": True,
        "allow_accept": True,
    }

    request = {
        "action_request": {
            "action": tool_call["name"],
            "args": tool_call["args"]
        },
        "config": config,
        "description": description,
    }

    response = interrupt(request) 

    if response.get("type") == "accept":
        tool = tools_by_name[tool_call["name"]]
        observation = tool.invoke(tool_call["args"])
        result.append({"role": "tool", "content": observation, "tool_call_id": tool_call["id"]})
    
    elif response.get("type") == "edit":
        tool = tools_by_name[tool_call["name"]]
        edited_args = response.get("args", tool_call["args"])

        ai_message = state["messages"][-1]
        current_id = tool_call["id"]
        updated_tool_calls = [
            tc for tc in ai_message.tool_calls if tc["id"] != current_id
        ] + [
            {"type": "tool_call", "name": tool_call["name"], "args": edited_args, "id": current_id}
        ]
        result.append(ai_message.model_copy(update={"tool_calls": updated_tool_calls}))
        
        observation = tool.invoke(edited_args)
        result.append({"role": "tool", "content": observation, "tool_call_id": current_id})
        
        if tool_call["name"] == "send_email":
            update_memory(store, ("email_assistant", "response_preferences"), [{
                "role": "user",
                "content": f"User edited the email with args: {edited_args}. {MEMORY_UPDATE_INSTRUCTIONS_REINFORCEMENT}."
            }])
        elif tool_call["name"] == "schedule_meeting":
            update_memory(store, ("email_assistant", "cal_preferences"), [{
                "role": "user",
                "content": f"User edited the meeting with args: {edited_args}. {MEMORY_UPDATE_INSTRUCTIONS_REINFORCEMENT}."
            }])
    
    elif response.get("type") == "ignore":
        result.append({"role": "tool", "content": "User ignored this action.", "tool_call_id": tool_call["id"]})
        goto = "__end__"
        update_memory(store, ("email_assistant", "triage_preferences"), [{
            "role": "user",
            "content": f"User ignored an action. Update preferences. {MEMORY_UPDATE_INSTRUCTIONS_REINFORCEMENT}."
        }])
    
    elif response.get("type") == "response":
        user_feedback = response.get("args", "")
        result.append({"role": "tool", "content": f"User feedback: {user_feedback}", "tool_call_id": tool_call["id"]})
        
        if tool_call["name"] == "send_email":
            update_memory(store, ("email_assistant", "response_preferences"), [{
                "role": "user",
                "content": f"User provided feedback: {user_feedback}. {MEMORY_UPDATE_INSTRUCTIONS_REINFORCEMENT}."
            }])
        elif tool_call["name"] == "schedule_meeting":
            update_memory(store, ("email_assistant", "cal_preferences"), [{
                "role": "user",
                "content": f"User provided feedback: {user_feedback}. {MEMORY_UPDATE_INSTRUCTIONS_REINFORCEMENT}."
            }])
    
    return Command(goto=goto, update={"messages": state["messages"] + result})

# response_agent - node2
@traceable
def should_continue(state: State, store: BaseStore):
    """Inside the sub-agent:
       - If LLM calls Done → END sub-agent.
       - Otherwise → interrupt_handler."""
    
    messages = state["messages"]
    last_message = messages[-1]

    tool_calls = last_message.tool_calls or []

    for tool_call in tool_calls:
        if tool_call.get("name") == "Done":
            return END  

    return "interrupt_handler"


# response_agent - node4
@traceable
def mark_as_read_node(state: State):
    """
    This runs AFTER the entire response_agent workflow completes.
    """
    email_input = state["email_input"]
    message_id = email_input["id"]
    user_id  = email_input["user_id"]

    coll = db["email_threads"]

    coll.update_one(
        {"user_id": user_id, "messages.id": message_id},
        {"$set": {"messages.$.is_unread": False}}
    )

    return Command(goto=END, update={})


#subagent
agent_builder = StateGraph(State)
agent_builder.add_node("llm_call", llm_call)
agent_builder.add_node("interrupt_handler", interrupt_handler)
agent_builder.add_edge(START, "llm_call")

agent_builder.add_conditional_edges(
    "llm_call",
    should_continue,
    {
        "interrupt_handler": "interrupt_handler",
        END: END,  
    },
)

agent_builder.add_edge("interrupt_handler", "llm_call")
response_agent = agent_builder.compile()


#agent
overall_workflow = (
    StateGraph(State, inout_schema=StateInput)

    .add_node("triage_router", triage_router)
    .add_node("triage_interrupt_handler", triage_interrupt_handler)
    .add_node("response_agent", response_agent)   # sub-agent
    .add_node("mark_as_read_node", mark_as_read_node)  # outer mark-as-read node

    .add_edge(START, "triage_router")
    .add_conditional_edges(
        "triage_router",
        lambda state: state.get("classification_decision"),
        {
            "respond": "response_agent",
            "notify": "triage_interrupt_handler",
            "ignore": "mark_as_read_node",
        }
    )
    .add_conditional_edges(
        "triage_interrupt_handler",
        lambda state: "response_agent" if state.get("classification_decision") == "response" else END,
        {
            "response_agent": "response_agent",
            END: END,
        }
    )

    .add_edge("response_agent", "mark_as_read_node")
    .add_edge("mark_as_read_node", END)
)

email_assistant = overall_workflow.compile(
    checkpointer=mongo_saver,
    store=memory_store
)