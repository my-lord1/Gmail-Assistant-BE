import asyncio
from inngest.storage import store_threads_to_mongo
from routers.emails_router import fetch_primary_inbox_emails_threaded
from routers.stores import get_all_tokens

async def sync_user_inbox(user_id: str):
    try:
        data = await fetch_primary_inbox_emails_threaded(user_id)
        store_threads_to_mongo(user_id, data)
    except Exception as e:
        print(f"failed syncing for {user_id}: {e}")

async def sync_all_users():
    all_tokens = get_all_tokens()
    if not all_tokens:
        return

    user_ids = [t["user_id"] for t in all_tokens if "user_id" in t]
    tasks = [sync_user_inbox(user_id) for user_id in user_ids]
    await asyncio.gather(*tasks, return_exceptions=True)

#731