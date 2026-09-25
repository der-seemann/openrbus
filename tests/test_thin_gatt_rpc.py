from __future__ import annotations

from pathlib import Path

import pytest

from tools.thin_gatt_rpc import (
    MAX_FRAME_BYTES,
    MAX_PAYLOAD_BYTES,
    RpcFrame,
    RpcProtocolError,
    RpcSimulator,
    b64,
)


def connect(sim: RpcSimulator) -> int:
    sim.submit(RpcFrame("request", "CONNECT", 0, 1, {"address": "device"}))
    frames = sim.drain()
    assert frames[0].status == "OK"
    event = frames[1]
    assert event.op == "CONNECTION_STATE"
    assert event.payload["state"] == "connected"
    return event.epoch


def test_envelope_round_trip_and_zero_length_write() -> None:
    request = RpcFrame(
        "request",
        "WRITE_CHAR",
        1,
        8,
        {"handle": 12, "value": "", "response": True},
    )
    assert RpcFrame.from_json(request.to_json()) == request
    sim = RpcSimulator()
    assert connect(sim) == 1
    sim.submit(request)
    sim.write_complete(8)
    response = sim.drain()[0]
    assert response.status == "OK"


def test_notification_can_arrive_before_write_callback_and_completes_once() -> None:
    sim = RpcSimulator()
    epoch = connect(sim)
    sim.submit(
        RpcFrame(
            "request",
            "WRITE_CHAR",
            epoch,
            7,
            {
                "handle": 99,
                "value": b64(b"request"),
                "expect_notification": True,
                "notification_handle": 100,
            },
        )
    )
    sim.notification(b"response", handle=100)
    events = sim.drain()
    assert len(events) == 1
    assert events[0].request_id == 7
    assert events[0].payload["value"] == b64(b"response")
    sim.write_complete(7)
    assert sim.drain() == []


def test_disconnect_fences_late_notification_and_reconnect_epoch() -> None:
    sim = RpcSimulator()
    epoch = connect(sim)
    sim.submit(
        RpcFrame(
            "request",
            "WRITE_CHAR",
            epoch,
            2,
            {"handle": 4, "value": "", "expect_notification": True},
        )
    )
    sim.disconnect()
    disconnected = sim.drain()
    assert disconnected[-1].payload["state"] == "disconnected"
    sim.notification(b"late", handle=4)
    # A notification after disconnect is dropped by the ESP host and cannot
    # complete request 2.
    assert sim.drain() == []
    sim.submit(RpcFrame("request", "CONNECT", 0, 3, {"address": "device"}))
    assert sim.drain()[0].epoch == epoch + 1


def test_cancel_and_limits() -> None:
    sim = RpcSimulator()
    epoch = connect(sim)
    sim.submit(RpcFrame("request", "WRITE_CHAR", epoch, 5, {"handle": 1, "value": ""}))
    sim.cancel(5)
    assert sim.drain()[-1].status == "CANCELLED"
    with pytest.raises(RpcProtocolError):
        b64(b"x" * (MAX_PAYLOAD_BYTES + 1))
    with pytest.raises(RpcProtocolError):
        RpcFrame("request", "WRITE_CHAR", epoch, 6, {"value": "x" * MAX_FRAME_BYTES}).to_json()


def test_canonical_esphome_scan_contract_is_present() -> None:
    root = Path(__file__).parents[1] / "tools" / "phase1a" / "esphome"
    header = (root / "openrbus_gatt_rpc.h").read_text()
    yaml = (root / "openrbus-ble-proxy.yaml").read_text()
    assert 'if (op == "SCAN")' in header
    assert '"SCAN_RESULT"' in header
    assert '"SCAN_DONE"' in header
    assert "duration_ms < 250" in header
    assert "MAX_SCAN_RESULTS = 8" in header
    assert 'source"] = "configured_target"' in header
    assert "this->parent_->address_str()" in header
    assert "this->parent_->get_remote_addr_type()" in header
    assert "scan_results_.empty()" in header
    assert "if (configured_target)" in header
    assert '"", -127' in header
    assert "response_session_independent" in header
    scan_branch = header.index('if (op == "SCAN")')
    identity_guard = header.index("if (request_gattc_if !=", scan_branch)
    assert scan_branch < identity_guard
    assert 'root["epoch"] = 0;' in header[header.index('"SCAN_RESULT"') :]
    assert "openrbus_gatt_rpc_request" in yaml
    assert "openrbus_gatt_rpc_poll" in yaml


def test_scan_cancel_routes_before_generic_cancel_without_regression() -> None:
    header = (
        Path(__file__).parents[1] / "tools" / "phase1a" / "esphome" / "openrbus_gatt_rpc.h"
    ).read_text()
    scan_cancel = header.index('if (op == "CANCEL" && this->scan_request_id_ != 0)')
    generic_cancel = header.index('if (op == "CANCEL")', scan_cancel)
    assert scan_cancel < generic_cancel
    assert (
        'this->response_session_independent(scan_id, "SCAN", "CANCELLED", "cancelled");' in header
    )
    assert "this->cancel_pending_emit_once();" in header[generic_cancel:]


def test_event_ring_overflow_is_explicit_and_bounded() -> None:
    sim = RpcSimulator()
    connect(sim)
    for _ in range(17):
        sim.notification(b"event")
    events = sim.drain()
    assert len(events) == 1
    assert events[0].op == "FLOW_CONTROL"
    assert events[0].payload["state"] == "overflow"
    assert sim.dropped_events == 1
