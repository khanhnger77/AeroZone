"""Standalone verification test suite for Odyssey Copilot."""
from __future__ import annotations
import asyncio
import os
import sys
from pathlib import Path
import unittest

# Ensure repository root is on sys.path whether executed directly or via -m unittest
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from odyssey_copilot.backend.schemas import (
    ChatRequest,
    ChatResponse,
    CopilotAction,
    LonLat,
    ChatMessage
)
from odyssey_copilot.backend.tools import (
    get_sample_info,
    inspect_terrain,
    plan_route,
    get_weather_and_climate,
    get_space_weather_and_radiation,
    get_geology_context,
    SAMPLES_DB,
    LANDMARKS_DB,
    execute_get_sample_info,
    execute_inspect_terrain,
    execute_plan_route,
    execute_get_weather_and_climate,
    execute_get_space_weather_and_radiation,
    execute_get_geology_context
)
from odyssey_copilot.backend.router import router, copilot_router, chat, execute_offline_fallback


class TestOdysseyCopilotSchemas(unittest.TestCase):
    """Test Pydantic schemas for Odyssey Copilot."""

    def test_chat_request_valid(self):
        req = ChatRequest(
            message="Check terrain hazard",
            history=[{"role": "user", "content": "Hello Capcom"}],
            current_location={"lon": 77.4509, "lat": 18.4446}
        )
        self.assertEqual(req.message, "Check terrain hazard")
        self.assertEqual(len(req.history), 1)
        self.assertEqual(req.current_location["lon"], 77.4509)

    def test_chat_request_minimal(self):
        req = ChatRequest(message="Status report")
        self.assertEqual(req.message, "Status report")
        self.assertIsNone(req.history)
        self.assertIsNone(req.current_location)

    def test_copilot_action(self):
        action = CopilotAction(
            action_type="FOCUS_SAMPLE",
            payload={"sample_no": 25, "name": "Sapphire Canyon"},
            description="Focusing on sample #25"
        )
        self.assertEqual(action.action_type, "FOCUS_SAMPLE")
        self.assertEqual(action.payload["sample_no"], 25)
        self.assertIn("sample #25", action.description)

    def test_copilot_action_data_platform_actions(self):
        """Test schemas for weather, space weather, and layer toggle actions."""
        act_weather = CopilotAction(
            action_type="SHOW_WEATHER",
            payload={"sol": 1783, "t_air_mean_c": -52.83},
            description="Perseverance MEDA Sol 1783"
        )
        self.assertEqual(act_weather.action_type, "SHOW_WEATHER")

        act_sw = CopilotAction(
            action_type="SHOW_SPACE_WEATHER",
            payload={"radiation_risk_level": "ELEVATED"},
            description="DONKI & crustal field"
        )
        self.assertEqual(act_sw.action_type, "SHOW_SPACE_WEATHER")

        act_layer = CopilotAction(
            action_type="TOGGLE_LAYER",
            payload={"layer": "geologic_units", "unit": "Undifferentiated smooth"},
            description="Geological formation"
        )
        self.assertEqual(act_layer.action_type, "TOGGLE_LAYER")

    def test_chat_response(self):
        action = CopilotAction(
            action_type="INSPECT_TERRAIN",
            payload={"elevation_m": -2569.9},
            description="Inspection complete"
        )
        resp = ChatResponse(
            reply="Capcom copy, slope within safe limits.",
            action=action,
            tools_called=["inspect_terrain"]
        )
        self.assertIn("Capcom copy", resp.reply)
        self.assertEqual(resp.action.action_type, "INSPECT_TERRAIN")
        self.assertEqual(resp.tools_called, ["inspect_terrain"])


