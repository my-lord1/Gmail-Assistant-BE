from datetime import datetime
from db.mongodb import state_store, token_store

def save_state_key(state_key: str):
    doc = {
        "state_key": state_key,
        "created_at": datetime.now()
    }
    state_store.insert_one(doc)

def get_state_key(state_key: str):
    return state_store.find_one({"state_key": state_key})

def delete_state_key(state_key: str):
    state_store.delete_one({"state_key": state_key})

def save_token(user_id: str, user_email: str, access_token: str, refresh_token: str, scopes: list, expiry: str):
   
    doc = {
        "user_id": user_id,
        "user_email": user_email,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "scopes": scopes,
        "expiry": expiry,
        "created_at": datetime.now().isoformat()
    }

    token_store.update_one(
        {"user_id": user_id},
        {"$set": doc},
        upsert=True
    )

def get_token(user_id: str):
    return token_store.find_one({"user_id": user_id})

def get_all_tokens():
    try:
        return list(token_store.find({}, {"user_id": 1, "_id": 0}))
    except Exception as e:
        print(f"error fetching all tokens: {e}")
        return []


def update_access_token(user_id: str, new_access_token: str, new_expiry: str):
    token_store.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "access_token": new_access_token,
                "expiry": new_expiry
            }
        }
    )


def delete_token(user_id: str):
    token_store.delete_one({"user_id": user_id})



