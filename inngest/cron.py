from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from inngest.storage import store_threads_to_mongo, verify_mongodb_connection, clear_user_threads
from routers.stores import get_all_tokens
from routers.emails_router import fetch_primary_inbox_emails_threaded_sync

scheduler = None  

def sync_user(user_id: str):
    data = fetch_primary_inbox_emails_threaded_sync(user_id)
    if data:
        clear_user_threads(user_id)
        store_threads_to_mongo(user_id, data)
  
        
def run_sync_job():
    tokens = get_all_tokens()
    user_ids = [t["user_id"] for t in tokens if "user_id" in t]

    if not user_ids:
        return
    if not verify_mongodb_connection():
        return
    for user_id in user_ids:
        sync_user(user_id)



def start_scheduler():
    global scheduler
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


def stop_scheduler():
    global scheduler
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
    else:
        print("scheduler was not running")
