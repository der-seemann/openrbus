#pragma once

#include "esphome/components/ble_client/ble_client.h"
#include "esphome/core/log.h"

extern "C" void BTA_GATTC_WriteCharValue(uint16_t conn_id, uint16_t handle,
                                           uint8_t write_type, uint16_t len,
                                           uint8_t *value, uint8_t auth_req);

namespace openrbus_phase1a {

struct PendingEmptyWrite {
  uint16_t bta_conn_id{0};
  uint16_t handle{0};
  bool pending{false};
};

inline PendingEmptyWrite &pending_empty_write() {
  static PendingEmptyWrite request;
  return request;
}

inline void dispatch_empty_after_cccd(uint16_t descriptor_handle, uint16_t status) {
  auto &request = pending_empty_write();
  if (!request.pending || descriptor_handle != 0x0022 || status != 0)
    return;
  request.pending = false;
  ESP_LOGD("openrbus_att", "EMPTY_WRITE gate passed cccd=0x%04X", descriptor_handle);
  BTA_GATTC_WriteCharValue(request.bta_conn_id, request.handle, 2, 0, nullptr, 2);
}

inline void write_empty_with_response(esphome::ble_client::BLEClient *client,
                                      uint16_t handle) {
  // ESP-IDF's public esp_ble_gattc_write_char rejects value_len == 0 despite
  // ATT permitting it. The Bluedroid BTA primitive below is the same stack
  // path after that guard and explicitly supports a null zero-length value.
  // BTA expects BTC_GATT_CREATE_CONN_ID(gattc_if, conn_id), not ESPHome's
  // raw GAP connection ID. This matches esp_ble_gattc_write_char().
  const uint16_t conn_id = static_cast<uint16_t>(
      (static_cast<uint16_t>(static_cast<uint8_t>(client->get_conn_id())) << 8) |
      static_cast<uint8_t>(client->get_gattc_if()));
  ESP_LOGD("openrbus_att",
           "EMPTY_WRITE start gatt_if=%d conn_id=%u bta_conn_id=0x%04X handle=0x%04X len=0 type=RSP auth=MITM",
           client->get_gattc_if(), client->get_conn_id(), conn_id, handle);
  // Require the authenticated (MITM) link established by openrbus_pair.
  // `auth_req=2` is ESP_GATT_AUTH_REQ_MITM / BTA_GATT_AUTH_REQ_MITM.
  auto &request = pending_empty_write();
  request.bta_conn_id = conn_id;
  request.handle = handle;
  request.pending = true;
  ESP_LOGD("openrbus_att", "EMPTY_WRITE queued for CCCD completion handle=0x%04X", handle);
}

}  // namespace openrbus_phase1a