class TestOdysseyCopilotTools(unittest.TestCase):
    """Test standalone tool functions in tools.py."""

    def test_catalog_size(self):
        """Confirm complete 25 Perseverance sample catalog."""
        self.assertEqual(len(SAMPLES_DB), 25)
        self.assertEqual(len(LANDMARKS_DB), 5)

    def test_get_sample_info_by_number(self):
        s25 = get_sample_info("25")
        self.assertEqual(s25["sample_no"], 25)
        self.assertEqual(s25["name"], "Sapphire Canyon")
        self.assertEqual(s25["feature"], "Cheyava Falls")
        self.assertAlmostEqual(s25["lon"], 77.30519, places=3)
        self.assertAlmostEqual(s25["lat"], 18.49748, places=3)

        s1 = get_sample_info(1)
        self.assertEqual(s1["sample_no"], 1)
        self.assertEqual(s1["name"], "Roubion")
        self.assertEqual(s1["type"], "Atmospheric")

    def test_get_sample_info_by_name_and_feature(self):
        s_cheyava = get_sample_info("Cheyava Falls")
        self.assertEqual(s_cheyava["sample_no"], 25)

        s_roubion = get_sample_info("Roubion")
        self.assertEqual(s_roubion["sample_no"], 1)

        s_unknown = get_sample_info("NonExistentCraterSample999")
        self.assertIn("error", s_unknown)

    def test_empty_sample_query_returns_error(self):
        """Empty or whitespace-only queries must return an error rather than sample #1."""
        for empty_q in ["", "   ", "\t", "\n\t\r", None]:
            res = get_sample_info(empty_q)
            self.assertIn("error", res)
            self.assertEqual(res.get("status"), "not_found")

    def test_inspect_terrain_in_bounds(self):
        res = inspect_terrain(77.4509, 18.4446)
        self.assertTrue(res.get("inside"))
        self.assertIn("elevation_m", res)
        self.assertIn("slope_deg", res)
        self.assertIn("hazard", res)

    def test_inspect_terrain_out_of_bounds(self):
        res = inspect_terrain(10.0, 10.0)
        self.assertFalse(res.get("inside"))

    def test_plan_route(self):
        # Butler Landing to Delta Front
        res = plan_route(77.4509, 18.4446, 77.4060, 18.4588)
        self.assertIn("straight_line_m", res)
        self.assertGreater(res["straight_line_m"], 0)
        self.assertIn("routes", res)
        self.assertTrue(len(res["routes"]) >= 1)
        self.assertIn("recommended", res)

    def test_get_weather_and_climate_default_latest(self):
        """Default call returns latest Sol (1783) and full meteorological + dust stats."""
        w = get_weather_and_climate()
        self.assertEqual(w["sol"], 1783)
        self.assertTrue(w["is_latest"])
        self.assertAlmostEqual(w["t_air_mean_k"], 220.32, places=2)
        self.assertAlmostEqual(w["t_air_mean_c"], -52.83, places=2)
        self.assertAlmostEqual(w["p_mean_pa"], 738.55, places=2)
        self.assertAlmostEqual(w["rh_mean_pct"], 5.071, places=2)
        self.assertIn("atmosphere_composition", w)
        self.assertIn("dust_storm_seasonality", w)
        self.assertEqual(w["dust_storm_seasonality"]["total_storms_1500km"], 168)
        self.assertIn("MODERATE", w["dust_storm_seasonality"]["risk_level"])

    def test_get_weather_and_climate_specific_sol(self):
        """Querying Sol 168 returns historical record including wind telemetry."""
        w168 = get_weather_and_climate(168)
        self.assertEqual(w168["sol"], 168)
        self.assertFalse(w168["is_latest"])
        self.assertEqual(w168.get("wind_mean_m_s"), 3.19)
        self.assertAlmostEqual(w168["t_air_mean_k"], 219.64, places=2)

    def test_get_weather_and_climate_nearest_sol(self):
        """Querying an unrecorded sol returns nearest sol with is_nearest annotation."""
        w_near = get_weather_and_climate(170)
        self.assertEqual(w_near["sol"], 168)
        self.assertTrue(w_near.get("is_nearest"))
        self.assertEqual(w_near.get("requested_sol"), 170)

    def test_get_space_weather_and_radiation(self):
        """Space weather tool returns crustal field data and DONKI event statistics."""
        sw = get_space_weather_and_radiation()
        self.assertIn("magnetic_field", sw)
        self.assertIn("space_weather", sw)
        self.assertAlmostEqual(sw["magnetic_field"]["jezero_surface_b_nT"], 86.32, places=2)
        self.assertAlmostEqual(sw["magnetic_field"]["jezero_altitude_200km_b_nT"], 5.50, places=2)
        self.assertEqual(sw["space_weather"]["total_recorded_events"], 890)
        self.assertEqual(sw["space_weather"]["cme_count"], 583)
        self.assertEqual(sw["space_weather"]["sep_count"], 218)
        self.assertIsNotNone(sw["space_weather"]["latest_cme"])
        self.assertEqual(sw["space_weather"]["latest_cme"]["id"], "2026-11-09T18:15:00-CME-001")
        self.assertEqual(sw["radiation_risk_level"], "ELEVATED")

    def test_get_geology_context_in_bounds(self):
        """Landing site (77.4509, 18.4446) resolves to Undifferentiated smooth bedrock."""
        geo = get_geology_context(77.4509, 18.4446)
        self.assertTrue(geo["inside_aoi"])
        self.assertEqual(geo["unit_code"], 1)
        self.assertEqual(geo["group"], "bedrock_smooth")
        self.assertEqual(geo["risk_score"], 10)
        self.assertEqual(geo["trafficability"], "EXCELLENT")
        self.assertEqual(geo["color"], "#c9b79c")
        self.assertIn("thermal_inertia", geo)

    def test_get_geology_context_out_of_bounds(self):
        """Coordinates far outside Jezero AOI return inside_aoi: False with error."""
        geo_out = get_geology_context(10.0, 10.0)
        self.assertFalse(geo_out["inside_aoi"])
        self.assertIn("outside", geo_out.get("error", "").lower())

    def test_tool_aliases(self):
        """Verify execute_* aliases match primary functions."""
        self.assertEqual(execute_get_sample_info("25")["sample_no"], 25)
        self.assertTrue(execute_inspect_terrain(77.45, 18.44)["inside"])
        self.assertGreater(execute_plan_route(77.45, 18.44, 77.40, 18.45)["straight_line_m"], 0)
        self.assertEqual(execute_get_weather_and_climate()["sol"], 1783)
        self.assertEqual(execute_get_space_weather_and_radiation()["space_weather"]["cme_count"], 583)
        self.assertTrue(execute_get_geology_context(77.4509, 18.4446)["inside_aoi"])


