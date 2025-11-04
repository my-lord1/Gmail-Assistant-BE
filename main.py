from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import auth_router, emails_router
from routers.settings import FRONTEND_URL
from inngest.cron import start_scheduler, stop_scheduler
from inngest.storage import verify_mongodb_connection
from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        if not verify_mongodb_connection():
            print("mongoDB connection failed")
        start_scheduler()

    except Exception as e:
        print(f"startup error: {e}")
        raise

    yield  #App runs here

    try:
        stop_scheduler()
    except Exception as e:
        print(f"shutdown error: {e}")


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(emails_router.router)

@app.get("/")
def read_root():
    return {"message": "Gmail API Backend is running"}
