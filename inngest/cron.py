from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from inngest.storage import store_threads_to_mongo, verify_mongodb_connection
from routers.stores import get_all_tokens
from routers.emails_router import fetch_primary_inbox_emails_threaded_sync

scheduler = None  # global scheduler instance

def sync_user(user_id: str):
    try:
        data = fetch_primary_inbox_emails_threaded_sync(user_id)
        success = store_threads_to_mongo(user_id, data)
        if success:
            print(f"synced inbox for {user_id}")
        else:
            print(f"data not storing {user_id}")
    except Exception as e:
        print(f"error syncing {user_id}: {e}")

def run_sync_job():
    try:
        tokens = get_all_tokens()
        user_ids = [t["user_id"] for t in tokens if "user_id" in t]

        if not user_ids:
            return

        if not verify_mongodb_connection():
            return

        for user_id in user_ids:
            sync_user(user_id)

    except Exception as e:
        print(f"error in run sync job: {e}")

def start_scheduler():
    global scheduler
    try:
        if scheduler and scheduler.running:
            return

        scheduler = BackgroundScheduler()
        scheduler.add_job(
            run_sync_job,
            trigger=IntervalTrigger(minutes=10),
            id="email_sync_job",
            name="Email sync every 10 minutes",
            replace_existing=True,
            max_instances=1
        )

        scheduler.start()
        run_sync_job()

    except Exception as e:
        raise

def stop_scheduler():
    global scheduler
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
    else:
        print("scheduler was not running")
