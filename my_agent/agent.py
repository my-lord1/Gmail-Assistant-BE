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
from langgraph.store.memory import InMemoryStore

load_dotenv()

tools = get_tools(["send_email_tool", "check_calendar_tool", "schedule_meeting_tool", "Question", "Done"])
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

    store.put(namespace, "user_preferences", result.user_preferences) #try result.content

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

    triage_instructions = get_memory(store, ("email_assistant", "triage_preferences"), default_triage_instructions)
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

#subagent
# response_agent - node1
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
def interrupt_handler(state: State, store: BaseStore) -> Command[Literal["llm_call", "__end__"]]:
    """Creates an interrupt for human review of tool calls"""
    result = []
    goto = "llm_call"

    for tool_call in state["messages"][-1].tool_calls:
        hitl_tools = ["send_email_tool", "schedule_meeting_tool", "Question"]
        if tool_call["name"] not in hitl_tools:
            tool = tools_by_name[tool_call["name"]]
            observation = tool.invoke(tool_call["args"])
            result.append({"role": "tool", "content": observation, "tool_call_id": tool_call["id"]})
            continue

    email_input = state["email_input"]
    from_, to, subject, body_clean, id_ = parse_gmail(email_input)
    description = {
        "author":from_,
        "to":to,
        "subject":subject,
        "body":body_clean,
        "id":id_,
    }
    if tool_call["name"] == "send_email_tool":
            config = {
                "allow_ignore": True,
                "allow_respond": True,
                "allow_edit": True,
                "allow_accept": True,
            }
    elif tool_call["name"] == "schedule_meeting_tool":
            config = {
                "allow_ignore": True,
                "allow_respond": True,
                "allow_edit": True,
                "allow_accept": True,
            }
    elif tool_call["name"] == "Question":
            config = {
                "allow_ignore": True,
                "allow_respond": True,
                "allow_edit": False,
                "allow_accept": False,
            }
    else:
        raise ValueError(f"Invalid tool call: {tool_call['name']}")

    request = {
            "action_request": {
                "action": tool_call["name"],
                "args": tool_call["args"]
            },
            "config": config,
            "description": description,
        }
    
    response = interrupt([request])[0]
    
    if response["type"] == "accept":
        tool = tools_by_name[tool_call["name"]]
        observation = tool.invoke(tool_call["args"])
        result.append({"role": "tool", "content": observation, "tool_call_id": tool_call["id"]})
    
    elif response["type"] == "edit":
        tool = tools_by_name[tool_call["name"]]
        initial_tool_call = tool_call["args"]
        edited_args = response["args"]["args"]

        ai_message = state["messages"][-1]
        current_id = tool_call["id"]
        updated_tool_calls = [tc for tc in ai_message.tool_calls if tc["id"] != current_id] + [
                {"type": "tool_call", "name": tool_call["name"], "args": edited_args, "id": current_id}
            ]
        result.append(ai_message.model_copy(update={"tool_calls": updated_tool_calls}))
        if tool_call["name"] == "send_email_tool":
            observation = tool.invoke(edited_args)
            result.append({"role": "tool", "content": observation, "tool_call_id": current_id})
            update_memory(store, ("email_assistant", "response_preferences"), [{
                    "role": "user",
                    "content": f"User edited the email response. Here is the initial email generated by the assistant: {initial_tool_call}. Here is the edited email: {edited_args}. Follow all instructions above, and remember: {MEMORY_UPDATE_INSTRUCTIONS_REINFORCEMENT}."
                }])
        
        elif tool_call["name"] == "schedule_meeting_tool":
                observation = tool.invoke(edited_args)
                result.append({"role": "tool", "content": observation, "tool_call_id": current_id})
                update_memory(store, ("email_assistant", "cal_preferences"), [{
                    "role": "user",
                    "content": f"User edited the calendar invitation. Here is the initial calendar invitation generated by the assistant: {initial_tool_call}. Here is the edited calendar invitation: {edited_args}. Follow all instructions above, and remember: {MEMORY_UPDATE_INSTRUCTIONS_REINFORCEMENT}."
                }])

        else:
            raise ValueError(f"Invalid tool call: {tool_call['name']}")
    
    elif response["type"] == "ignore":

            if tool_call["name"] == "send_email_tool":
                result.append({"role": "tool", "content": "User ignored this email draft. Ignore this email and end the workflow.", "tool_call_id": tool_call["id"]})
                goto = END
                update_memory(store, ("email_assistant", "triage_preferences"), state["messages"] + result + [{
                    "role": "user",
                    "content": f"The user ignored the email draft. That means they did not want to respond to the email. Update the triage preferences to ensure emails of this type are not classified as respond. Follow all instructions above, and remember: {MEMORY_UPDATE_INSTRUCTIONS_REINFORCEMENT}."
                }])

            elif tool_call["name"] == "schedule_meeting_tool":
                result.append({"role": "tool", "content": "User ignored this calendar meeting draft. Ignore this email and end the workflow.", "tool_call_id": tool_call["id"]})
                goto = END
                update_memory(store, ("email_assistant", "triage_preferences"), state["messages"] + result + [{
                    "role": "user",
                    "content": f"The user ignored the calendar meeting draft. That means they did not want to schedule a meeting for this email. Update the triage preferences to ensure emails of this type are not classified as respond. Follow all instructions above, and remember: {MEMORY_UPDATE_INSTRUCTIONS_REINFORCEMENT}."
                }])

            elif tool_call["name"] == "Question":
                result.append({"role": "tool", "content": "User ignored this question. Ignore this email and end the workflow.", "tool_call_id": tool_call["id"]})
                goto = END
                update_memory(store, ("email_assistant", "triage_preferences"), state["messages"] + result + [{
                    "role": "user",
                    "content": f"The user ignored the Question. That means they did not want to answer the question or deal with this email. Update the triage preferences to ensure emails of this type are not classified as respond. Follow all instructions above, and remember: {MEMORY_UPDATE_INSTRUCTIONS_REINFORCEMENT}."
                }])

            else:
                raise ValueError(f"Invalid tool call: {tool_call['name']}")
            
    elif response["type"] == "response":
            user_feedback = response["args"]
            if tool_call["name"] == "send_email_tool":
                result.append({"role": "tool", "content": f"User gave feedback, which can we incorporate into the email. Feedback: {user_feedback}", "tool_call_id": tool_call["id"]})
                update_memory(store, ("email_assistant", "response_preferences"), state["messages"] + result + [{
                    "role": "user",
                    "content": f"User gave feedback, which we can use to update the response preferences. Follow all instructions above, and remember: {MEMORY_UPDATE_INSTRUCTIONS_REINFORCEMENT}."
                }])

            elif tool_call["name"] == "schedule_meeting_tool":
                result.append({"role": "tool", "content": f"User gave feedback, which can we incorporate into the meeting request. Feedback: {user_feedback}", "tool_call_id": tool_call["id"]})
                update_memory(store, ("email_assistant", "cal_preferences"), state["messages"] + result + [{
                    "role": "user",
                    "content": f"User gave feedback, which we can use to update the calendar preferences. Follow all instructions above, and remember: {MEMORY_UPDATE_INSTRUCTIONS_REINFORCEMENT}."
                }])

            elif tool_call["name"] == "Question":
                result.append({"role": "tool", "content": f"User answered the question, which can we can use for any follow up actions. Feedback: {user_feedback}", "tool_call_id": tool_call["id"]})

            else:
                raise ValueError(f"Invalid tool call: {tool_call['name']}")

    update = {
        "messages": result,
    }

    return Command(goto=goto, update=update)

