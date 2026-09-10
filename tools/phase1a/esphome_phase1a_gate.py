"""Deterministic ESPHome 2026.8.2 ATT ordering patch for the EHC gateway."""

# ruff: noqa: E501 -- exact generated-source anchors must remain byte-for-byte stable.

from __future__ import annotations

from pathlib import Path

SUPPORTED_ESPHOME_VERSION = "2026.8.2"


def _replace_once(path: Path, old: str, new: str, marker: str) -> None:
    text = path.read_text(encoding="utf-8")
    if marker in text:
        return
    if text.count(old) != 1:
        raise RuntimeError(f"Phase-1A upstream anchor mismatch in {path}: {marker}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def apply_gate(source_root: Path) -> None:
    """Patch a freshly generated ESPHome source tree exactly once."""

    header = source_root / "esphome/components/esp32_ble_client/ble_client_base.h"
    source = source_root / "esphome/components/esp32_ble_client/ble_client_base.cpp"
    if not header.is_file() or not source.is_file():
        raise RuntimeError(f"ESPHome esp32_ble_client sources missing below {source_root}")

    _replace_once(
        header,
        "  std::vector<BLEService *> services_;\n",
        "  std::vector<BLEService *> services_;\n"
        "  // OpenRBus: do not write notification CCCDs before MITM encryption.\n"
        "  std::vector<uint16_t> deferred_notify_handles_;\n",
        "deferred_notify_handles_",
    )
    _replace_once(
        source,
        '#include "ble_client_base.h"\n',
        '#include "ble_client_base.h"\n#include "openrbus_zero_write.h"\n',
        '#include "openrbus_zero_write.h"',
    )
    _replace_once(
        source,
        '      this->log_gattc_data_event_("WRITE_DESCR");\n',
        "      openrbus_phase1a::dispatch_empty_after_cccd(param->write.handle, param->write.status);\n"
        '      this->log_gattc_data_event_("WRITE_DESCR");\n',
        "dispatch_empty_after_cccd",
    )
    _replace_once(
        source,
        "      // The event carries no conn_id, so this is the only place the request can be retired.\n",
        "      if (!this->paired_) {\n"
        "        if (std::find(this->deferred_notify_handles_.begin(), this->deferred_notify_handles_.end(),\n"
        "                      param->reg_for_notify.handle) == this->deferred_notify_handles_.end()) {\n"
        "          this->deferred_notify_handles_.push_back(param->reg_for_notify.handle);\n"
        "        }\n"
        '        ESP_LOGD(TAG, "ATT_GATE deferring CCCD for 0x%04X until encryption", param->reg_for_notify.handle);\n'
        "        esp_ble_set_encryption(this->remote_bda_, ESP_BLE_SEC_ENCRYPT_MITM);\n"
        "        break;\n"
        "      }\n"
        "      // The event carries no conn_id, so this is the only place the request can be retired.\n",
        "ATT_GATE deferring CCCD",
    )
    _replace_once(
        source,
        "                 param->ble_security.auth_cmpl.addr_type, param->ble_security.auth_cmpl.auth_mode);\n",
        "                 param->ble_security.auth_cmpl.addr_type, param->ble_security.auth_cmpl.auth_mode);\n"
        "        for (const auto handle : this->deferred_notify_handles_)\n"
        "          esp_ble_gattc_register_for_notify(this->gattc_if_, this->remote_bda_, handle);\n"
        "        this->deferred_notify_handles_.clear();\n",
        "deferred_notify_handles_.clear",
    )

    generated = source.read_text(encoding="utf-8")
    required = (
        '#include "openrbus_zero_write.h"',
        "dispatch_empty_after_cccd",
        "ATT_GATE deferring CCCD",
        "deferred_notify_handles_.clear",
    )
    if any(marker not in generated for marker in required):
        raise RuntimeError("Phase-1A gate verification failed after patching")
