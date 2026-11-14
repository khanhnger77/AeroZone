"""Odyssey Copilot Backend Package.

Mounting in FastAPI:
    from odyssey_copilot.backend import router as copilot_router
    app.include_router(copilot_router, prefix="/api/copilot")
"""

from .schemas import ChatRequest, ChatResponse, CopilotAction, LonLat, ChatMessage
from .router import router, copilot_router, chat
from .prompt import SYSTEM_PROMPT
from .tools import (
    get_sample_info,
    inspect_terrain,
    plan_route,
    SAMPLES_DB,
    LANDMARKS_DB
)

__all__ = [
    "router",
    "copilot_router",
    "chat",
    "ChatRequest",
    "ChatResponse",
    "CopilotAction",
    "LonLat",
    "ChatMessage",
    "SYSTEM_PROMPT",
    "get_sample_info",
    "inspect_terrain",
    "plan_route",
    "SAMPLES_DB",
    "LANDMARKS_DB"
]
