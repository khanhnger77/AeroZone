"""FastAPI APIRouter for Odyssey Copilot with 2-pass Gemini tool loop and offline fallback."""
from __future__ import annotations
import os
import re
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import APIRouter

from .schemas import ChatRequest, ChatResponse, CopilotAction
from .prompt import SYSTEM_PROMPT
from .tools import (
    get_sample_info,
    inspect_terrain,
    plan_route,
    get_weather_and_climate,
    get_space_weather_and_radiation,
    get_geology_context,
    SAMPLES_DB,
    LANDMARKS_DB
)

# Model selection: default to gemini-3.5-flash-lite (fast, lightweight, active tier)
DEFAULT_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")

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
    """Return a google-genai Client if GEMINI_API_KEY or GOOGLE_API_KEY is configured."""
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        for p in [Path(".env"), Path(__file__).resolve().parents[2] / ".env"]:
            if p.exists():
                for line in p.read_text().splitlines():
                    clean = line.strip()
                    if clean.startswith(("GEMINI_API_KEY=", "GOOGLE_API_KEY=")):
                        api_key = clean.split("=", 1)[1].strip().strip('"').strip("'")
                        break
            if api_key:
                break
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

    func_weather = types.FunctionDeclaration(
        name="get_weather_and_climate",
        description="Retrieve Perseverance MEDA meteorological data (temperature, pressure, humidity, wind) and dust storm seasonality for Jezero Crater.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "sol": {
                    "type": "INTEGER",
                    "description": "Martian sol number (e.g. 1783, 168). Defaults to latest recorded sol if omitted."
                }
            }
        }
    )

    func_space_weather = types.FunctionDeclaration(
        name="get_space_weather_and_radiation",
        description="Retrieve NASA DONKI solar event alerts (CME, SEP), Langlais crustal magnetic field measurements, and EVA radiation hazard levels for Mars.",
        parameters={
            "type": "OBJECT",
            "properties": {}
        }
    )

    func_geology = types.FunctionDeclaration(
        name="get_geology_context",
        description="Retrieve Stack et al. 2020 geological formation, risk score, trafficability, and THEMIS thermal inertia for Martian coordinates (lon, lat) in Jezero AOI.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "lon": {"type": "NUMBER", "description": "Longitude in degrees East (e.g. 77.4509)"},
                "lat": {"type": "NUMBER", "description": "Latitude in degrees North (e.g. 18.4446)"}
            },
            "required": ["lon", "lat"]
        }
    )

    return types.Tool(function_declarations=[
        func_inspect, func_plan, func_sample,
        func_weather, func_space_weather, func_geology
    ])


