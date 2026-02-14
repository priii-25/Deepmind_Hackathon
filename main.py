"""
Eve — AI Agent Orchestrator
Entry point. Run with: python main.py
"""

import uvicorn

from app.factory import create_app
from app.core.config import get_settings

app = create_app()

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.env == "development",
        log_level=settings.log_level.lower(),
    )
