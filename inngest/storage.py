from pymongo import UpdateOne
from db.mongodb import email_threads, user_profiles
from datetime import datetime

def store_threads_to_mongo(user_id: str, data: dict):
    try:
        if not user_id or not isinstance(user_id, str):
            return False

        threads = data.get("threads", [])
        user_info = data.get("user_info", {})

        if not threads:
            return False

        operations = []

        for thread in threads:
            thread_id = thread.get("threadId") or thread.get("thread_id")
            if not thread_id:
                continue

            doc = {
                "user_id": user_id,
                "thread_id": thread_id,
                "message_count": thread.get("message_count", 0),
                "subject": thread.get("subject", ""),
                "participants": thread.get("participants", []),
                "messages": thread.get("messages", []),
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
            }

            operations.append(
                UpdateOne(
                    {"user_id": user_id, "thread_id": thread_id},
                    {"$set": doc},
                    upsert=True
                )
            )

        if user_info:
            user_profiles.update_one(
                {"user_id": user_id},
                {"$set": {
                    "gmail_id": user_info.get("gmail_id"),
                    "profile_photo": user_info.get("profile_photo"),
                    "user_name": user_info.get("user_name")
                }},
                upsert=True
            )

        if operations:
            result = email_threads.bulk_write(operations, ordered=False)
            return True

        return False

    except Exception as e:
        print(f"error storing threads for {user_id}: {e}")
        return False


def get_user_threads_from_mongo(user_id: str, limit: int = 20):
    try:
        threads = list(
            email_threads.find({"user_id": user_id})
            .sort("updated_at", -1)
            .limit(limit)
        )

        return threads
    except Exception as e:
        print(f"error retrieving threads for {user_id}: {e}")
        return []


def verify_mongodb_connection():
    try:
        email_threads.database.client.server_info()
        return True
    except Exception as e:
        print(f"mongoDB connection failed: {e}")
        return False
