"""Offline safety checks for the ESPHome runtime transport bridge."""

from pathlib import Path
import unittest


ROOT = Path(__file__).parent / "esphome"
YAML = (ROOT / "heizungskeller-ble-proxy.yaml").read_text()
HEADER = (ROOT / "openrbus_transport.h").read_text()


class Phase2TransportStaticChecks(unittest.TestCase):
    def test_dynamic_session_defaults_off_and_is_not_restored(self) -> None:
        self.assertIn('id: openrbus_dynamic_session', YAML)
        self.assertIn('initial_value: "false"', YAML)
        self.assertNotIn('id: openrbus_dynamic_session\n    type: bool\n    restore_value: true', YAML)
        self.assertIn("set_enabled(true)", YAML)
        self.assertIn("set_enabled(false)", YAML)

    def test_runtime_path_is_not_ble_write_or_cccd_owner(self) -> None:
        self.assertNotIn("ble_client.ble_write", HEADER)
        self.assertNotIn("register_for_notify", HEADER)
        self.assertIn("esp_ble_gattc_write_char", HEADER)
        self.assertIn("register_ble_node(this)", HEADER)

    def test_input_and_disconnect_guards_exist(self) -> None:
        self.assertIn("request_id < 0", HEADER)
        self.assertIn("frame.size() > 128", HEADER)
        self.assertIn("bytes.size() > 64", HEADER)
        self.assertIn("MAX_RESPONSE_BYTES", HEADER)
        self.assertIn("this->enabled_", HEADER)
        self.assertIn("this->request_in_flight_ = false", HEADER)
        self.assertIn("this->write_completed_ = false", HEADER)
        self.assertIn("REQUEST_TIMEOUT_MS", HEADER)
        self.assertIn("this->transport_handle_ = 0", HEADER)
        self.assertIn("this->response_.clear()", HEADER)


if __name__ == "__main__":
    unittest.main()
