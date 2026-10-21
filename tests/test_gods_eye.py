import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mark.gods_eye_intent import handle_godseye_intent, parse_godseye_intent
from mark.gods_eye_view import (
    ALLOWED_TOOLS, GodseyeError, GodsEyeManager, _schema_check,
    _validate_api_base, supported_node_version,
)


class GodseyeIntentTests(unittest.TestCase):
    def test_explicit_globe_commands_are_recognized(self):
        self.assertEqual(parse_godseye_intent("MARK, abra o globo.").action, "show")
        self.assertEqual(parse_godseye_intent("Mostre Curitiba no globo").place, "Curitiba")
        self.assertEqual(parse_godseye_intent("Mostre a Terra").action, "show")
        intent = parse_godseye_intent("Mostre Curitiba no globo e os aviões")
        self.assertEqual(intent.place, "Curitiba")
        self.assertEqual(intent.layers, ("flights",))

    def test_stop_is_explicit_and_rotation_zoom_are_not_fabricated(self):
        self.assertEqual(parse_godseye_intent("Feche a visualização do globo").action, "stop")
        self.assertEqual(parse_godseye_intent("Gire o planeta").action, "unsupported")
        self.assertEqual(parse_godseye_intent("Aproxime a câmera").action, "unsupported")
        self.assertIsNone(parse_godseye_intent("Qual é o maior planeta?"))

    def test_visualization_opens_only_verified_local_url(self):
        manager = Mock()
        manager.start.return_value = "http://127.0.0.1:4173"
        manager.call_tool.return_value = {"structuredContent": {"url": "http://127.0.0.1:4173/#v=2"}}
        opened = Mock(return_value=True)
        reply = handle_godseye_intent("Abra o globo", manager, browser_open=opened)
        self.assertIn("127.0.0.1:4173", reply)
        opened.assert_called_once()
        args = manager.call_tool.call_args.args
        self.assertEqual(args[0], "show_in_gods_eye_view")

    def test_untrusted_non_loopback_visualizer_url_is_rejected(self):
        manager = Mock()
        manager.start.return_value = "http://127.0.0.1:4173"
        manager.call_tool.return_value = {"structuredContent": {"url": "https://example.com/"}}
        reply = handle_godseye_intent("Abra o globo", manager, browser_open=Mock())
        self.assertIn("endereço local verificável", reply)

    def test_visualizer_url_host_and_port_are_parsed_not_prefix_matched(self):
        manager = Mock()
        manager.start.return_value = "http://127.0.0.1:4173"
        manager.call_tool.return_value = {"structuredContent": {"url": "http://127.0.0.1:4173.evil.example/"}}
        reply = handle_godseye_intent("Abra o globo", manager, browser_open=Mock())
        self.assertIn("endereço local verificável", reply)


class GodseyeGuardTests(unittest.TestCase):
    def test_node_support_matches_upstream_runtime_line(self):
        for version in ("v24.14.0", "v24.19.0", "v26.0.0"):
            self.assertTrue(supported_node_version(version))
        for version in ("v22.23.3", "v24.13.9", "v25.0.0", "garbage"):
            self.assertFalse(supported_node_version(version))

    def test_mcp_tool_allowlist_rejects_arbitrary_tools(self):
        self.assertIn("show_in_gods_eye_view", ALLOWED_TOOLS)
        self.assertNotIn("panel_request", ALLOWED_TOOLS)
        self.assertNotIn("cctv_snapshot", ALLOWED_TOOLS)

    def test_mcp_argument_schema_rejects_unknown_or_out_of_range_values(self):
        schema = {"type": "object", "properties": {"lat": {"type": "number", "minimum": -90, "maximum": 90}},
                  "required": ["lat"], "additionalProperties": False}
        _schema_check(schema, {"lat": -25.4})
        for value in ({"lat": 91}, {"lat": True}, {"lat": 0, "shell": "id"}, {}):
            with self.assertRaises(ValueError):
                _schema_check(schema, value)

    def test_api_base_must_stay_on_loopback_and_permitted_ports(self):
        self.assertEqual(_validate_api_base("http://localhost:4173"), "http://127.0.0.1:4173")
        for address in ("https://127.0.0.1:4173", "http://0.0.0.0:4173", "http://10.0.0.2:4173",
                        "http://127.0.0.1:9999", "http://user@127.0.0.1:4173"):
            with self.assertRaises(GodseyeError, msg=address):
                _validate_api_base(address)


class GodseyeLifecycleTests(unittest.TestCase):
    class Process:
        pid = 54321

        def __init__(self):
            self.alive = True
            self.waits = []

        def poll(self):
            return None if self.alive else 0

        def wait(self, timeout=None):
            self.waits.append(timeout)
            self.alive = False
            return 0

    def test_start_is_loopback_only_and_stop_reaps_only_owned_process(self):
        process = self.Process()
        popen = Mock(return_value=process)
        manager = GodsEyeManager(home=Path("/unused"), node_lookup=lambda: ("/node24", "v24.19.0"), popen=popen)
        response = Mock(status=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch("mark.gods_eye_view._check_install"), \
             patch("mark.gods_eye_view._free_loopback_port", return_value=4174), \
             patch("mark.gods_eye_view.urllib.request.urlopen", return_value=response), \
             patch("mark.gods_eye_view.os.killpg") as killpg:
            address = manager.start()
            self.assertEqual(address, "http://127.0.0.1:4174")
            argv = popen.call_args.args[0]
            self.assertIn("127.0.0.1", argv)
            self.assertIn("--strictPort", argv)
            self.assertTrue(manager.running)
            manager.stop()
        killpg.assert_called_once_with(process.pid, 15)
        self.assertFalse(manager.running)
        self.assertIsNone(manager.api_base)


if __name__ == "__main__":
    unittest.main()
