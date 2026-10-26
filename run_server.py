"""
Entry point to run Pream with Uvicorn.
Ensures project root is in sys.path and starts the ASGI server.
"""
import os
import sys
from pathlib import Path

# Add project root directory to Python path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn
from app.config import settings

if __name__ == "__main__":
    print(f"Starting {settings.SERVER_NAME} on http://{settings.HOST}:{settings.PORT}")
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True,
    )
