#!/usr/bin/env python3
"""Verify gateway auth and one read-only OpenRBus object across a reboot."""

from __future__ import annotations

import argparse
import asyncio
import re
from pathlib import Path
from typing import Any

from aioesphomeapi import APIClient

from openrbus.protocol.ble_segments import BleSegmentCodec, BleSegmentReassembler
from openrbus.protocol.canip import CanIpMessage, ObjectAddress, build_read, parse_read_response
from openrbus.protocol.selector import unwrap_canip, wrap_canip
from openrbus.registry import Registry
from openrbus.value_codec import decode_value

ADDRESS = ObjectAddress(0x2001, 0x02)
NODE = 0xFF


def _noise_key(yaml_path: Path, secrets_path: Path) -> str:
    contents = yaml_path.read_text(encoding="utf-8")
    direct = re.search(r"(?m)^\s*key:\s*[\"']?([^\"'\s#]+)", contents)
    if direct is None:
        raise RuntimeError("No ESPHome API encryption key found")
    value = direct.group(1)
    if value != "!secret":
        return value
    reference = re.search(r"(?m)^\s*key:\s*!secret\s+([^\s#]+)", contents)
    if reference is None:
        raise RuntimeError("No ESPHome API encryption-key reference found")
    secret_name = re.escape(reference.group(1))
    secret = re.search(
        rf"(?m)^\s*{secret_name}:\s*[\"']?([^\"'\s#]+)",
        secrets_path.read_text(encoding="utf-8"),
    )
    if secret is None:
        raise RuntimeError("Referenced API key is absent from secrets file")
    return secret.group(1)


async def _connect(host: str, port: int, key: str) -> APIClient:
    client = APIClient(host, port, noise_psk=key, client_info="openrbus-phase1a")
    await client.connect(login=True)
    return client


async def _read_once(client: APIClient, label: str) -> dict[str, Any]:
    entities, services = await client.list_entities_services()
    names = {entity.key: entity.object_id for entity in entities}
    action = next((item for item in services if item.name == "openrbus_gateway_auth"), None)
    if action is None:
        raise RuntimeError("openrbus_gateway_auth action is unavailable")

    raw: str | None = None
    status: str | None = None
    armed = False
    done = asyncio.Event()

    def on_state(state: Any) -> None:
        nonlocal raw, status
        object_id = (names.get(state.key) or "").replace("-", "_")
        if object_id == "openrbus_read_raw_response" and armed and state.state:
            raw = str(state.state)
        if object_id == "openrbus_pairing_status" and armed:
            status = str(state.state)
        if raw and status == "openrbus_read_received":
            done.set()

    client.subscribe_states(on_state)
    await asyncio.sleep(1)
    armed = True
    await client.execute_service(action, {"passkey": 0})
    await asyncio.wait_for(done.wait(), timeout=55)
    if raw is None:
        raise RuntimeError("read completed without a raw response")

    segment = bytes.fromhex(raw)
    message = BleSegmentReassembler().feed(segment)
    if message is None:
        raise RuntimeError("read response was not a final BLE segment")
    decoded_message = CanIpMessage.decode(unwrap_canip(message))
    response = parse_read_response(decoded_message, NODE, ADDRESS)
    registry = Registry.load_default()
    definition = registry.get(ADDRESS)
    value = decode_value(definition, ADDRESS, response.raw_value, registry=registry)
    request = BleSegmentReassembler().feed(
        BleSegmentCodec().encode(wrap_canip(build_read(NODE, ADDRESS).encode()))[0]
    )
    if request is None:
        raise AssertionError("known request did not reassemble")
    print(f"{label}: gateway_authenticated + read_received")
    print(f"{label}: request={wrap_canip(build_read(NODE, ADDRESS).encode()).hex()}")
    print(f"{label}: response_segment={segment.hex()}")
    print(f"{label}: object={ADDRESS} raw={response.raw_value.hex()} decoded={value}")
    return {"value": value, "raw": response.raw_value, "segment": segment}


async def _main(args: argparse.Namespace) -> None:
    key = _noise_key(args.yaml, args.secrets)
    first_client = await _connect(args.host, args.port, key)
    try:
        first = await _read_once(first_client, "before_reboot")
        _entities, services = await first_client.list_entities_services()
        reboot = next((item for item in services if item.name == "openrbus_reboot"), None)
        if reboot is None:
            raise RuntimeError("openrbus_reboot action is unavailable")
        await first_client.execute_service(reboot, {})
        await asyncio.sleep(2)
    finally:
        await first_client.disconnect(force=True)

    second_client: APIClient | None = None
    for _attempt in range(30):
        try:
            second_client = await _connect(args.host, args.port, key)
            break
        except Exception:
            await asyncio.sleep(2)
    if second_client is None:
        raise RuntimeError("device did not return after controlled reboot")
    try:
        second = await _read_once(second_client, "after_reboot")
    finally:
        await second_client.disconnect(force=True)
    if first["raw"] != second["raw"] or first["value"] != second["value"]:
        raise RuntimeError("post-reboot read differs from the first live result")
    print("PHASE_1A VERIFIED_LIVE")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="192.168.2.196")
    parser.add_argument("--port", type=int, default=6053)
    parser.add_argument("--yaml", type=Path, required=True)
    parser.add_argument("--secrets", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(_main(_arguments()))