# response_agent - node2
def should_continue(state: State, store: BaseStore) -> Literal["interupt_handler", "mark_as_read_node"]:
     """Route to tool handler or end if Done tool called"""
     messages = state["messages"]
     last_message = messages[-1]
     if last_message.tool_calls:
          for tool_call in last_message.tool_calls:
                if tool_call["name"] == "Done":
                    return "mark_as_read_node"
                else:
                     return "interrupt_handler"

# response_agent - node4
def mark_as_read_node(state: State):
    email_input = state["email_input"]
    from_, to, subject, body_clean, id_ = parse_gmail(email_input)
    mark_email_as_read(id_)

agent_builder = StateGraph(State)
agent_builder.add_node("llm_call", llm_call)
agent_builder.add_node("interrupt_handler", interrupt_handler)
agent_builder.add_node("mark_as_read_node", mark_as_read_node)

#subagent
agent_builder.add_edge(START, "llm_call")
agent_builder.add_conditional_edges(
     "llm_call",
     should_continue,
     {
          "interrupt_handler": "interrupt_handler",
          "mark_as_read_node": "mark_as_read_node"
     },
)
agent_builder.add_edge("mark_as_read_node", END)
response_agent = agent_builder.compile()

overall_workflow = (
    StateGraph(State, inout_schema=StateInput)
    .add_node(triage_router)
    .add_node(triage_interrupt_handler)
    .add_node("response_agent", response_agent)
    .add_node("mark_as_read_node", mark_as_read_node)
    .add_edge(START, "triage_router")
    .add_edge("mark_as_read_node", END)
)

email_assistant = overall_workflow.compile()