class TestOdysseyCopilotRouter(unittest.TestCase):
    """Test router endpoints, Gemini tool calling, and Capcom fallback behavior."""

    def test_zero_hallucination_vitals_refusal(self):
        """Assistant MUST refuse to invent astronaut suit vitals or telemetry."""
        queries = [
            "What is my current suit oxygen level and heart rate?",
            "Report my O2 consumption rate and battery life",
            "What is my suit pressure and biometric vitals?",
            "What is my O2 level?",
            "Verify suit O2 reserves",
            "Check my heart rate",
            "Report suit pressure",
        ]
        for q in queries:
            req = ChatRequest(message=q)
            res = asyncio.run(chat(req))
            self.assertIsInstance(res, ChatResponse)
            self.assertIn("telemetry offline", res.reply.lower())
            self.assertIsNone(res.action)
            self.assertEqual(res.tools_called, [])

    def test_co2_query_not_falsely_refused(self):
        """Atmospheric CO2 or adjective queries containing 'vital' must not trigger vitals refusal."""
        valid_queries = [
            "Is there CO2 in the Martian atmosphere?",
            "What is the CO2 concentration on Mars?",
            "This is a vital discovery for planetary science.",
            "What are the vital landmarks in Jezero?",
            "Is atmospheric CO2 useful for MOXIE oxygen generation?",
            "Tell me about vital geological landmarks in Jezero",
            "Can MOXIE produce oxygen from atmospheric CO2?",
        ]
        for q in valid_queries:
            req = ChatRequest(message=q)
            res = asyncio.run(chat(req))
            self.assertIsInstance(res, ChatResponse)
            self.assertNotIn("telemetry offline", res.reply.lower())

    def test_partial_current_location_no_crash(self):
        """Incomplete location dictionaries must not raise KeyError in offline fallback or chat."""
        partial_payloads = [
            {"lat": 18.44},
            {"lon": 77.45},
            {"x": 77.4509, "y": 18.4446},
            {}
        ]
        for loc in partial_payloads:
            req_terrain = ChatRequest(message="inspect terrain", current_location=loc)
            res_terrain = asyncio.run(chat(req_terrain))
            self.assertIsInstance(res_terrain, ChatResponse)
            self.assertIsNotNone(res_terrain.action)
            self.assertEqual(res_terrain.action.action_type, "INSPECT_TERRAIN")

            req_route = ChatRequest(message="plan route to Delta Front", current_location=loc)
            res_route = asyncio.run(chat(req_route))
            self.assertIsInstance(res_route, ChatResponse)
            self.assertIsNotNone(res_route.action)
            self.assertEqual(res_route.action.action_type, "PLAN_ROUTE")

    def test_chat_sample_query(self):
        req = ChatRequest(message="Give me the science details on Sample #25 Cheyava Falls.")
        res = asyncio.run(chat(req))
        self.assertIsInstance(res, ChatResponse)
        self.assertIsNotNone(res.action)
        self.assertEqual(res.action.action_type, "FOCUS_SAMPLE")
        self.assertEqual(res.action.payload.get("sample_no"), 25)
        self.assertIn("get_sample_info", res.tools_called)

    def test_chat_route_query(self):
        req = ChatRequest(
            message="Plan safe route from current position to Delta Front",
            current_location={"lon": 77.4509, "lat": 18.4446}
        )
        res = asyncio.run(chat(req))
        self.assertIsInstance(res, ChatResponse)
        self.assertIsNotNone(res.action)
        self.assertEqual(res.action.action_type, "PLAN_ROUTE")
        self.assertIn("plan_route", res.tools_called)

    def test_chat_terrain_query(self):
        req = ChatRequest(
            message="Inspect terrain slope and elevation at current position",
            current_location={"lon": 77.4509, "lat": 18.4446}
        )
        res = asyncio.run(chat(req))
        self.assertIsInstance(res, ChatResponse)
        self.assertIsNotNone(res.action)
        self.assertEqual(res.action.action_type, "INSPECT_TERRAIN")
        self.assertIn("inspect_terrain", res.tools_called)

    def test_offline_fallback_vietnamese_weather(self):
        """Vietnamese weather prompt triggers MEDA data and SHOW_WEATHER action."""
        req = ChatRequest(message="Thời tiết MEDA hôm nay thế nào?")
        res = execute_offline_fallback(req)
        self.assertIsInstance(res, ChatResponse)
        self.assertIsNotNone(res.action)
        self.assertEqual(res.action.action_type, "SHOW_WEATHER")
        self.assertEqual(res.action.payload.get("sol"), 1783)
        self.assertIn("thời tiết", res.reply.lower())
        self.assertIn("get_weather_and_climate", res.tools_called)

    def test_offline_fallback_vietnamese_space_weather(self):
        """Vietnamese space weather prompt triggers DONKI + crustal field telemetry."""
        req = ChatRequest(message="Kiểm tra mức độ bức xạ và bão mặt trời")
        res = execute_offline_fallback(req)
        self.assertIsInstance(res, ChatResponse)
        self.assertIsNotNone(res.action)
        self.assertEqual(res.action.action_type, "SHOW_SPACE_WEATHER")
        self.assertIn("bức xạ", res.reply.lower())
        self.assertIn("get_space_weather_and_radiation", res.tools_called)

    def test_offline_fallback_vietnamese_geology(self):
        """Vietnamese geology prompt triggers Stack 2020 units and TOGGLE_LAYER action."""
        req = ChatRequest(
            message="Đặc điểm địa chất và nguy cơ lún bánh xe tại đây",
            current_location={"lon": 77.4509, "lat": 18.4446}
        )
        res = execute_offline_fallback(req)
        self.assertIsInstance(res, ChatResponse)
        self.assertIsNotNone(res.action)
        self.assertEqual(res.action.action_type, "TOGGLE_LAYER")
        self.assertEqual(res.action.payload.get("unit_id"), "Undifferentiated smooth")
        self.assertIn("địa chất", res.reply.lower())
        self.assertIn("get_geology_context", res.tools_called)

    def test_offline_fallback_english_weather(self):
        """English weather prompt with sol number queries specific historical sol."""
        req = ChatRequest(message="What is the MEDA weather telemetry for Sol 168?")
        res = execute_offline_fallback(req)
        self.assertIsInstance(res, ChatResponse)
        self.assertIsNotNone(res.action)
        self.assertEqual(res.action.action_type, "SHOW_WEATHER")
        self.assertEqual(res.action.payload.get("sol"), 168)
        self.assertIn("get_weather_and_climate", res.tools_called)

    def test_offline_fallback_english_radiation(self):
        """English radiation inquiry returns crustal field and DONKI CME alerts."""
        req = ChatRequest(message="Check space weather radiation and cosmic ray dosage")
        res = execute_offline_fallback(req)
        self.assertIsInstance(res, ChatResponse)
        self.assertIsNotNone(res.action)
        self.assertEqual(res.action.action_type, "SHOW_SPACE_WEATHER")
        self.assertIn("get_space_weather_and_radiation", res.tools_called)

    def test_offline_fallback_english_geology(self):
        """English geology inquiry returns formation interpretation and TOGGLE_LAYER."""
        req = ChatRequest(
            message="Inspect geological formation and trafficability",
            current_location={"lon": 77.4509, "lat": 18.4446}
        )
        res = execute_offline_fallback(req)
        self.assertIsInstance(res, ChatResponse)
        self.assertIsNotNone(res.action)
        self.assertEqual(res.action.action_type, "TOGGLE_LAYER")
        self.assertIn("get_geology_context", res.tools_called)

    def test_chat_weather_action(self):
        """Full chat endpoint handles weather prompt and emits SHOW_WEATHER action."""
        req = ChatRequest(message="Thời tiết MEDA hôm nay thế nào?")
        res = asyncio.run(chat(req))
        self.assertIsInstance(res, ChatResponse)
        self.assertIsNotNone(res.action)
        self.assertEqual(res.action.action_type, "SHOW_WEATHER")
        self.assertEqual(res.action.payload.get("sol"), 1783)
        self.assertTrue(len(res.reply) > 0)

    def test_fastapi_test_client_integration(self):
        """Test router using FastAPI TestClient with HTTP request simulation."""
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient

            app = FastAPI()
            app.include_router(copilot_router, prefix="/api/copilot")
            client = TestClient(app)

            # Test sample analysis endpoint
            resp = client.post(
                "/api/copilot/chat",
                json={"message": "Analyze sample #25 Cheyava Falls"}
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("reply", data)
            self.assertEqual(data["action"]["action_type"], "FOCUS_SAMPLE")
            self.assertEqual(data["action"]["payload"]["sample_no"], 25)

            # Test weather query endpoint (Vietnamese)
            weather_resp = client.post(
                "/api/copilot/chat",
                json={"message": "Thời tiết MEDA hôm nay thế nào?"}
            )
            self.assertEqual(weather_resp.status_code, 200)
            w_data = weather_resp.json()
            self.assertIn("reply", w_data)
            self.assertGreater(len(w_data["reply"]), 0)
            self.assertEqual(w_data["action"]["action_type"], "SHOW_WEATHER")
            self.assertEqual(w_data["action"]["payload"]["sol"], 1783)

            # Test vitals refusal
            vitals_resp = client.post(
                "/api/copilot/chat",
                json={"message": "What is my O2 oxygen level?"}
            )
            self.assertEqual(vitals_resp.status_code, 200)
            v_data = vitals_resp.json()
            self.assertIn("telemetry offline", v_data["reply"].lower())
            self.assertIsNone(v_data.get("action"))

        except (ImportError, RuntimeError) as e:
            self.skipTest(f"TestClient skipped: {e}")


if __name__ == "__main__":
    unittest.main()