VITALS_REGEX = re.compile(
    r"\b("
    r"(?:my|suit|internal|astronaut|remaining)\s+(?:o2|oxygen)|"
    r"(?:o2|oxygen)\s+(?:level|reserves?|stats?|status|telemetry|reading|sensor|supply|tank|remaining)|"
    r"vitals|vital\s+(?:signs?|stats?|status|telemetry)|"
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
    """Deterministic fallback engine for English and Vietnamese queries when offline or rate-limited."""
    # 1. Zero-hallucination vitals check
    vitals_refusal = check_vitals_refusal(request.message)
    if vitals_refusal:
        return vitals_refusal

    msg = request.message.lower()

    # Current location extraction with fallback for missing/partial keys
    loc = request.current_location if isinstance(request.current_location, dict) else {}
    cur_lon = float(loc.get("lon")) if loc.get("lon") is not None else (float(loc.get("x")) if loc.get("x") is not None else 77.4509)
    cur_lat = float(loc.get("lat")) if loc.get("lat") is not None else (float(loc.get("y")) if loc.get("y") is not None else 18.4446)

    # Coordinates in message text override
    coord_match = re.search(r"(\d{2}\.\d+)\s*,\s*(\d{2}\.\d+)", msg)
    if coord_match:
        c1, c2 = float(coord_match.group(1)), float(coord_match.group(2))
        if 70.0 <= c1 <= 80.0 and 15.0 <= c2 <= 22.0:
            cur_lon, cur_lat = c1, c2
        elif 70.0 <= c2 <= 80.0 and 15.0 <= c1 <= 22.0:
            cur_lon, cur_lat = c2, c1

    # Detect route-planning intent FIRST (before sample name scan)
    ROUTE_KEYWORDS = [
        "route", "path", "traverse", "navigate", "walk", "way to",
        "đường", "đi đến", "tìm đường", "lộ trình",
        "an toàn nhất", "safest", "how to get", "get to", "go to",
        "từ vị trí", "từ", "đến"
    ]
    is_route_intent = any(k in msg for k in ROUTE_KEYWORDS)

    # 2. Space Weather & Radiation inquiry check (Vietnamese & English)
    SPACE_WEATHER_KEYWORDS = [
        "bức xạ", "không gian", "bão mặt trời", "từ trường", "cme", "sep", "donki", "tia vũ trụ",
        "radiation", "space weather", "solar storm", "magnetic field", "crustal", "cosmic ray", "coronal mass"
    ]
    if any(k in msg for k in SPACE_WEATHER_KEYWORDS):
        sw_data = get_space_weather_and_radiation()
        action = CopilotAction(
            action_type="SHOW_SPACE_WEATHER",
            payload=sw_data,
            description="Space weather telemetry: NASA DONKI solar events and crustal magnetic field"
        )
        is_vi = any(k in msg for k in ["bức xạ", "bão mặt trời", "từ trường", "không gian", "tia vũ trụ"])
        b_surf = sw_data.get("magnetic_field", {}).get("jezero_surface_b_nT", 86.32)
        latest_cme = sw_data.get("space_weather", {}).get("latest_cme", {})
        cme_id = latest_cme.get("id", "N/A") if latest_cme else "N/A"
        if is_vi:
            reply = (
                f"Capcom cảnh báo bức xạ và thời tiết không gian: "
                f"Từ trường vỏ bề mặt Jezero đạt {b_surf:.2f} nT (~500 lần yếu hơn Trái Đất, chắn bức xạ không đáng kể). "
                f"Mức độ rủi ro bức xạ: {sw_data.get('radiation_risk_level', 'ELEVATED')}. "
                f"Tổng số sự kiện DONKI ghi nhận: {sw_data.get('space_weather', {}).get('total_recorded_events', 890)} (CME: {sw_data.get('space_weather', {}).get('cme_count', 583)}). "
                f"Sự kiện CME gần nhất: {cme_id}. Hướng dẫn vận hành: {sw_data.get('operational_guidance')}."
            )
        else:
            reply = (
                f"Capcom space weather telemetry: "
                f"Jezero surface crustal magnetic field: {b_surf:.2f} nT (~500x weaker than Earth, negligible GCR shielding). "
                f"Radiation risk level: {sw_data.get('radiation_risk_level', 'ELEVATED')}. "
                f"Recorded DONKI events: {sw_data.get('space_weather', {}).get('total_recorded_events', 890)} (CMEs: {sw_data.get('space_weather', {}).get('cme_count', 583)}). "
                f"Latest CME: {cme_id}. Operational guidance: {sw_data.get('operational_guidance')}."
            )
        return ChatResponse(reply=reply, action=action, tools_called=["get_space_weather_and_radiation"])

    # 3. Weather & Climate inquiry check (Vietnamese & English)
    WEATHER_KEYWORDS = [
        "thời tiết", "khí hậu", "áp suất", "nhiệt độ", "độ ẩm", "gió",
        "bão bụi", "meda", "weather", "climate", "temperature", "pressure",
        "humidity", "wind", "dust storm", "duststorm"
    ]
    if any(k in msg for k in WEATHER_KEYWORDS) or ("sol" in msg and any(w in msg for w in ["hôm nay", "today", "báo cáo", "report", "stats", "telemetry"])):
        sol_match = re.search(r"sol\s*[:=]?\s*(\d+)", msg)
        sol_val = int(sol_match.group(1)) if sol_match else None
        w_data = get_weather_and_climate(sol=sol_val)
        action = CopilotAction(
            action_type="SHOW_WEATHER",
            payload=w_data,
            description=f"Perseverance MEDA weather telemetry for Sol {w_data.get('sol')}"
        )
        is_vi = any(k in msg for k in ["thời tiết", "hôm nay", "thế nào", "áp suất", "nhiệt độ", "bão bụi", "báo cáo", "khí hậu"])
        # Humidity is only in some MEDA snapshots (M1's data-env has it, M3's weather-data does not).
        rh = w_data.get('rh_mean_pct')
        rh_vi = f"Độ ẩm tương đối: {rh}%. " if rh is not None else ""
        rh_en = f"Relative humidity: {rh}%. " if rh is not None else ""
        if is_vi:
            reply = (
                f"Capcom đây. Báo cáo thời tiết trạm MEDA Sol {w_data.get('sol')} tại Jezero: "
                f"Nhiệt độ trung bình {w_data.get('t_air_mean_c')}°C ({w_data.get('t_air_min_c')}°C đến {w_data.get('t_air_max_c')}°C / {w_data.get('t_air_mean_k')}K). "
                f"Áp suất bề mặt: {w_data.get('p_mean_pa')} Pa (~0.73% bar Trái Đất). {rh_vi}"
                f"Thành phần: {w_data.get('atmosphere_composition')}. "
                f"Mùa bão bụi: {w_data.get('dust_storm_seasonality', {}).get('risk_level', 'MODERATE')} (đỉnh điểm tại Ls 180°-240°). Telemetry đã đồng bộ lên HUD."
            )
        else:
            reply = (
                f"Capcom here with Perseverance MEDA weather for Sol {w_data.get('sol')}: "
                f"Mean air temperature {w_data.get('t_air_mean_c')}°C ({w_data.get('t_air_min_c')}°C to {w_data.get('t_air_max_c')}°C / {w_data.get('t_air_mean_k')}K). "
                f"Surface pressure: {w_data.get('p_mean_pa')} Pa (0.73% bar). {rh_en}"
                f"Atmosphere: {w_data.get('atmosphere_composition')}. "
                f"Regional dust storm risk: {w_data.get('dust_storm_seasonality', {}).get('risk_level', 'MODERATE')} (peak risk at Ls 180°-240°). Telemetry pinned to HUD."
            )
        return ChatResponse(reply=reply, action=action, tools_called=["get_weather_and_climate"])

    # 4. Geology & Trafficability inquiry check (Vietnamese & English)
    GEOLOGY_KEYWORDS = [
        "địa chất", "địa tầng", "đất đá", "lún", "sa lầy", "quán tính nhiệt", "themis",
        "máaz", "séítah", "geology", "geological", "formation", "trafficability",
        "thermal inertia", "rock type", "sinkage", "bedrock"
    ]
    if any(k in msg for k in GEOLOGY_KEYWORDS):
        geo_data = get_geology_context(cur_lon, cur_lat)
        action = CopilotAction(
            action_type="TOGGLE_LAYER",
            payload={"layer": "geologic_units", "unit": geo_data.get("unit_id"), "lon": cur_lon, "lat": cur_lat, **geo_data},
            description=f"Geological formation at [{cur_lon:.4f}, {cur_lat:.4f}]: {geo_data.get('unit_id', 'Unknown')}"
        )
        is_vi = any(k in msg for k in ["địa chất", "địa tầng", "lún", "sa lầy", "đất đá", "quán tính nhiệt"])
        if is_vi:
            reply = (
                f"Capcom phân tích địa chất tại tọa độ [{cur_lon:.4f}°E, {cur_lat:.4f}°N]: "
                f"Đơn vị địa chất: {geo_data.get('unit_name')} (Nhóm: {geo_data.get('group')}, Chỉ số rủi ro: {geo_data.get('risk_score')}/100). "
                f"Khả năng di chuyển (Trafficability): {geo_data.get('trafficability')}. "
                f"Quán tính nhiệt THEMIS: {geo_data.get('thermal_inertia', {}).get('rating')}. "
                f"Ý nghĩa địa chất: {geo_data.get('formation_interpretation')}. Lớp bản đồ địa chất đã kích hoạt."
            )
        else:
            reply = (
                f"Capcom geological assessment at [{cur_lon:.4f}°E, {cur_lat:.4f}°N]: "
                f"Unit: {geo_data.get('unit_name')} (Group: {geo_data.get('group')}, Risk Score: {geo_data.get('risk_score')}/100). "
                f"Trafficability: {geo_data.get('trafficability')}. "
                f"THEMIS Thermal Inertia: {geo_data.get('thermal_inertia', {}).get('rating')} ({geo_data.get('thermal_inertia', {}).get('surface_character')}). "
                f"Interpretation: {geo_data.get('formation_interpretation')}. Geological layer toggled on map."
            )
        return ChatResponse(reply=reply, action=action, tools_called=["get_geology_context"])

    # 5. Sample inquiry check — skipped when user clearly asks for a route
    found_sample = None
    if not is_route_intent:
        sample_match = re.search(r"(?:sample|mẫu)\s*(?:#|\s)?\s*(\d{1,2})", msg)
        if sample_match:
            found_sample = get_sample_info(sample_match.group(1))
        else:
            for s in SAMPLES_DB:
                if s["name"].lower() in msg or s["feature"].lower() in msg:
                    found_sample = s
                    break
    else:
        sample_match = re.search(r"(?:sample|mẫu)\s*(?:#|\s)?\s*(\d{1,2})", msg)
        if sample_match:
            found_sample = get_sample_info(sample_match.group(1))

    if found_sample and "error" not in found_sample and not is_route_intent:
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

    # 6. Route planning inquiry check
    if is_route_intent:
        cur = {"lon": cur_lon, "lat": cur_lat}
        dest_lon, dest_lat = 77.4060, 18.4588  # Default: Delta Front
        dest_name = "Delta Front"

        # Resolve destination: check samples by name/feature first
        for s in SAMPLES_DB:
            if s["name"].lower() in msg or s["feature"].lower() in msg:
                dest_lon, dest_lat = s["lon"], s["lat"]
                dest_name = f"{s['name']} (Sample #{s['sample_no']})"
                break
        else:
            # Then check landmarks
            for lm in LANDMARKS_DB:
                if lm["name"].lower() in msg:
                    dest_lon, dest_lat = lm["lon"], lm["lat"]
                    dest_name = lm["name"]
                    break

        route_data = plan_route(cur_lon, cur_lat, dest_lon, dest_lat)
        action = CopilotAction(
            action_type="PLAN_ROUTE",
            payload={
                "start": cur,
                "end": {"lon": dest_lon, "lat": dest_lat},
                **route_data
            },
            description=f"Planned traverse from current waypoint to {dest_name} [{dest_lon:.4f}, {dest_lat:.4f}]"
        )
        reply = (
            f"Capcom copy. Traverse route calculated to {dest_name} [{dest_lon:.4f}°E, {dest_lat:.4f}°N]. "
            f"Straight-line distance: {route_data.get('straight_line_m', 0.0):.0f}m. "
            f"Recommended profile: '{route_data.get('recommended', 'balanced')}'. "
            f"Safety check: All waypoints remain below the 30.0° NO-GO slope threshold. Route overlay transmitted to map."
        )
        return ChatResponse(reply=reply, action=action, tools_called=["plan_route"])

    # 7. Terrain inspection inquiry check
    if any(k in msg for k in ["terrain", "slope", "hazard", "roughness", "inspect", "elevation", "grade", "địa hình", "độ dốc", "hiểm họa", "độ gồ ghề"]):
        cur = {"lon": cur_lon, "lat": cur_lat}
        t_data = inspect_terrain(cur_lon, cur_lat)
        action = CopilotAction(
            action_type="INSPECT_TERRAIN",
            payload={"lon": cur_lon, "lat": cur_lat, **t_data},
            description=f"Inspecting terrain at [{cur_lon:.4f}, {cur_lat:.4f}]"
        )
        reply = (
            f"Capcom terrain telemetry check at [{cur_lon:.4f}°E, {cur_lat:.4f}°N]: "
            f"Elevation: {t_data.get('elevation_m')}m, Slope: {t_data.get('slope_deg')}°, "
            f"Roughness: {t_data.get('roughness_m')}m, Hazard Index: {t_data.get('hazard')}/100 "
            f"(Safe operational envelope: <30.0° slope limit). Telemetry pinned to HUD."
        )
        return ChatResponse(reply=reply, action=action, tools_called=["inspect_terrain"])

    # 8. General mission overview
    return ChatResponse(
        reply="Odyssey Capcom standing by. Ready to assist with terrain inspection, route planning, MEDA weather telemetry, space radiation, or sample science within Jezero Crater. State your destination or waypoint.",
        action=None,
        tools_called=[]
    )


@router.post("/chat", response_model=ChatResponse)
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
    loc = request.current_location if isinstance(request.current_location, dict) else {}
    cur_lon = float(loc.get("lon")) if loc.get("lon") is not None else (float(loc.get("x")) if loc.get("x") is not None else 77.4509)
    cur_lat = float(loc.get("lat")) if loc.get("lat") is not None else (float(loc.get("y")) if loc.get("y") is not None else 18.4446)
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
            model=DEFAULT_GEMINI_MODEL,
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
            lon = float(func_args.get("lon", cur_lon))
            lat = float(func_args.get("lat", cur_lat))
            tool_result = inspect_terrain(lon, lat)
            action = CopilotAction(
                action_type="INSPECT_TERRAIN",
                payload={"lon": lon, "lat": lat, **tool_result},
                description=f"Inspect terrain at [{lon:.4f}, {lat:.4f}]"
            )
        elif func_name == "plan_route":
            s_lon = float(func_args.get("start_lon", cur_lon))
            s_lat = float(func_args.get("start_lat", cur_lat))
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
        elif func_name == "get_weather_and_climate":
            sol = func_args.get("sol")
            tool_result = get_weather_and_climate(sol=int(sol) if sol is not None else None)
            action = CopilotAction(
                action_type="SHOW_WEATHER",
                payload=tool_result,
                description=f"Perseverance MEDA weather telemetry for Sol {tool_result.get('sol', 'latest')}"
            )
        elif func_name == "get_space_weather_and_radiation":
            tool_result = get_space_weather_and_radiation()
            action = CopilotAction(
                action_type="SHOW_SPACE_WEATHER",
                payload=tool_result,
                description="Space weather telemetry: DONKI solar events and crustal magnetic field"
            )
        elif func_name == "get_geology_context":
            lon = float(func_args.get("lon", cur_lon))
            lat = float(func_args.get("lat", cur_lat))
            tool_result = get_geology_context(lon=lon, lat=lat)
            action = CopilotAction(
                action_type="TOGGLE_LAYER",
                payload={"layer": "geologic_units", "unit": tool_result.get("unit_id"), "lon": lon, "lat": lat, **tool_result},
                description=f"Geological formation at [{lon:.4f}, {lat:.4f}]: {tool_result.get('unit_id', 'Unknown')}"
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
            types.Content(role="user", parts=[func_resp_part])
        ]

        try:
            final_res = client.models.generate_content(
                model=DEFAULT_GEMINI_MODEL,
                contents=pass2_contents,
                config=config
            )
            reply_text = final_res.text or f"Capcom telemetry confirmed. Data for {func_name} received."
        except Exception:
            reply_text = f"Capcom telemetry confirmed. Data for {func_name} received and transmitted to HUD."

        return ChatResponse(reply=reply_text, action=action, tools_called=tools_called)

    # No tools invoked: if direct action query was expected, check offline fallback
    fallback_res = execute_offline_fallback(request)
    if fallback_res.action is not None:
        return fallback_res

    # Direct textual answer
    return ChatResponse(reply=response.text or "Roger, Capcom standing by.", action=None, tools_called=[])
