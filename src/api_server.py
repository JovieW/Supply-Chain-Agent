"""Development entry point for the FastAPI service."""
from __future__ import annotations

import os

from dotenv import load_dotenv
import uvicorn


load_dotenv()


if __name__ == "__main__":
    uvicorn.run(
        "api:app",
        host=os.getenv("API_HOST", "127.0.0.1"),
        port=int(os.getenv("API_PORT", "8000")),
        reload=os.getenv("API_RELOAD", "false").strip().lower() == "true",
    )
