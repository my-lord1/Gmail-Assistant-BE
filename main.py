from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import auth_router, emails_router
from routers.settings import FRONTEND_URL
import logging
logging.getLogger('googleapiclient.discovery_cache').setLevel(logging.ERROR)
logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)

app = FastAPI()

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