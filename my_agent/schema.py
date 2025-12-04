from pydantic import BaseModel, Field
from typing_extensions import TypedDict, Literal, Optional
from langgraph.graph import MessagesState

class RouterSchema(BaseModel):
    """Analyze the unread email and route it according to its content."""

    reasoning: str = Field(
        description="Step-by-step reasoning behind the classification."
    )
    classification: Literal["ignore", "respond", "notify"] = Field(
        description="The classification of an email: 'ignore' for irrelevant emails, "
        "'notify' for important information that doesn't need a response, "
        "'respond' for emails that need a reply",
    )

class StateInput(TypedDict):
    email_input: dict
    user_response: Optional[dict]  

class State(MessagesState):
    email_input: dict
    classification_decision: Literal["ignore", "respond", "notify"]
    user_response: Optional[dict]  

class UserPreferences(BaseModel):
    """Updated user preferences based on user's feedback."""
    chain_of_thought: str = Field(description="Reasoning about which user preferences need to add/update if required")
    user_preferences: str = Field(description="Updated user preferences")