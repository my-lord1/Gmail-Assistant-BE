from pymongo import MongoClient
import os
from dotenv import load_dotenv
from langgraph.checkpoint.mongodb import MongoDBSaver
load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME", "gmail_assistant")

client = MongoClient(MONGO_URI, tls=True, tlsAllowInvalidCertificates=True)
db = client[DB_NAME]

#collections
email_threads = db["email_threads"]
token_store = db["token_store"]
state_store = db["state_store"]
user_profiles = db["user_profiles"]
agent_memory = db["agent_memory"]
checkpoints = db["agent_checkpoints"]


#documents
email_threads.create_index([("user_id", 1), ("thread_id", 1), ("messages.is_unread", 1)])
token_store.create_index("user_id", unique=True)
state_store.create_index("state_key", unique=True)
user_profiles.create_index("user_id", unique=True)
agent_memory.create_index([("namespace", 1), ("key", 1)], unique=True)

mongo_saver = MongoDBSaver(
    client=client,
    db_name=DB_NAME,
    collection_name="agent_checkpoints",
)