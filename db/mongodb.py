from pymongo import MongoClient
import os
from dotenv import load_dotenv

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


#documents
email_threads.create_index([("user_id", 1), ("thread_id", 1)], unique=True)
token_store.create_index("user_id", unique=True)
state_store.create_index("state_key", unique=True)
user_profiles.create_index("user_id", unique=True)