from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import auth_router, emails_router
from routers.settings import FRONTEND_URL
from inngest.cron import start_scheduler, stop_scheduler
import logging
from contextlib import asynccontextmanager

logging.getLogger('googleapiclient.discovery_cache').setLevel(logging.ERROR)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


from inngest.storage import verify_mongodb_connection


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for FastAPI.
    Handles startup and shutdown events.
    """
    # STARTUP
    try:
        logger.info("🚀 Starting up application...")
        
        # Verify MongoDB connection
        if not verify_mongodb_connection():
            logger.warning("⚠️ MongoDB connection failed at startup, but continuing anyway")
        
        # Start the scheduler
        start_scheduler()
        
        logger.info("✅ Application startup complete")
    except Exception as e:
        logger.error(f"❌ Startup error: {str(e)}", exc_info=True)
        raise
    
    yield  # App is running
    
    # SHUTDOWN
    try:
        logger.info("🛑 Shutting down application...")
        stop_scheduler()
        logger.info("✅ Application shutdown complete")
    except Exception as e:
        logger.error(f"❌ Shutdown error: {str(e)}", exc_info=True)


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

