import unittest
from unittest.mock import Mock

from mark.cognition.brain_routing import route_request, route_with_gods_eye_mcp
from mark.cognition.tools import ActionRequest, ToolRegistry
from mark.gods_eye_view import ALLOWED_TOOLS, GodseyeError
from mark.mcp_gods_eye import GODS_EYE_TOOL_SCHEMAS, GodsEyeMCPBridge, sanitize_mcp_output


class GodsEyeMCPTests(unittest.TestCase):
    def setUp(self):
        self.manager = Mock()
        self.manager.running = True
        self.manager.start.return_value = "http://127.0.0.1:4173"
        self.bridge = GodsEyeMCPBridge(manager=self.manager, api_base="http://127.0.0.1:4173")

    def test_mcp_tool_registration_and_json_schemas(self):
        tools = self.bridge.get_mcp_tools()
        names = {t["name"] for t in tools}
        self.assertEqual(names, set(ALLOWED_TOOLS))
        self.assertEqual(names, set(GODS_EYE_TOOL_SCHEMAS.keys()))
        for tool in tools:
            self.assertIn("description", tool)
            self.assertTrue(len(tool["description"]) > 10)
            schema = tool["inputSchema"]
            self.assertEqual(schema["type"], "object")
            self.assertIs(schema.get("additionalProperties"), False)

        openai_tools = self.bridge.get_openai_tools()
        self.assertEqual(len(openai_tools), len(ALLOWED_TOOLS))
        self.assertEqual(openai_tools[0]["type"], "function")
        self.assertIn("parameters", openai_tools[0]["function"])

    def test_parameter_validation_and_allowlist_enforcement(self):
        with self.assertRaises(PermissionError):
            self.bridge.validate_call("cctv_snapshot", {})
        with self.assertRaises(ValueError):
            self.bridge.validate_call("aircraft_in_area", {"lat": 120, "lon": 0})
        with self.assertRaises(ValueError):
            self.bridge.validate_call("aircraft_in_area", {"lat": -23.5, "lon": -46.6, "extra_field": "hack"})

        valid_args = self.bridge.validate_call("aircraft_in_area", {"lat": -23.5, "lon": -46.6, "radius_km": 150})
        self.assertEqual(valid_args["lat"], -23.5)

    def test_execute_tool_and_security_sanitization(self):
        self.manager.call_tool.return_value = {
            "content": [{"type": "text", "text": "Aircraft found; Bearer secret-token-123"}],
            "structuredContent": {"count": 2, "url": "http://127.0.0.1:4173/#v=1"},
        }
        result = self.bridge.execute_tool("aircraft_in_area", {"lat": -25.4, "lon": -49.2, "radius_km": 100})
        self.manager.call_tool.assert_called_once_with(
            "aircraft_in_area", {"lat": -25.4, "lon": -49.2, "radius_km": 100}, timeout=30
        )
        self.assertNotIn("secret-token-123", str(result))
        self.assertIn("[REDACTED]", result["content"][0]["text"])

        with self.assertRaises(GodseyeError):
            sanitize_mcp_output({"structuredContent": {"url": "https://evil.example.com/phish"}})

    def test_cognition_tool_registry_integration(self):
        self.manager.call_tool.return_value = {"events": [{"mag": 5.2, "place": "Chile"}]}
        registry = self.bridge.register_in_cognition_registry(ToolRegistry())
        contracts = {c["name"] for c in registry.contracts()}
        self.assertIn("gods_eye.get_earthquakes", contracts)
        self.assertIn("gods_eye.aircraft_in_area", contracts)

        response = registry.invoke(
            ActionRequest("gods_eye.get_earthquakes", {"min_magnitude": 5.0, "hours": 24}),
            grants=frozenset({"gods_eye.read"}),
        )
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["tool"], "get_earthquakes")
        self.assertEqual(response["data"]["events"][0]["mag"], 5.2)

    def test_router_triggers_gods_eye_mcp_on_spatial_or_osint_intent(self):
        self.manager.call_tool.return_value = {"events": [{"mag": 6.0}]}
        decision = route_request("Verifique os terremotos recentes na região andina", gods_eye_bridge=self.bridge)
        self.assertEqual(decision, "GODS_EYE_MCP_TRIGGERED")

        routed = route_with_gods_eye_mcp("Liste os lançamentos espaciais recentes", bridge=self.bridge)
        self.assertEqual(routed["route"], "GODS_EYE_MCP_TRIGGERED")
        self.assertEqual(routed["tool"], "get_recent_launches")
        self.assertEqual(routed["mode"], "osint")


if __name__ == "__main__":
    unittest.main()
