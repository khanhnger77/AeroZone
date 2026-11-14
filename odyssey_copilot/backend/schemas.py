"""Pydantic schemas for Odyssey Copilot."""
from __future__ import annotations
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class LonLat(BaseModel):
    lon: float = Field(..., ge=-180.0, le=180.0, description="Longitude in degrees East")
    lat: float = Field(..., ge=-90.0, le=90.0, description="Latitude in degrees North")


class ChatMessage(BaseModel):
    role: str = Field(..., description="Role of the sender: 'user', 'assistant', or 'model'")
    content: str = Field(..., description="Message text content")


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="Astronaut query or Capcom message")
    history: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Prior conversation turns [{'role': 'user'|'model', 'content': '...'}]"
    )
    current_location: Optional[Dict[str, float]] = Field(
        default=None,
        description="Current EVA coordinates {'lon': float, 'lat': float}"
    )


class CopilotAction(BaseModel):
    action_type: str = Field(
        ...,
        description="Type of map action: 'INSPECT_TERRAIN' | 'PLAN_ROUTE' | 'FOCUS_SAMPLE' | 'NAVIGATE_WAYPOINT'"
    )
    payload: Dict[str, Any] = Field(
        default_factory=dict,
        description="Parameters for the map action (coordinates, sample details, route points)"
    )
    description: str = Field(
        ...,
        description="Human-readable summary of the triggered action"
    )


class ChatResponse(BaseModel):
    reply: str = Field(..., description="Capcom natural language response")
    action: Optional[CopilotAction] = Field(
        default=None,
        description="Optional structured action emitted to the map interface"
    )
    tools_called: Optional[List[str]] = Field(
        default=None,
        description="List of tool names invoked during the execution loop"
    )
