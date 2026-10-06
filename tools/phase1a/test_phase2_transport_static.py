"""Offline safety checks for the ESPHome runtime transport bridge."""

import unittest
from pathlib import Path

ROOT = Path(__file__).parent / "esphome"
YAML = (ROOT / "openrbus-ble-proxy.yaml").read_text()
HEADER = (ROOT / "openrbus_transport.h").read_text()


class Phase2TransportStaticChecks(unittest.TestCase):
    def test_dynamic_session_defaults_off_and_is_not_restored(self) -> None:
        self.assertIn("id: openrbus_dynamic_session", YAML)
        self.assertIn('initial_value: "false"', YAML)
        self.assertNotIn(
            "id: openrbus_dynamic_session\n    type: bool\n    restore_value: true", YAML
        )
        self.assertIn("set_enabled(true)", YAML)
        self.assertIn("set_enabled(false)", YAML)

    def test_runtime_path_is_not_ble_write_or_cccd_owner(self) -> None:
        self.assertNotIn("ble_client.ble_write", HEADER)
        self.assertNotIn("register_for_notify", HEADER)
        self.assertIn("esp_ble_gattc_write_char", HEADER)
        self.assertIn("register_ble_node(this)", HEADER)
        self.assertIn("response_handle_", HEADER)
        self.assertIn("param->notify.handle == this->response_handle_", HEADER)
        self.assertIn("response_uuid[]", HEADER)

    def test_input_and_disconnect_guards_exist(self) -> None:
        self.assertIn("request_id <= 0", HEADER)
        self.assertIn("frame.size() > 128", HEADER)
        self.assertIn("bytes.size() > 64", HEADER)
        self.assertIn("MAX_RESPONSE_BYTES", HEADER)
        self.assertIn("this->enabled_", HEADER)
        self.assertIn("this->request_in_flight_ = false", HEADER)
        self.assertIn("this->write_completed_ = false", HEADER)
        self.assertIn("REQUEST_TIMEOUT_MS", HEADER)
        self.assertIn("this->transport_handle_ = 0", HEADER)
        self.assertIn("this->response_.clear()", HEADER)

    def test_async_att_operations_have_a_bounded_timeout(self) -> None:
        rpc = (ROOT / "openrbus_gatt_rpc.h").read_text()
        self.assertIn("check_pending_timeout();", rpc)
        self.assertIn("ATT_TIMEOUT_MS", rpc)
        self.assertIn('this->response(request_id, op, "ERROR", "timeout")', rpc)

    def test_batch_poll_is_additive_fifo_and_response_bounded(self) -> None:
        rpc = (ROOT / "openrbus_gatt_rpc.h").read_text()
        self.assertIn("std::string poll_frame()", rpc)
        self.assertIn("std::vector<std::string> poll_frames()", rpc)
        self.assertIn("MAX_POLL_BATCH_FRAMES = 8", rpc)
        self.assertIn("MAX_POLL_BATCH_RESPONSE_BYTES = 16 * 1024", rpc)
        self.assertIn("json_string_encoded_size(next)", rpc)
        self.assertIn("this->events_.front()", rpc)
        self.assertIn("this->events_.pop_front()", rpc)
        self.assertIn("openrbus_gatt_rpc_poll_batch", YAML)
        self.assertIn('createNestedArray("frames")', YAML)

    def test_batch_poll_budget_is_inclusive_and_capped_at_eight(self) -> None:
        rpc = (ROOT / "openrbus_gatt_rpc.h").read_text()
        self.assertIn("frames.size() < MAX_POLL_BATCH_FRAMES", rpc)
        self.assertIn(">\n          MAX_POLL_BATCH_RESPONSE_BYTES", rpc)
        self.assertIn("response_bytes += comma_bytes + item_bytes", rpc)

        # Exercise the documented wire-size arithmetic at the exact boundary:
        # wrapper + N escaped strings + commas. The implementation's guard is
        # inclusive at 16 KiB and stops before consuming an over-budget item.
        limit = 16 * 1024
        wrapper_bytes = len('{"frames":[]}')
        base_frame_bytes, extra_bytes = divmod(limit - wrapper_bytes - 7, 8)
        sizes = [base_frame_bytes + 1] * extra_bytes + [base_frame_bytes] * (8 - extra_bytes)
        used = wrapper_bytes + sum(sizes) + len(sizes) - 1
        self.assertLessEqual(used, limit)
        self.assertEqual(len(sizes), 8)
        self.assertGreater(used + 1, limit)

    def test_rpc_does_not_cast_invalid_negative_ids_to_wire_ids(self) -> None:
        rpc = (ROOT / "openrbus_gatt_rpc.h").read_text()
        guard = "if (action_request_id <= 0)"
        self.assertIn(guard, rpc)
        self.assertLess(rpc.index(guard), rpc.index("static_cast<uint32_t>(action_request_id)"))

    def test_notification_handle_is_bounded_before_narrowing(self) -> None:
        rpc = (ROOT / "openrbus_gatt_rpc.h").read_text()
        self.assertIn("notification_handle > UINT16_MAX", rpc)
        self.assertIn('"invalid_handle"', rpc)
        self.assertIn("static_cast<uint16_t>(notification_handle)", rpc)

    def test_handle_refresh_is_discovery_guarded(self) -> None:
        self.assertIn("this->refresh_transport_handle_();", HEADER)
        self.assertIn("ClientState::ESTABLISHED", HEADER)
        self.assertIn("this->response_handle_ = 0", HEADER)

    def test_gateway_auth_arms_passkey_before_security_request(self) -> None:
        action = YAML[YAML.index("- action: openrbus_gateway_auth") :]
        self.assertIn("if (passkey < 0 || passkey > 999999)", action)
        self.assertIn("id(openrbus_pairing_passkey) = static_cast<uint32_t>(passkey);", action)
        self.assertIn("id(openrbus_pairing_armed) = true;", action)
        self.assertLess(
            action.index("openrbus_pairing_armed) = true"),
            action.index("ehc16_ble_client).connect"),
        )
        self.assertLess(
            action.index("openrbus_pairing_armed) = true"), action.index("ehc16_ble_client).pair()")
        )

    def test_pairing_can_recover_only_inactive_stale_terminal_state(self) -> None:
        action = YAML[YAML.index("- action: openrbus_pair") :]
        self.assertIn("const bool terminal_state", action)
        self.assertIn("!id(openrbus_pairing_armed) && terminal_state", action)
        self.assertIn("!id(ehc16_ble_client).connected()", action)

    def test_thin_rpc_fences_legacy_pairing_disconnect_loop(self) -> None:
        self.assertIn("id: openrbus_thin_rpc_active", YAML)
        self.assertIn("id(openrbus_thin_rpc_active) = true;", YAML)
        guard = "if (!id(openrbus_thin_rpc_active))"
        self.assertIn(guard, YAML)
        legacy_loop = YAML.index("const uint32_t now = millis();")
        self.assertLess(YAML.index(guard), legacy_loop)

    def test_dynamic_session_is_latched_only_after_authenticated_handles(self) -> None:
        action = YAML[YAML.index("- action: openrbus_enable_dynamic_transport") :]
        self.assertIn("openrbus_phase2::instance().authenticated()", action)
        self.assertIn("openrbus_phase2::instance().ready()", action)
        self.assertLess(
            action.index("id(openrbus_dynamic_session) = true;"),
            action.index('publish_state("dynamic_session_enabled")'),
        )
        self.assertIn('publish_state("dynamic_session_not_ready")', action)

    def test_positive_gateway_auth_notification_publishes_terminal_status(self) -> None:
        callback = YAML[YAML.index("id: openrbus_gateway_auth_notification") :]
        self.assertIn("id(openrbus_gateway_auth_ok) = x.size() == 1", callback)
        self.assertIn("id(openrbus_gateway_auth_ready) = true;", callback)
        terminal_status = (
            "publish_state(\n              id(openrbus_gateway_auth_ok) ? "
            '"gateway_authenticated" : "gateway_auth_failed")'
        )
        self.assertIn(terminal_status, callback)
        self.assertLess(
            callback.index("id(openrbus_gateway_auth_ok) = x.size() == 1"),
            callback.index('"gateway_authenticated"'),
        )


if __name__ == "__main__":
    unittest.main()
