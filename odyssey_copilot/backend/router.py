"""FastAPI APIRouter for Odyssey Copilot with 2-pass Gemini tool loop and offline fallback."""
from __future__ import annotations
import os
import re
from typing import Optional, List, Dict, Any
from fastapi import APIRouter

from .schemas import ChatRequest, ChatResponse, CopilotAction
from .prompt import SYSTEM_PROMPT
from .tools import (
    get_sample_info,
    inspect_terrain,
    plan_route,
    SAMPLES_DB,
    LANDMARKS_DB
)

router = APIRouter(tags=["copilot"])
copilot_router = router

# Check for google-genai library availability
_genai_available = False
try:
    from google import genai
    from google.genai import types
    _genai_available = True
except ImportError:
    types = None
    genai = None


def get_gemini_client():
    """Return a google-genai Client if GEMINI_API_KEY is configured."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if _genai_available and api_key:
        try:
            return genai.Client(api_key=api_key)
        except Exception:
            return None
    return None


def build_gemini_tools():
    """Build types.Tool with function declarations for Gemini 2.0 Flash."""
    if not _genai_available or types is None:
        return None

    func_inspect = types.FunctionDeclaration(
        name="inspect_terrain",
        description="Inspect terrain elevation, slope, roughness, and hazard index at specific Jezero Crater coordinates (lon, lat)",
        parameters={
            "type": "OBJECT",
            "properties": {
                "lon": {"type": "NUMBER", "description": "Longitude in degrees East (e.g. 77.4509)"},
                "lat": {"type": "NUMBER", "description": "Latitude in degrees North (e.g. 18.4446)"}
            },
            "required": ["lon", "lat"]
        }
    )

    func_plan = types.FunctionDeclaration(
        name="plan_route",
        description="Plan safe least-cost traverse route between two points in Jezero Crater respecting the 30° NO-GO slope limit",
        parameters={
            "type": "OBJECT",
            "properties": {
                "start_lon": {"type": "NUMBER", "description": "Starting longitude"},
                "start_lat": {"type": "NUMBER", "description": "Starting latitude"},
                "end_lon": {"type": "NUMBER", "description": "Destination longitude"},
                "end_lat": {"type": "NUMBER", "description": "Destination latitude"}
            },
            "required": ["start_lon", "start_lat", "end_lon", "end_lat"]
        }
    )

    func_sample = types.FunctionDeclaration(
        name="get_sample_info",
        description="Retrieve scientific metadata and coordinates for any of the 25 Perseverance Jezero Crater samples by number (1-25) or name (e.g. 'Cheyava Falls')",
        parameters={
            "type": "OBJECT",
            "properties": {
                "sample_query_or_id": {
                    "type": "STRING",
                    "description": "Sample number (1-25) or feature/target name (e.g. 'Cheyava Falls', 'Sapphire Canyon')"
                }
            },
            "required": ["sample_query_or_id"]
        }
    )

    return types.Tool(function_declarations=[func_inspect, func_plan, func_sample])


VITALS_REGEX = re.compile(
    r"\b("
    r"o2|oxygen|"
    r"vitals|vital\s+(?:signs?|stats?|status|telemetry)|"
    r"vital(?!\s+(?:discovery|landmark|sample|mission|science|finding|clue|component|target|importance|area|traverse|route|observation|step|priority|role|task|detail|significance))|"
    r"heart[ -]?rate|pulse|blood[ -]?pressure|"
    r"body[ -]?temp(?:erature)?|biometrics?|"
    r"suit\s+pressure|respiration|life\s+support|battery\s+(?:level|life)"
    r")\b",
    re.IGNORECASE
)


def check_vitals_refusal(message: str = "", text: Optional[str] = None) -> Optional[ChatResponse]:
    """Strict zero-hallucination check: refuse to invent astronaut suit vitals or telemetry."""
    query = text if text is not None else message
    if not query:
        return None
    if VITALS_REGEX.search(query):
        return ChatResponse(
            reply="EVA suit telemetry offline / not equipped. Capcom does not monitor internal biometric telemetry. "
                  "Verify suit pressure and O2 reserves on your analog suit console.",
            action=None,
            tools_called=[]
        )
    return None


def execute_offline_fallback(request: ChatRequest) -> ChatResponse:
    """Deterministic fallback engine when GEMINI_API_KEY is not set or network is offline."""
    # 1. Zero-hallucination vitals check
    vitals_refusal = check_vitals_refusal(request.message)
    if vitals_refusal:
        return vitals_refusal

    msg = request.message.lower()

    # 2. Sample inquiry check
    sample_match = re.search(r"sample\s*(?:#|\s)?\s*(\d{1,2})", msg)
    found_sample = None
    if sample_match:
        found_sample = get_sample_info(sample_match.group(1))
    else:
        for s in SAMPLES_DB:
            if s["name"].lower() in msg or s["feature"].lower() in msg:
                found_sample = s
                break

    if found_sample and "error" not in found_sample:
        action = CopilotAction(
            action_type="FOCUS_SAMPLE",
            payload=found_sample,
            description=f"Focus on sample #{found_sample['sample_no']}: {found_sample['name']} at {found_sample['feature']}"
        )
        reply = (
            f"Capcom here. Sample #{found_sample['sample_no']} ({found_sample['name']}) "
            f"was sealed on Sol {found_sample['sol_sealed']} at feature '{found_sample['feature']}'. "
            f"Rock class: {found_sample['rock_class']} ({found_sample['type']}). "
            f"Coordinates: [{found_sample['lon']:.4f}°E, {found_sample['lat']:.4f}°N] at elevation {found_sample['elev_m']:.1f}m. "
            f"Geological region: {found_sample.get('geological_region', 'Jezero Crater')}. "
            f"Science summary: {found_sample.get('science_notes', '')} Target coordinates transmitted to map display."
        )
        return ChatResponse(reply=reply, action=action, tools_called=["get_sample_info"])

    # 3. Route planning inquiry check
    if any(k in msg for k in ["route", "path", "traverse", "plan", "navigate", "walk"]):
        loc = request.current_location if isinstance(request.current_location, dict) else {}
        cur_lon = float(loc.get("lon")) if loc.get("lon") is not None else 77.4509
        cur_lat = float(loc.get("lat")) if loc.get("lat") is not None else 18.4446
        cur = {"lon": cur_lon, "lat": cur_lat}
        dest_lon, dest_lat = 77.4060, 18.4588  # Default: Delta Front

        if "cheyava" in msg or "25" in msg:
            dest_lon, dest_lat = 77.3052, 18.4975
        elif "séítah" in msg or "seitah" in msg:
            dest_lon, dest_lat = 77.4440, 18.4320
        elif "upper fan" in msg:
            dest_lon, dest_lat = 77.3600, 18.4850
        elif "landing" in msg or "butler" in msg:
            dest_lon, dest_lat = 77.4509, 18.4446

        route_data = plan_route(cur.get("lon", 77.4509), cur.get("lat", 18.4446), dest_lon, dest_lat)
        action = CopilotAction(
            action_type="PLAN_ROUTE",
            payload={
                "start": cur,
                "end": {"lon": dest_lon, "lat": dest_lat},
                **route_data
            },
            description=f"Planned traverse from current waypoint to [{dest_lon:.4f}, {dest_lat:.4f}]"
        )
        reply = (
            f"Capcom copy. Traverse route calculated to [{dest_lon:.4f}°E, {dest_lat:.4f}°N]. "
            f"Straight-line distance: {route_data.get('straight_line_m', 0.0):.0f}m. "
            f"Recommended profile: '{route_data.get('recommended', 'balanced')}'. "
            f"Safety check: All waypoints remain below the 30.0° NO-GO slope threshold. Route overlay transmitted to map."
        )
        return ChatResponse(reply=reply, action=action, tools_called=["plan_route"])

    # 4. Terrain inspection inquiry check
    if any(k in msg for k in ["terrain", "slope", "hazard", "roughness", "inspect", "elevation", "grade"]):
        loc = request.current_location if isinstance(request.current_location, dict) else {}
        cur_lon = float(loc.get("lon")) if loc.get("lon") is not None else 77.4509
        cur_lat = float(loc.get("lat")) if loc.get("lat") is not None else 18.4446

        # Check if coordinates were supplied in message text
        coord_match = re.search(r"(\d{2}\.\d+)\s*,\s*(\d{2}\.\d+)", msg)
        if coord_match:
            c1, c2 = float(coord_match.group(1)), float(coord_match.group(2))
            if 70.0 <= c1 <= 80.0 and 15.0 <= c2 <= 22.0:
                cur_lon, cur_lat = c1, c2
            elif 70.0 <= c2 <= 80.0 and 15.0 <= c1 <= 22.0:
                cur_lon, cur_lat = c2, c1

        cur = {"lon": cur_lon, "lat": cur_lat}
        t_data = inspect_terrain(cur.get("lon", 77.4509), cur.get("lat", 18.4446))
        action = CopilotAction(
            action_type="INSPECT_TERRAIN",
            payload={"lon": cur.get("lon", 77.4509), "lat": cur.get("lat", 18.4446), **t_data},
            description=f"Inspecting terrain at [{cur.get('lon', 77.4509):.4f}, {cur.get('lat', 18.4446):.4f}]"
        )
        reply = (
            f"Capcom terrain telemetry check at [{cur.get('lon', 77.4509):.4f}°E, {cur.get('lat', 18.4446):.4f}°N]: "
            f"Elevation: {t_data.get('elevation_m')}m, Slope: {t_data.get('slope_deg')}°, "
            f"Roughness: {t_data.get('roughness_m')}m, Hazard Index: {t_data.get('hazard')}/100 "
            f"(Safe operational envelope: <30.0° slope limit). Telemetry pinned to HUD."
        )
        return ChatResponse(reply=reply, action=action, tools_called=["inspect_terrain"])

    # 5. General mission overview
    return ChatResponse(
        reply="Odyssey Capcom standing by. Ready to assist with terrain inspection, route planning, or sample data within Jezero Crater. State your destination or waypoint.",
        action=None,
        tools_called=[]
    )


@router.post("/chat", response_model=ChatResponse)
@router.post("/api/copilot/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Chat endpoint executing the 2-pass Gemini tool loop or offline fallback."""
    # Pre-check: Strict zero-hallucination vitals refusal
    vitals_refusal = check_vitals_refusal(request.message)
    if vitals_refusal:
        return vitals_refusal

    client = get_gemini_client()
    if not client:
        return execute_offline_fallback(request)

    # Pass 1: Build input contents for Gemini 2.0 Flash
    contents = []
    if request.history:
        for turn in request.history:
            role = "user" if turn.get("role") == "user" else "model"
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=turn.get("content", ""))]))

    user_prompt = request.message
    if request.current_location:
        loc = request.current_location if isinstance(request.current_location, dict) else {}
        cur_lon = loc.get("lon", 77.4509) if loc.get("lon") is not None else 77.4509
        cur_lat = loc.get("lat", 18.4446) if loc.get("lat") is not None else 18.4446
        user_prompt = f"[Astronaut EVA Coordinates: lon={cur_lon}, lat={cur_lat}]\n{user_prompt}"

    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_prompt)]))
    gemini_tools = [build_gemini_tools()]
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=gemini_tools,
        temperature=0.2
    )

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=contents,
            config=config
        )
    except Exception:
        # Fallback cleanly on network failure, quota limit, or invalid credentials
        return execute_offline_fallback(request)

    # Tool Execution Loop
    if response.function_calls:
        call = response.function_calls[0]
        func_name = call.name
        func_args = dict(call.args) if call.args else {}
        tools_called = [func_name]
        action = None
        tool_result: Dict[str, Any] = {}

        if func_name == "inspect_terrain":
            lon = float(func_args.get("lon", 77.4509))
            lat = float(func_args.get("lat", 18.4446))
            tool_result = inspect_terrain(lon, lat)
            action = CopilotAction(
                action_type="INSPECT_TERRAIN",
                payload={"lon": lon, "lat": lat, **tool_result},
                description=f"Inspect terrain at [{lon:.4f}, {lat:.4f}]"
            )
        elif func_name == "plan_route":
            s_lon = float(func_args.get("start_lon", 77.4509))
            s_lat = float(func_args.get("start_lat", 18.4446))
            e_lon = float(func_args.get("end_lon", 77.4060))
            e_lat = float(func_args.get("end_lat", 18.4588))
            tool_result = plan_route(s_lon, s_lat, e_lon, e_lat)
            action = CopilotAction(
                action_type="PLAN_ROUTE",
                payload={"start": {"lon": s_lon, "lat": s_lat}, "end": {"lon": e_lon, "lat": e_lat}, **tool_result},
                description=f"Route planned to [{e_lon:.4f}, {e_lat:.4f}]"
            )
        elif func_name == "get_sample_info":
            ident = func_args.get("sample_query_or_id") or func_args.get("sample_identifier") or func_args.get("sample_id", "")
            tool_result = get_sample_info(ident)
            action = CopilotAction(
                action_type="FOCUS_SAMPLE",
                payload=tool_result,
                description=f"Focus on sample #{tool_result.get('sample_no', 'N/A')}: {tool_result.get('name', 'N/A')}"
            )
        else:
            tool_result = {"error": f"Unknown tool: {func_name}"}

        # Pass 2: Return function response part to Gemini for synthesis
        func_resp_part = types.Part.from_function_response(
            name=func_name,
            response={"result": tool_result}
        )
        pass2_contents = [
            *contents,
            response.candidates[0].content,
            types.Content(role="tool", parts=[func_resp_part])
        ]

        try:
            final_res = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=pass2_contents,
                config=config
            )
            reply_text = final_res.text or f"Capcom telemetry confirmed. Data for {func_name} received."
        except Exception:
            reply_text = f"Capcom telemetry confirmed. Data for {func_name} received and transmitted to HUD."

        return ChatResponse(reply=reply_text, action=action, tools_called=tools_called)

    # No tools invoked: direct textual answer
    return ChatResponse(reply=response.text or "Roger, Capcom standing by.", action=None, tools_called=[])
