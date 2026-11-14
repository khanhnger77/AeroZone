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


class SavedLocation(BaseModel):
    """A point the user saved on the map (e.g. HQ_1, Cargo_2)."""
    name: str
    lon: float
    lat: float
    type: Optional[str] = None
    description: Optional[str] = None


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="Astronaut query or Odyssey message")
    history: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Prior conversation turns [{'role': 'user'|'model', 'content': '...'}]"
    )
    current_location: Optional[Dict[str, float]] = Field(
        default=None,
        description="Current EVA coordinates {'lon': float, 'lat': float}"
    )
    locations: Optional[List[SavedLocation]] = Field(
        default=None,
        description="Locations the user saved on the map, so names like 'HQ_1' can be resolved"
    )


class CopilotAction(BaseModel):
    action_type: str = Field(
        ...,
        description=(
            "Type of map action: 'INSPECT_TERRAIN' | 'PLAN_ROUTE' | 'FOCUS_SAMPLE' | "
            "'NAVIGATE_WAYPOINT' | 'SHOW_WEATHER' | 'SHOW_SPACE_WEATHER' | 'TOGGLE_LAYER'"
        )
    )
    payload: Dict[str, Any] = Field(
        default_factory=dict,
        description="Parameters for the map action (coordinates, sample details, route points, weather, layers)"
    )
    description: str = Field(
        ...,
        description="Human-readable summary of the triggered action"
    )


class ChatResponse(BaseModel):
    reply: str = Field(..., description="Odyssey natural language response")
    action: Optional[CopilotAction] = Field(
        default=None,
        description="Optional structured action emitted to the map interface"
    )
    tools_called: Optional[List[str]] = Field(
        default=None,
        description="List of tool names invoked during the execution loop"
    )
