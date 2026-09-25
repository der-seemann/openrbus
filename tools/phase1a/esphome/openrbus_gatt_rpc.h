#pragma once

// Opt-in, generic ATT/GATT control plane for the existing ESPHome BLEClient.
// This header intentionally contains no OpenRBus/CAN-IP/gateway knowledge.
// It is additive: the known-good openrbus_transport.h is not changed.

#include "esphome/components/api/custom_api_device.h"
#include "esphome/components/ble_client/ble_client.h"
#include "esphome/components/esp32_ble_tracker/esp32_ble_tracker.h"
#include "esphome/components/json/json_util.h"
#include "esphome/core/alloc_helpers.h"
#include "esphome/core/component.h"
#include "esphome/core/log.h"

#include <esp_gattc_api.h>
#include <esp_gap_ble_api.h>

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <deque>
#include <string>
#include <vector>

// ESP-IDF rejects a public GATTC zero-length write before reaching ATT.  This
// is the same proven Bluedroid primitive used by the Phase-1A gate, but the
// generic RPC adapter supplies no UUID, handle, or protocol semantics.
extern "C" void BTA_GATTC_WriteCharValue(uint16_t conn_id, uint16_t handle,
                                           uint8_t write_type, uint16_t len,
                                           uint8_t *value, uint8_t auth_req);

namespace openrbus_thin_gatt {

class Rpc final : public esphome::Component,
                  public esphome::ble_client::BLEClientNode,
                  public esphome::ble_device_base::ESPBTDeviceListener,
                  public esphome::api::CustomAPIDevice {
 public:
  // Stable source marker used by the opt-in compile harness.  Keep this
  // alongside the capability event so a generated artifact can be checked
  // without exposing the API encryption secret.
  static constexpr const char *CAPABILITY = "openrbus_thin_gatt.v1";
  static constexpr const char *STATUS_OK = "OK";
  static constexpr const char *STATUS_WRITE_FAILED = "WRITE_FAILED";
  static constexpr const char *STATUS_CANCELLED = "CANCELLED";
  static constexpr const char *STATUS_ERROR = "ERROR";
  static constexpr size_t MAX_PAYLOAD_BYTES = 512;
  static constexpr size_t MAX_FRAME_BYTES = 2048;
  static constexpr size_t MAX_EVENT_QUEUE = 16;
  static constexpr size_t MAX_KNOWN_HANDLES = 64;
  static constexpr uint32_t PAIR_TIMEOUT_MS = 10000;
  static constexpr uint32_t PAIR_DIAGNOSTIC_COUNTER_MAX = 65535;
  static constexpr uint32_t RPC_SCHEMA_VERSION = 3;
  static constexpr const char *PAIR_CONTRACT = "pair_terminal_v3";
  // GAP auth failure codes are copied as a bounded diagnostic byte.  0xff
  // is reserved for the boot/request state where no callback code exists.
  static constexpr uint8_t PAIR_AUTH_CODE_UNAVAILABLE = 0xff;

  // The caller must explicitly wire this component in a temporary/opt-in
  // YAML.  No global singleton or automatic BLE client is created here.
  void begin(esphome::ble_client::BLEClient *client) {
    if (this->parent_ != nullptr || client == nullptr)
      return;
    this->parent_ = client;
    client->register_ble_node(this);
    this->register_service(&Rpc::api_request, "openrbus_gatt_rpc",
                           {"request_id", "frame"});
    // SCAN is deliberately a consumer of the one ESPHome tracker stream;
    // it never starts a second scanner or BLE lifecycle.
    if (esphome::esp32_ble_tracker::global_esp32_ble_tracker != nullptr) {
      esphome::esp32_ble_tracker::global_esp32_ble_tracker->register_listener(
          static_cast<esphome::ble_device_base::ESPBTDeviceListener *>(this));
    }
    // CAPABILITY is a pre-bootstrap control marker, not part of the ordered
    // connection stream.  Keep all identity/epoch/sequence fields zero so a
    // fresh Native API client can consume it before a physical link exists.
    this->emit_capability();
    this->capability_pending_ = true;
    ESP_LOGI(TAG, "generic ATT/GATT RPC attached (opt-in)");
  }

  // Events are pulled through the response-capable API action below.  The
  // BLE GATTC readiness callback can run before BLEClient::connected() flips
  // true; retrying the guarded completion from the component loop closes
  // that ordering gap without weakening any link/identity checks.
  void loop() override {
    this->check_pair_timeout();
    this->check_pending_timeout();
    this->complete_connect_bootstrap();
    this->finish_scan_if_due();
  }

  bool parse_device(const esphome::ble_device_base::ESPBTDevice &device) override {
    if (this->scan_request_id_ == 0)
      return false;
    this->on_device(device);
    return true;
  }

  // Dequeue exactly one bounded envelope.  An empty string is the canonical
  // EMPTY result; the API action wraps it in response_data without exposing
  // an HA entity or changing the Native API protobuf.
  std::string poll_frame() {
    this->diagnostics_.poll_frame_calls++;
    this->diagnostics_.poll_queue_depth_before = this->events_.size();
    if (this->events_.empty()) {
      this->diagnostics_.poll_empty_returns++;
      this->diagnostics_.poll_queue_depth_after = 0;
      return {};
    }
    auto event = std::move(this->events_.front());
    this->events_.pop_front();
    this->diagnostics_.poll_nonempty_returns++;
    this->diagnostics_.poll_queue_depth_after = this->events_.size();
    if (this->capability_pending_ && event.find("\"op\":\"CAPABILITY\"") != std::string::npos)
      this->capability_pending_ = false;
    return event;
  }

  // Read-only, entity-free snapshot for diagnosing the bootstrap path.  The
  // schema intentionally contains only counters, booleans, bounded enums and
  // the current epoch; no peer identity, handles, payloads or credentials.
  std::string diagnostics_snapshot() const {
    return static_cast<std::string>(esphome::json::build_json([&](JsonObject root) {
      root["rpc_schema_version"] = RPC_SCHEMA_VERSION;
      root["pair_contract"] = PAIR_CONTRACT;
      root["connect_requests"] = this->diagnostics_.connect_requests;
      root["parent_connect_calls"] = this->diagnostics_.parent_connect_calls;
      root["connect_callbacks"] = this->diagnostics_.connect_callbacks;
      root["open_callbacks"] = this->diagnostics_.open_callbacks;
      root["search_callbacks"] = this->diagnostics_.search_callbacks;
      root["auth_callbacks"] = this->diagnostics_.auth_callbacks;
      root["disconnect_callbacks"] = this->diagnostics_.disconnect_callbacks;
      root["pending_bootstrap"] = this->pending_connect_request_id_ != 0;
      root["host_ready"] = this->ready_;
      root["parent_connected"] = this->parent_ != nullptr && this->parent_->connected();
      root["link_active"] = this->link_active_;
      root["epoch"] = this->epoch_;
      root["epoch_count"] = this->diagnostics_.epoch_count;
      root["bootstrap_frames_discarded"] = this->diagnostics_.bootstrap_frames_discarded;
      root["event_queue_depth"] = this->events_.size();
      root["poll_frame_calls"] = this->diagnostics_.poll_frame_calls;
      root["poll_nonempty_returns"] = this->diagnostics_.poll_nonempty_returns;
      root["poll_empty_returns"] = this->diagnostics_.poll_empty_returns;
      root["poll_queue_depth_before"] = this->diagnostics_.poll_queue_depth_before;
      root["poll_queue_depth_after"] = this->diagnostics_.poll_queue_depth_after;
      root["total_frames_enqueued"] = this->diagnostics_.total_frames_enqueued;
      root["connect_responses_enqueued"] = this->diagnostics_.connect_responses_enqueued;
      root["connected_state_enqueued"] = this->diagnostics_.connected_state_enqueued;
      root["pair_requests"] = this->diagnostics_.pair_requests;
      root["pair_already_secure_success"] = this->diagnostics_.pair_already_secure_success;
      root["pair_call_sync_ok"] = this->diagnostics_.pair_call_sync_ok;
      root["pair_call_sync_error"] = this->diagnostics_.pair_call_sync_error;
      root["pair_auth_callback_seen"] = this->diagnostics_.pair_auth_callback_seen;
      root["pair_auth_callback_success"] = this->diagnostics_.pair_auth_callback_success;
      root["pair_auth_callback_failure"] = this->diagnostics_.pair_auth_callback_failure;
      root["pair_callback_identity_mismatch"] =
          this->diagnostics_.pair_callback_identity_mismatch;
      root["pair_disconnect_abort"] = this->diagnostics_.pair_disconnect_abort;
      root["pair_watchdog_timeout"] = this->diagnostics_.pair_watchdog_timeout;
      root["pair_terminal_emitted_success"] =
          this->diagnostics_.pair_terminal_emitted_success;
      root["pair_terminal_emitted_error"] = this->diagnostics_.pair_terminal_emitted_error;
      root["pair_late_callback_ignored"] = this->diagnostics_.pair_late_callback_ignored;
      root["pair_last_status"] = this->diagnostics_.pair_last_status;
      // pair_terminal_status is the immutable cause of the most recently
      // emitted terminal pair result; late callbacks never overwrite it.
      root["pair_terminal_status"] = this->diagnostics_.pair_terminal_status;
      root["pair_last_auth_code"] = this->diagnostics_.pair_last_auth_code;
      root["last_enqueued_kind"] = this->diagnostics_.last_enqueued_kind;
      root["last_enqueued_op"] = this->diagnostics_.last_enqueued_op;
      root["last_callback_event"] = this->diagnostics_.last_event;
      root["last_callback_status"] = this->diagnostics_.last_status;
    }));
  }

  void api_request(int32_t action_request_id, std::string frame) {
    // Zero and negative IDs cannot be correlated. Do not cast a negative ID
    // to uint32_t and emit an apparently valid response for a phantom peer.
    if (action_request_id <= 0)
      return;
    if (frame.size() > MAX_FRAME_BYTES) {
      this->fatal_flow_control("frame_too_large");
      return;
    }

    JsonDocument document = esphome::json::parse_json(
        reinterpret_cast<const uint8_t *>(frame.data()), frame.size());
    JsonObject root = document.as<JsonObject>();
    if (root.isNull() || (root["v"] | 0) != 1 ||
        std::string(root["kind"] | "") != "request" ||
        (root["request_id"] | 0) != static_cast<uint32_t>(action_request_id)) {
      this->protocol_error(static_cast<uint32_t>(action_request_id), "invalid_payload");
      return;
    }

    JsonVariant op_value = root["op"];
    if (!op_value.is<const char *>() || std::string(op_value | "").empty()) {
      this->protocol_error(static_cast<uint32_t>(action_request_id), "invalid_payload");
      return;
    }
    const std::string op = op_value | "";
    const uint32_t request_id = static_cast<uint32_t>(action_request_id);
    if (op == "PAIR_ENCRYPT") {
      this->bump_pair_counter(this->diagnostics_.pair_requests);
      this->diagnostics_.pair_last_status = "requested";
      this->diagnostics_.pair_terminal_status = "none";
      this->diagnostics_.pair_last_auth_code = PAIR_AUTH_CODE_UNAVAILABLE;
    }
    const uint32_t epoch = root["epoch"] | 0;
    const uint32_t request_gattc_if = root["gattc_if"] | 0;
    const uint32_t request_conn_id = root["conn_id"] | 0;
    JsonObject payload = root["payload"].as<JsonObject>();

    if (op == "CONNECT")
      this->diagnostics_.connect_requests++;

    // Overflow is a fatal stream condition.  The peer must reconnect before
    // any operation (including writes) can be admitted again.
    if (this->desynchronized_ && op != "DISCONNECT" &&
        !(op == "CONNECT" && !this->link_active_)) {
      this->error(request_id, op.c_str(), "event_overflow");
      return;
    }

    // SCAN is deliberately session-independent.  It consumes the one
    // ESPHome tracker stream and therefore must be available before a target
    // GATT connection (and its epoch/interface/conn_id) is known.  Keep this
    // branch ahead of all connection identity and connected-state guards.
    if (op == "SCAN") {
      if (epoch != 0 || request_gattc_if != 0 || request_conn_id != 0) {
        this->response_session_independent(request_id, op.c_str(), "ERROR",
                                            "stale_epoch");
        return;
      }
      if (this->scan_request_id_ != 0) {
        this->response_session_independent(request_id, op.c_str(), "ERROR", "busy");
        return;
      }
      const uint32_t duration_ms = payload["duration_ms"] | 0;
      if (duration_ms < 250 || duration_ms > SCAN_MAX_DURATION_MS) {
        this->response_session_independent(request_id, op.c_str(), "ERROR", "invalid_payload");
        return;
      }
      this->scan_request_id_ = request_id;
      this->scan_deadline_ms_ = millis() + duration_ms;
      this->scan_results_.clear();
      this->response_session_independent(request_id, op.c_str(), "ACCEPTED");
      return;
    }
    if (op == "CANCEL" && this->scan_request_id_ != 0) {
      const uint32_t target = payload["request_id"] | 0;
      if (target == this->scan_request_id_) {
        if (epoch != 0 || request_gattc_if != 0 || request_conn_id != 0) {
          this->response_session_independent(request_id, op.c_str(), "ERROR",
                                              "stale_epoch");
          return;
        }
        const uint32_t scan_id = this->scan_request_id_;
        this->scan_request_id_ = 0;
        this->response_session_independent(scan_id, "SCAN", "CANCELLED", "cancelled");
        this->response_session_independent(request_id, op.c_str(), "OK");
        return;
      }
    }

    if (op == "CONNECT") {
      // CONNECT is the sole bootstrap request: caller epoch and callback
      // identity are intentionally ignored, but its epoch must be zero.
      if (epoch != 0) {
        this->error(request_id, op.c_str(), "stale_epoch");
        return;
      }
      if (this->pending_connect_request_id_ != 0) {
        this->error(request_id, op.c_str(), "busy");
        return;
      }
      // Every host bootstrap gets its own capability marker.  A config-entry
      // reload creates a fresh host-side session even when the physical BLE
      // link is already active, so the boot-only marker is insufficient.
      this->reset_bootstrap_queue();
      if (!this->parent_->connected()) {
        // CONNECT opens a new RPC bootstrap boundary. Retire envelopes from
        // an orphaned physical stream before invoking BLEClient.
        this->pending_connect_request_id_ = request_id;
        this->diagnostics_.parent_connect_calls++;
        this->parent_->connect();
        return;
      }
      if (!this->link_active_ || !this->ready_ ||
          this->current_gattc_if_ == ESP_GATT_IF_NONE) {
        // The host reports connected before OPEN/SEARCH completion.  Hold
        // the bootstrap until a current physical identity is established.
        this->pending_connect_request_id_ = request_id;
        return;
      }
      this->response(request_id, op.c_str(), "OK");
      this->emit_state("CONNECTION_STATE", "connected", request_id);
      return;
    }
    if (op == "CANCEL" && this->pending_connect_request_id_ != 0) {
      const uint32_t target = payload["request_id"] | 0;
      if (target == this->pending_connect_request_id_ && epoch == 0 &&
          request_gattc_if == 0 && request_conn_id == 0) {
        const uint32_t connect_id = this->pending_connect_request_id_;
        this->pending_connect_request_id_ = 0;
        this->response(connect_id, "CONNECT", "CANCELLED", "cancelled");
        this->response(request_id, op.c_str(), "OK");
        return;
      }
    }
    if (request_gattc_if != static_cast<uint32_t>(this->current_gattc_if_) ||
        request_conn_id != this->current_conn_id_) {
      this->error(request_id, op.c_str(), "stale_epoch");
      return;
    }
    if (epoch != this->epoch_) {
      this->error(request_id, op.c_str(), "stale_epoch");
      return;
    }
    if (op == "DISCONNECT") {
      this->record_pair_disconnect_abort();
      this->cancel_pending_emit_once("disconnect_abort");
      this->response(request_id, op.c_str(), "OK");
      this->parent_->disconnect();
      return;
    }
    if (!this->parent_->connected()) {
      this->error(request_id, op.c_str(), "not_connected");
      return;
    }

    if (op == "CANCEL") {
      const uint32_t target = payload["request_id"] | 0;
      if (target == 0 || target != this->pending_request_id_) {
        this->error(request_id, op.c_str(), "not_supported");
        return;
      }
      this->cancel_pending_emit_once();
      this->response(request_id, op.c_str(), "OK");
      return;
    }

    if (op == "PAIR_ENCRYPT") {
      if (this->pending_request_id_ != 0) {
        this->error(request_id, op.c_str(), "busy");
        return;
      }
      // BLEClientBase marks the current physical connection paired only from
      // its address-checked AUTH_CMPL callback.  Treat that state as proof for
      // an already-encrypted/bonded link and emit the same correlated terminal
      // pair as the asynchronous path.
      if (this->parent_->is_paired()) {
        this->bump_pair_counter(this->diagnostics_.pair_already_secure_success);
        this->encrypted_current_ = true;
        this->response(request_id, op.c_str(), "OK");
        this->emit_state("ENCRYPTION_STATE", "encrypted", request_id);
        this->record_pair_terminal(true, "already_secure_success");
        return;
      }
      // Arm before entering the host call so a synchronous security callback
      // cannot race an untracked request.
      this->arm_pending(request_id, PendingOp::PAIR);
      const esp_err_t status = this->parent_->pair();
      if (status != ESP_OK) {
        this->bump_pair_counter(this->diagnostics_.pair_call_sync_error);
        this->diagnostics_.pair_last_status = "pair_call_error";
        this->finish_pair(false, "security_required", "pair_call_error");
      } else {
        this->bump_pair_counter(this->diagnostics_.pair_call_sync_ok);
        this->diagnostics_.pair_last_status = "pair_call_ok";
        if (this->pending_pair_matches() && this->parent_->is_paired()) {
          // Some bonded links complete synchronously without a GAP callback.
          // The pending guard makes this mutually exclusive with a callback
          // that may already have retired the same request.
          this->finish_pair(true, nullptr, "auth_success");
        }
      }
      return;
    }
    if (op == "DISCOVER") {
      // A discovery request starts a new handle view even though ESPHome may
      // satisfy it from its cache.  Callers must perform fresh lookups before
      // issuing any ATT operation.
      this->clear_known_handles();
      this->response(request_id, op.c_str(), "OK");
      this->emit_state("CONNECTION_STATE", "discovered", request_id);
      return;
    }
    if (op == "HANDLE_LOOKUP") {
      if (!this->encrypted_current_) {
        this->error(request_id, op.c_str(), "security_required");
        return;
      }
      const std::string service = payload["service"] | "";
      const std::string characteristic = payload["characteristic"] | "";
      const std::string descriptor = payload["descriptor"] | "";
      auto service_uuid = esphome::esp32_ble_tracker::ESPBTUUID::from_raw(service);
      auto characteristic_uuid = esphome::esp32_ble_tracker::ESPBTUUID::from_raw(characteristic);
      uint16_t found_handle = 0;
      if (descriptor.empty()) {
        auto *found = this->parent_->get_characteristic(service_uuid, characteristic_uuid);
        if (found != nullptr)
          found_handle = found->handle;
      } else {
        auto *found = this->parent_->get_descriptor(
            service_uuid, characteristic_uuid,
            esphome::esp32_ble_tracker::ESPBTUUID::from_raw(descriptor));
        if (found != nullptr)
          found_handle = found->handle;
      }
      if (found_handle == 0) {
        this->error(request_id, op.c_str(), "invalid_handle");
        return;
      }
      const HandleKind kind = descriptor.empty() ? HandleKind::VALUE : HandleKind::DESCRIPTOR;
      if (!this->remember_handle(found_handle, kind)) {
        this->fatal_flow_control("handle_registry_full");
        return;
      }
      this->response(request_id, op.c_str(), "OK", nullptr, found_handle);
      return;
    }
    if (op == "SUBSCRIBE") {
      uint16_t value_handle = 0;
      uint16_t cccd_handle = 0;
      const bool enable = payload["enable"] | true;
      if (!this->u16(payload, "value_handle", value_handle) ||
          !this->u16(payload, "cccd_handle", cccd_handle) ||
          !this->known_handle(value_handle, HandleKind::VALUE) ||
          !this->known_handle(cccd_handle, HandleKind::DESCRIPTOR)) {
        this->error(request_id, op.c_str(), "invalid_handle");
        return;
      }
      if (!this->encrypted_current_ || this->pending_request_id_ != 0) {
        this->error(request_id, op.c_str(), !this->encrypted_current_ ? "security_required" : "busy");
        return;
      }
      // The BLEClient registration path is the sole CCCD owner for enable:
      // it registers and performs the encrypted descriptor write exactly
      // once.  Arm pending state before entering that path because the REG
      // callback can be delivered synchronously by test/fake hosts.
      this->arm_pending(request_id, PendingOp::SUBSCRIBE, value_handle, cccd_handle);
      if (enable) {
        const esp_err_t status = this->parent_->register_for_notify(value_handle);
        if (status != ESP_OK) {
          this->clear_pending_without_event();
          this->write_failed(request_id, "SUBSCRIBE");
        }
        return;
      }

      // Disable is intentionally the inverse: unregister first, then issue
      // exactly one 0x0000 CCCD write.  This direct write is not used by the
      // enable path and therefore cannot create a second CCCD owner.
      const esp_err_t unregister_status = esp_ble_gattc_unregister_for_notify(
          this->parent_->get_gattc_if(), this->current_peer_bda_, value_handle);
      if (unregister_status != ESP_OK) {
        this->clear_pending_without_event();
        this->write_failed(request_id, "SUBSCRIBE");
        return;
      }
      const uint16_t cccd_value = 0x0000;
      uint8_t disabled_cccd[2] = {
          static_cast<uint8_t>(cccd_value & 0xFF),
          static_cast<uint8_t>(cccd_value >> 8)};
      const esp_err_t cccd_status = esp_ble_gattc_write_char_descr(
          this->parent_->get_gattc_if(), this->parent_->get_conn_id(), cccd_handle,
          sizeof(disabled_cccd), disabled_cccd, ESP_GATT_WRITE_TYPE_RSP,
          ESP_GATT_AUTH_REQ_MITM);
      if (cccd_status != ESP_OK) {
        this->clear_pending_without_event();
        this->write_failed(request_id, "SUBSCRIBE");
      }
      return;
    }
    if (op == "WRITE_CHAR" || op == "WRITE_DESCRIPTOR") {
      uint16_t handle = 0;
      std::string encoded = payload["value"] | "";
      const HandleKind required_kind = op == "WRITE_CHAR" ? HandleKind::VALUE : HandleKind::DESCRIPTOR;
      if (!this->u16(payload, "handle", handle) ||
          !this->known_handle(handle, required_kind)) {
        this->error(request_id, op.c_str(), "invalid_handle");
        return;
      }
      if (encoded.size() > 700 || this->pending_request_id_ != 0) {
        this->error(request_id, op.c_str(), encoded.size() > 700 ? "invalid_payload" : "busy");
        return;
      }
      if (!this->encrypted_current_) {
        this->error(request_id, op.c_str(), "security_required");
        return;
      }
      const uint32_t notification_handle = payload["notification_handle"] | 0;
      if (notification_handle > UINT16_MAX) {
        this->error(request_id, op.c_str(), "invalid_handle");
        return;
      }
      std::vector<uint8_t> value;
      if (!encoded.empty())
        value = esphome::base64_decode(encoded);
      if (value.size() > MAX_PAYLOAD_BYTES || (!encoded.empty() && value.empty())) {
        this->error(request_id, op.c_str(), "invalid_payload");
        return;
      }
      if (op == "WRITE_DESCRIPTOR" && value.empty()) {
        // The public IDF descriptor API rejects a zero-length request before
        // ATT.  Empty writes are supported only for WRITE_CHAR via BTA.
        this->error(request_id, op.c_str(), "invalid_payload");
        return;
      }
      const bool response_write = payload["response"] | true;
      this->arm_pending(request_id, op == "WRITE_CHAR" ? PendingOp::CHAR : PendingOp::DESCRIPTOR,
                        handle);
      this->pending_expect_notification_ = payload["expect_notification"] | false;
      this->pending_notification_handle_ = static_cast<uint16_t>(notification_handle);

      esp_err_t status = ESP_OK;
      if (op == "WRITE_CHAR" && value.empty()) {
        // Keep ATT length zero.  A dummy pointer is intentionally not encoded
        // into the RPC frame; the Bluedroid primitive accepts nullptr here.
        BTA_GATTC_WriteCharValue(this->bta_conn_id(),
                                 handle, response_write ? ESP_GATT_WRITE_TYPE_RSP : ESP_GATT_WRITE_TYPE_NO_RSP,
                                 0, nullptr, ESP_GATT_AUTH_REQ_MITM);
      } else if (op == "WRITE_CHAR") {
        status = esp_ble_gattc_write_char(
            this->parent_->get_gattc_if(), this->parent_->get_conn_id(), handle,
            value.size(), value.data(),
            response_write ? ESP_GATT_WRITE_TYPE_RSP : ESP_GATT_WRITE_TYPE_NO_RSP,
            ESP_GATT_AUTH_REQ_MITM);
      } else {
        status = esp_ble_gattc_write_char_descr(
            this->parent_->get_gattc_if(), this->parent_->get_conn_id(), handle,
            value.size(), value.empty() ? nullptr : value.data(),
            response_write ? ESP_GATT_WRITE_TYPE_RSP : ESP_GATT_WRITE_TYPE_NO_RSP,
            ESP_GATT_AUTH_REQ_MITM);
      }
      if (status != ESP_OK) {
        this->clear_pending_without_event();
        this->write_failed(request_id, op.c_str());
      } else if (!response_write && !this->pending_expect_notification_) {
        // ATT Write Command has no completion callback.  Its successful host
        // submission is the terminal response; otherwise this request would
        // occupy the single-operation slot forever.
        this->clear_pending_without_event();
        this->response(request_id, op.c_str(), "OK");
      }
      return;
    }
    this->error(request_id, op.c_str(), "not_supported");
  }

  void gattc_event_handler(esp_gattc_cb_event_t event, esp_gatt_if_t gattc_if,
                           esp_ble_gattc_cb_param_t *param) override {
    if (param == nullptr)
      return;
    this->record_callback(event);
    switch (event) {
      case ESP_GATTC_CONNECT_EVT:
        // A CONNECT event is the only point at which the physical epoch and
        // callback identity are replaced.  Retire a stale request before
        // changing epoch so its cancellation carries the old identity.
        this->cancel_pending_emit_once("disconnect_abort");
        // A new physical connection starts a fresh pull stream.  Do not let
        // envelopes from the retired epoch cross the reconnect boundary.
        // Keep the terminal boundary from the old physical epoch.  The host
        // must consume it before the next bootstrap capability marker; if it
        // is dropped here, Python still considers the old session connected
        // and correctly rejects that marker as a duplicate.
        this->retain_disconnect_boundary();
        this->current_gattc_if_ = gattc_if;
        this->current_conn_id_ = param->connect.conn_id;
        this->clear_known_handles();
        std::memcpy(this->current_peer_bda_, param->connect.remote_bda,
                    sizeof(this->current_peer_bda_));
        this->epoch_ = this->epoch_ == UINT32_MAX ? 1 : this->epoch_ + 1;
        this->diagnostics_.epoch_count++;
        this->next_seq_ = 0;
        this->desynchronized_ = false;
        this->encrypted_current_ = false;
        this->ready_ = false;
        this->physical_disconnect_emitted_ = false;
        this->link_active_ = true;
        break;
      case ESP_GATTC_OPEN_EVT:
        if (!this->callback_matches(gattc_if, param->open.conn_id))
          break;
        if (!this->ready_) {
          this->ready_ = true;
          this->complete_connect_bootstrap();
        }
        break;
      case ESP_GATTC_SEARCH_CMPL_EVT:
        if (!this->callback_matches(gattc_if, param->search_cmpl.conn_id))
          break;
        this->clear_known_handles();
        if (!this->ready_) {
          this->ready_ = true;
          this->complete_connect_bootstrap();
        }
        break;
      case ESP_GATTC_REG_FOR_NOTIFY_EVT:
        // BLEClientBase owns CCCD writing and its encrypted-link gate.  This
        // event only confirms registration was admitted; completion is the
        // following WRITE_DESCR event.
        if (this->pending_op_ == PendingOp::SUBSCRIBE &&
            gattc_if == this->pending_gattc_if_ && this->pending_epoch_ == this->epoch_ &&
            this->pending_conn_id_ == this->current_conn_id_ &&
            param->reg_for_notify.handle == this->pending_handle_ &&
            param->reg_for_notify.status != ESP_GATT_OK) {
          const uint32_t request_id = this->pending_request_id_;
          this->clear_pending_without_event();
          this->write_failed(request_id, "SUBSCRIBE");
        }
        break;
      case ESP_GATTC_WRITE_DESCR_EVT:
        if (!this->pending_callback_matches(gattc_if, param->write.conn_id))
          break;
        if (this->pending_op_ == PendingOp::SUBSCRIBE &&
            this->pending_epoch_ == this->epoch_ &&
            param->write.handle == this->pending_cccd_handle_) {
          const uint32_t request_id = this->pending_request_id_;
          const bool ok = param->write.status == ESP_GATT_OK;
          this->clear_pending_without_event();
          ok ? this->response(request_id, "SUBSCRIBE", "OK")
             : this->write_failed(request_id, "SUBSCRIBE");
        } else if (this->pending_op_ == PendingOp::DESCRIPTOR &&
                   param->write.handle == this->pending_handle_) {
          this->finish_write(param->write.status == ESP_GATT_OK);
        }
        break;
      case ESP_GATTC_WRITE_CHAR_EVT:
        if (!this->pending_callback_matches(gattc_if, param->write.conn_id) ||
            this->pending_op_ != PendingOp::CHAR ||
            param->write.handle != this->pending_handle_)
          break;
        if (param->write.status != ESP_GATT_OK)
          this->finish_write(false);
        else if (!this->pending_expect_notification_)
          this->finish_write(true);
        break;
      case ESP_GATTC_NOTIFY_EVT:
        if (!this->callback_matches(gattc_if, param->notify.conn_id))
          break;
        if (param->notify.value_len > MAX_PAYLOAD_BYTES) {
          this->fatal_flow_control("payload_too_large");
          break;
        }
        if (this->pending_expect_notification_ &&
            (this->pending_notification_handle_ == 0 ||
             this->pending_notification_handle_ == param->notify.handle)) {
          const uint32_t request_id = this->pending_request_id_;
          this->clear_pending_without_event();
          this->emit_notification(param->notify.handle, param->notify.value,
                                  param->notify.value_len, request_id);
        } else {
          this->emit_notification(param->notify.handle, param->notify.value,
                                  param->notify.value_len, 0);
        }
        break;
      case ESP_GATTC_DISCONNECT_EVT:
        if (!this->callback_matches(gattc_if, param->disconnect.conn_id) ||
            this->physical_disconnect_emitted_)
          break;
        this->record_pair_disconnect_abort();
        this->cancel_pending_emit_once("disconnect_abort");
        this->link_active_ = false;
        this->ready_ = false;
        this->encrypted_current_ = false;
        this->physical_disconnect_emitted_ = true;
        this->clear_known_handles();
        this->fail_connect_bootstrap("not_connected");
        this->emit_state("CONNECTION_STATE", "disconnected", 0);
        this->current_conn_id_ = 0;
        break;
      case ESP_GATTC_CLOSE_EVT:
        if (!this->callback_matches(gattc_if, param->close.conn_id) ||
            this->physical_disconnect_emitted_)
          break;
        this->record_pair_disconnect_abort();
        this->cancel_pending_emit_once("disconnect_abort");
        this->link_active_ = false;
        this->ready_ = false;
        this->encrypted_current_ = false;
        this->physical_disconnect_emitted_ = true;
        this->clear_known_handles();
        this->fail_connect_bootstrap("not_connected");
        this->emit_state("CONNECTION_STATE", "disconnected", 0);
        this->current_conn_id_ = 0;
        break;
      default:
        break;
    }
  }

  void gap_event_handler(esp_gap_ble_cb_event_t event,
                         esp_ble_gap_cb_param_t *param) override {
    if (event != ESP_GAP_BLE_AUTH_CMPL_EVT || param == nullptr)
      return;
    const bool encrypted = param->ble_security.auth_cmpl.success;
    this->bump_pair_counter(this->diagnostics_.pair_auth_callback_seen);
    this->bump_pair_counter(encrypted ? this->diagnostics_.pair_auth_callback_success
                                      : this->diagnostics_.pair_auth_callback_failure);
    this->diagnostics_.pair_last_auth_code =
        param->ble_security.auth_cmpl.fail_reason;
    this->diagnostics_.auth_callbacks++;
    this->diagnostics_.last_event = "AUTH_CMPL";
    this->diagnostics_.last_status = encrypted ? "success" : "failed";
    // GAP auth is global and carries no gattc_if/conn_id.  Bind it to the
    // pending request's epoch/identity and the exact peer BDA captured at
    // CONNECT; an auth callback from another BLE client must never authorize
    // this one.  A mismatched callback fails the current request rather than
    // leaving it pending forever, while callbacks after retirement are ignored.
    if (!this->pending_pair_matches()) {
      this->bump_pair_counter(this->diagnostics_.pair_late_callback_ignored);
      this->diagnostics_.pair_last_status = "late_callback";
      return;
    }
    if (!this->auth_callback_matches(param->ble_security.auth_cmpl.bd_addr)) {
      this->bump_pair_counter(this->diagnostics_.pair_callback_identity_mismatch);
      this->diagnostics_.pair_last_status = "identity_mismatch";
      this->finish_pair(false, "identity_mismatch", "identity_mismatch");
      return;
    }
    this->diagnostics_.pair_last_status = encrypted ? "auth_success" : "auth_failure";
    this->finish_pair(encrypted, encrypted ? nullptr : "security_required",
                      encrypted ? "auth_success" : "auth_failure");
  }

 private:
  struct Diagnostics {
    uint32_t connect_requests{0};
    uint32_t parent_connect_calls{0};
    uint32_t connect_callbacks{0};
    uint32_t open_callbacks{0};
    uint32_t search_callbacks{0};
    uint32_t auth_callbacks{0};
    uint32_t disconnect_callbacks{0};
    uint32_t epoch_count{0};
    uint32_t bootstrap_frames_discarded{0};
    uint32_t poll_frame_calls{0};
    uint32_t poll_nonempty_returns{0};
    uint32_t poll_empty_returns{0};
    uint32_t poll_queue_depth_before{0};
    uint32_t poll_queue_depth_after{0};
    uint32_t total_frames_enqueued{0};
    uint32_t connect_responses_enqueued{0};
    uint32_t connected_state_enqueued{0};
    uint32_t pair_requests{0};
    uint32_t pair_already_secure_success{0};
    uint32_t pair_call_sync_ok{0};
    uint32_t pair_call_sync_error{0};
    uint32_t pair_auth_callback_seen{0};
    uint32_t pair_auth_callback_success{0};
    uint32_t pair_auth_callback_failure{0};
    uint32_t pair_callback_identity_mismatch{0};
    uint32_t pair_disconnect_abort{0};
    uint32_t pair_watchdog_timeout{0};
    uint32_t pair_terminal_emitted_success{0};
    uint32_t pair_terminal_emitted_error{0};
    uint32_t pair_late_callback_ignored{0};
    const char *last_enqueued_kind{"none"};
    const char *last_enqueued_op{"none"};
    const char *last_event{"none"};
    const char *last_status{"none"};
    const char *pair_last_status{"none"};
    const char *pair_terminal_status{"none"};
    uint8_t pair_last_auth_code{PAIR_AUTH_CODE_UNAVAILABLE};
  };

  static void bump_pair_counter(uint32_t &counter) {
    if (counter < PAIR_DIAGNOSTIC_COUNTER_MAX)
      counter++;
  }

  void record_callback(esp_gattc_cb_event_t event) {
    switch (event) {
      case ESP_GATTC_CONNECT_EVT:
        this->diagnostics_.connect_callbacks++;
        this->diagnostics_.last_event = "CONNECT_EVT";
        this->diagnostics_.last_status = "received";
        break;
      case ESP_GATTC_OPEN_EVT:
        this->diagnostics_.open_callbacks++;
        this->diagnostics_.last_event = "OPEN_EVT";
        this->diagnostics_.last_status = "received";
        break;
      case ESP_GATTC_SEARCH_CMPL_EVT:
        this->diagnostics_.search_callbacks++;
        this->diagnostics_.last_event = "SEARCH_CMPL_EVT";
        this->diagnostics_.last_status = "received";
        break;
      case ESP_GATTC_DISCONNECT_EVT:
      case ESP_GATTC_CLOSE_EVT:
        this->diagnostics_.disconnect_callbacks++;
        this->diagnostics_.last_event = event == ESP_GATTC_CLOSE_EVT ? "CLOSE_EVT" : "DISCONNECT_EVT";
        this->diagnostics_.last_status = "received";
        break;
      default:
        break;
    }
  }

  enum class HandleKind : uint8_t { VALUE, DESCRIPTOR };

  struct KnownHandle {
    uint16_t handle;
    esp_gatt_if_t gattc_if;
    uint16_t conn_id;
    uint32_t epoch;
    HandleKind kind;
  };

  enum class PendingOp : uint8_t { NONE, CHAR, DESCRIPTOR, SUBSCRIBE, PAIR };

  void record_pair_terminal(bool success, const char *terminal_status) {
    this->bump_pair_counter(success ? this->diagnostics_.pair_terminal_emitted_success
                                    : this->diagnostics_.pair_terminal_emitted_error);
    this->diagnostics_.pair_last_status = success ? "terminal_success" : "terminal_error";
    this->diagnostics_.pair_terminal_status = terminal_status;
  }

  void record_pair_disconnect_abort() {
    if (this->pending_op_ == PendingOp::PAIR && this->pending_request_id_ != 0) {
      this->bump_pair_counter(this->diagnostics_.pair_disconnect_abort);
      this->diagnostics_.pair_last_status = "disconnect_abort";
    }
  }

  bool u16(JsonObject payload, const char *key, uint16_t &result) const {
    const JsonVariant value = payload[key];
    if (!value.is<uint32_t>())
      return false;
    const uint32_t number = value.as<uint32_t>();
    if (number > UINT16_MAX)
      return false;
    result = static_cast<uint16_t>(number);
    return true;
  }

  bool callback_matches(esp_gatt_if_t gattc_if, uint16_t conn_id) const {
    return this->link_active_ && gattc_if == this->current_gattc_if_ &&
           conn_id == this->current_conn_id_;
  }

  bool pending_pair_matches() const {
    return this->pending_op_ == PendingOp::PAIR && this->pending_request_id_ != 0 &&
           this->pending_epoch_ == this->epoch_ &&
           this->pending_gattc_if_ == this->current_gattc_if_ &&
           this->pending_conn_id_ == this->current_conn_id_ &&
           this->callback_matches(this->current_gattc_if_, this->current_conn_id_) &&
           this->parent_ != nullptr && this->parent_->connected();
  }

  bool known_handle(uint16_t handle, HandleKind kind) const {
    for (const auto &known : this->known_handles_) {
      if (known.handle == handle && known.kind == kind &&
          known.gattc_if == this->current_gattc_if_ &&
          known.conn_id == this->current_conn_id_ && known.epoch == this->epoch_)
        return true;
    }
    return false;
  }

  bool remember_handle(uint16_t handle, HandleKind kind) {
    for (const auto &known : this->known_handles_) {
      if (known.handle == handle && known.kind == kind &&
          known.gattc_if == this->current_gattc_if_ &&
          known.conn_id == this->current_conn_id_ && known.epoch == this->epoch_)
        return true;
    }
    if (this->known_handles_.size() >= MAX_KNOWN_HANDLES)
      return false;
    this->known_handles_.push_back(
        {handle, this->current_gattc_if_, this->current_conn_id_, this->epoch_, kind});
    return true;
  }

  void clear_known_handles() { this->known_handles_.clear(); }

  void reset_bootstrap_queue() {
    const size_t before = this->events_.size();
    this->retain_disconnect_boundary();
    const size_t discarded = before - this->events_.size();
    this->diagnostics_.bootstrap_frames_discarded += static_cast<uint32_t>(discarded);
    this->emit_capability();
    this->capability_pending_ = true;
  }

  void retain_disconnect_boundary() {
    std::deque<std::string> preserved;
    for (auto &frame : this->events_) {
      if (frame.find("\"op\":\"CONNECTION_STATE\"") != std::string::npos &&
          frame.find("\"state\":\"disconnected\"") != std::string::npos)
        preserved.push_back(std::move(frame));
    }
    this->events_ = std::move(preserved);
  }

  void complete_connect_bootstrap() {
    // Completion is edge-triggered by the pending request.  OPEN/SEARCH and
    // the periodic loop may call this helper repeatedly after the link is
    // ready; without this gate each call would enqueue another connected
    // event, eventually overflowing the bounded event queue.
    if (this->pending_connect_request_id_ == 0)
      return;
    // OPEN/SEARCH callbacks are the readiness boundary.  Keep the guard in
    // the completion helper as well as at its call sites so an out-of-order
    // host callback can never acknowledge a link without current identity.
    if (!this->link_active_ || !this->ready_ ||
        this->current_gattc_if_ == ESP_GATT_IF_NONE ||
        this->parent_ == nullptr ||
        !this->parent_->connected())
      return;
    const uint32_t request_id = this->pending_connect_request_id_;
    this->pending_connect_request_id_ = 0;
    if (request_id != 0)
      this->response(request_id, "CONNECT", "OK");
    this->emit_state("CONNECTION_STATE", "connected", request_id);
  }

  void check_pair_timeout() {
    if (this->pending_op_ != PendingOp::PAIR || this->pending_request_id_ == 0 ||
        static_cast<uint32_t>(millis() - this->pending_started_ms_) < PAIR_TIMEOUT_MS)
      return;
    this->bump_pair_counter(this->diagnostics_.pair_watchdog_timeout);
    this->diagnostics_.pair_last_status = "watchdog_timeout";
    this->finish_pair(false, "timeout", "watchdog_timeout");
  }

  void check_pending_timeout() {
    if (this->pending_op_ == PendingOp::NONE || this->pending_op_ == PendingOp::PAIR ||
        this->pending_request_id_ == 0 ||
        static_cast<uint32_t>(millis() - this->pending_started_ms_) < ATT_TIMEOUT_MS)
      return;
    const uint32_t request_id = this->pending_request_id_;
    const char *op = this->pending_op_ == PendingOp::SUBSCRIBE
                         ? "SUBSCRIBE"
                         : (this->pending_op_ == PendingOp::DESCRIPTOR
                                ? "WRITE_DESCRIPTOR"
                                : "WRITE_CHAR");
    this->clear_pending_without_event();
    this->response(request_id, op, "ERROR", "timeout");
  }

  static constexpr uint32_t SCAN_MAX_DURATION_MS = 10000;
  static constexpr size_t MAX_SCAN_RESULTS = 8;

  void on_device(const esphome::ble_device_base::ESPBTDevice &device) {
    const uint64_t address_value = device.address_uint64();
    if (this->scan_request_id_ == 0 || address_value == 0 || this->scan_results_.size() >= MAX_SCAN_RESULTS)
      return;
    for (const auto &seen : this->scan_results_)
      if (seen.address == address_value)
        return;

    char address[18]{};
    for (int i = 0; i < 6; i++) {
      const uint8_t octet = device.address()[i];
      snprintf(address + i * 3, sizeof(address) - i * 3, "%02X%s", octet, i == 5 ? "" : ":");
    }
    this->enqueue_scan_result(address_value, address, device.get_name().c_str(), device.get_rssi(),
                              static_cast<uint8_t>(device.get_address_type()), false);
  }

  void enqueue_scan_result(uint64_t address_value, const char *address, const char *name,
                           int16_t rssi, uint8_t address_type, bool configured_target) {
    if (this->scan_request_id_ == 0 || address_value == 0 ||
        this->scan_results_.size() >= MAX_SCAN_RESULTS)
      return;
    for (const auto &seen : this->scan_results_)
      if (seen.address == address_value)
        return;
    this->scan_results_.push_back({address_value});
    this->next_seq_++;
    this->enqueue(esphome::json::build_json([&](JsonObject root) {
      root["v"] = 1;
      root["kind"] = "event";
      root["op"] = "SCAN_RESULT";
      // Tracker advertisements are independent of any target GATT session.
      root["epoch"] = 0;
      root["request_id"] = this->scan_request_id_;
      root["gattc_if"] = 0;
      root["conn_id"] = 0;
      root["seq"] = this->next_seq_;
      root["payload"]["address"] = address;
      root["payload"]["name"] = name;
      root["payload"]["rssi"] = rssi;
      root["payload"]["address_type"] = address_type;
      if (configured_target)
        root["payload"]["source"] = "configured_target";
    }));
  }

  void finish_scan_if_due() {
    if (this->scan_request_id_ == 0 || static_cast<int32_t>(millis() - this->scan_deadline_ms_) < 0)
      return;
    const uint32_t request_id = this->scan_request_id_;
    // A configured ESPHome BLEClient target is a real, connectable device even
    // when it is bonded, quiet, or outside the tracker's advertisement window.
    // Surface it as a bounded fallback so HA can still offer a genuine target
    // choice; mark the source explicitly instead of fabricating RSSI evidence.
    if (this->scan_results_.empty() && this->parent_ != nullptr &&
        this->parent_->get_address() != 0) {
      this->enqueue_scan_result(
          this->parent_->get_address(), this->parent_->address_str(),
          "", -127,
          static_cast<uint8_t>(this->parent_->get_remote_addr_type()), true);
    }
    const uint16_t count = static_cast<uint16_t>(this->scan_results_.size());
    this->scan_request_id_ = 0;
    this->next_seq_++;
    this->enqueue(esphome::json::build_json([&](JsonObject root) {
      root["v"] = 1;
      root["kind"] = "event";
      root["op"] = "SCAN_DONE";
      root["epoch"] = 0;
      root["request_id"] = request_id;
      root["gattc_if"] = 0;
      root["conn_id"] = 0;
      root["seq"] = this->next_seq_;
      root["payload"]["count"] = count;
    }));
  }

  void fail_connect_bootstrap(const char *code) {
    const uint32_t request_id = this->pending_connect_request_id_;
    this->pending_connect_request_id_ = 0;
    if (request_id != 0)
      this->response(request_id, "CONNECT", "ERROR", code);
  }

  bool auth_callback_matches(const uint8_t *peer_bda) const {
    if (!this->link_active_ || this->current_gattc_if_ == ESP_GATT_IF_NONE ||
        peer_bda == nullptr || this->parent_ == nullptr ||
        !this->parent_->connected())
      return false;
    // get_* is read from the same BLEClient whose GAP callback this adapter
    // owns; checking it here supplies the missing gattc_if/conn_id provenance.
    if (this->parent_->get_gattc_if() != this->current_gattc_if_ ||
        this->parent_->get_conn_id() != this->current_conn_id_)
      return false;
    return std::memcmp(peer_bda, this->current_peer_bda_,
                       sizeof(this->current_peer_bda_)) == 0;
  }

  void arm_pending(uint32_t request_id, PendingOp operation, uint16_t handle = 0,
                   uint16_t cccd_handle = 0) {
    this->pending_request_id_ = request_id;
    this->pending_op_ = operation;
    this->pending_epoch_ = this->epoch_;
    this->pending_gattc_if_ = this->current_gattc_if_;
    this->pending_conn_id_ = this->current_conn_id_;
    this->pending_handle_ = handle;
    this->pending_cccd_handle_ = cccd_handle;
    // Every asynchronous ATT operation needs a deadline. Without this, a
    // lost write/CCCD/notification callback permanently occupies the single
    // request slot until a physical disconnect.
    this->pending_started_ms_ = millis();
  }

  void finish_pair(bool encrypted, const char *error = nullptr,
                   const char *terminal_status = nullptr) {
    if (this->pending_op_ != PendingOp::PAIR || this->pending_request_id_ == 0 ||
        (encrypted && !this->pending_pair_matches()))
      return;
    const uint32_t request_id = this->pending_request_id_;
    this->clear_pending_without_event();
    this->encrypted_current_ = encrypted;
    this->record_pair_terminal(
        encrypted, terminal_status == nullptr
                       ? (encrypted ? "auth_success" : "auth_failure")
                       : terminal_status);
    if (encrypted) {
      this->response(request_id, "PAIR_ENCRYPT", "OK");
      this->emit_state("ENCRYPTION_STATE", "encrypted", request_id);
    } else {
      this->error(request_id, "PAIR_ENCRYPT", error == nullptr ? "security_required" : error);
      this->emit_state("ENCRYPTION_STATE", "failed", request_id);
    }
  }

  bool pending_callback_matches(esp_gatt_if_t gattc_if, uint16_t conn_id) const {
    return this->callback_matches(gattc_if, conn_id) &&
           this->pending_epoch_ == this->epoch_ &&
           gattc_if == this->pending_gattc_if_ && conn_id == this->pending_conn_id_;
  }

  void finish_write(bool ok) {
    const uint32_t request_id = this->pending_request_id_;
    const char *op = this->pending_op_ == PendingOp::DESCRIPTOR ? "WRITE_DESCRIPTOR" : "WRITE_CHAR";
    this->clear_pending_without_event();
    ok ? this->response(request_id, op, "OK")
       : this->write_failed(request_id, op);
  }

  void write_failed(uint32_t request_id, const char *op) {
    this->response(request_id, op, "WRITE_FAILED", "gatt_write_failed");
  }

  // Used by successful/failed completions.  It deliberately emits nothing;
  // normal terminal responses are emitted by the caller exactly once.
  void clear_pending_without_event() {
    this->pending_request_id_ = 0;
    this->pending_handle_ = 0;
    this->pending_cccd_handle_ = 0;
    this->pending_notification_handle_ = 0;
    this->pending_expect_notification_ = false;
    this->pending_op_ = PendingOp::NONE;
    this->pending_epoch_ = 0;
    this->pending_gattc_if_ = ESP_GATT_IF_NONE;
    this->pending_conn_id_ = 0;
    this->pending_started_ms_ = 0;
  }

  // Disconnect and explicit CANCEL are the only paths that emit CANCELLED.
  // Keeping this separate from clear_pending_without_event prevents a normal
  // WRITE/PAIR completion from producing a spurious cancellation envelope.
  void cancel_pending_emit_once(const char *pair_status = "cancelled") {
    if (this->pending_request_id_ != 0) {
      if (this->pending_op_ == PendingOp::PAIR)
        this->record_pair_terminal(false, pair_status);
      const char *op = this->pending_op_ == PendingOp::PAIR
                           ? "PAIR_ENCRYPT"
                           : (this->pending_op_ == PendingOp::SUBSCRIBE
                                  ? "SUBSCRIBE"
                                  : (this->pending_op_ == PendingOp::DESCRIPTOR
                                         ? "WRITE_DESCRIPTOR"
                                         : "WRITE_CHAR"));
      this->response(this->pending_request_id_, op, "CANCELLED", "cancelled");
    }
    this->clear_pending_without_event();
  }

  uint16_t bta_conn_id() const {
    // BTC_GATT_CREATE_CONN_ID(gattc_if, conn_id), matching ESPHome's
    // proven empty-write gate; do not pass the raw GAP connection ID.
    return static_cast<uint16_t>(
        (static_cast<uint16_t>(static_cast<uint8_t>(this->parent_->get_conn_id())) << 8) |
        static_cast<uint8_t>(this->parent_->get_gattc_if()));
  }

  void response(uint32_t request_id, const char *op, const char *status,
                const char *error = nullptr, uint16_t handle = 0) {
    this->enqueue(esphome::json::build_json([&](JsonObject root) {
      root["v"] = 1;
      root["kind"] = "response";
      root["op"] = op;
      root["epoch"] = this->epoch_;
      root["request_id"] = request_id;
      root["gattc_if"] = static_cast<uint8_t>(this->current_gattc_if_);
      root["conn_id"] = this->current_conn_id_;
      // Responses carry seq for a common envelope shape but do not consume
      // the event sequence; the native client uses seq==0 to distinguish
      // response acknowledgements from ordered callback events.
      root["seq"] = 0;
      root["status"] = status;
      if (error != nullptr)
        root["error"] = error;
      if (handle != 0)
        root["payload"]["handle"] = handle;
    }));
  }

  void response_session_independent(uint32_t request_id, const char *op,
                                    const char *status,
                                    const char *error = nullptr) {
    this->enqueue(esphome::json::build_json([&](JsonObject root) {
      root["v"] = 1;
      root["kind"] = "response";
      root["op"] = op;
      root["epoch"] = 0;
      root["request_id"] = request_id;
      root["gattc_if"] = 0;
      root["conn_id"] = 0;
      root["seq"] = 0;
      root["status"] = status;
      if (error != nullptr)
        root["error"] = error;
    }));
  }

  void error(uint32_t request_id, const char *op, const char *code) {
    if (request_id == 0)
      return;
    this->response(request_id, op, "ERROR", code);
  }

  // A malformed envelope has no trustworthy operation to preserve.  Keep it
  // distinct from correlated request errors; callers never use this as an
  // action response and must treat it as a protocol fault.
  void protocol_error(uint32_t request_id, const char *code) {
    if (request_id == 0)
      return;
    this->response(request_id, "INVALID_REQUEST", "ERROR", code);
  }

  void emit_state(const char *op, const char *state, uint32_t request_id) {
    this->next_seq_++;
    this->enqueue(esphome::json::build_json([&](JsonObject root) {
      root["v"] = 1;
      root["kind"] = "event";
      root["op"] = op;
      root["epoch"] = this->epoch_;
      root["request_id"] = request_id;
      root["gattc_if"] = static_cast<uint8_t>(this->current_gattc_if_);
      root["conn_id"] = this->current_conn_id_;
      root["seq"] = this->next_seq_;
      root["payload"]["state"] = state;
    }));
  }

  void emit_capability() {
    this->enqueue(esphome::json::build_json([&](JsonObject root) {
      root["v"] = 1;
      root["kind"] = "event";
      root["op"] = "CAPABILITY";
      root["epoch"] = 0;
      root["request_id"] = 0;
      root["gattc_if"] = 0;
      root["conn_id"] = 0;
      root["seq"] = 0;
      root["payload"]["state"] = CAPABILITY;
    }));
  }

  void emit_notification(uint16_t handle, const uint8_t *value, uint16_t length,
                         uint32_t request_id) {
    const std::string encoded = length == 0 ? "" : esphome::base64_encode(value, length);
    this->next_seq_++;
    this->enqueue(esphome::json::build_json([&](JsonObject root) {
      root["v"] = 1;
      root["kind"] = "event";
      root["op"] = "NOTIFICATION";
      root["epoch"] = this->epoch_;
      root["request_id"] = request_id;
      root["gattc_if"] = static_cast<uint8_t>(this->current_gattc_if_);
      root["conn_id"] = this->current_conn_id_;
      root["seq"] = this->next_seq_;
      root["payload"]["handle"] = handle;
      root["payload"]["value"] = encoded;
    }));
  }

  void enqueue(esphome::json::SerializationBuffer<> &&serialized) {
    std::string value = static_cast<std::string>(serialized);
    if (value.size() > MAX_FRAME_BYTES) {
      this->fatal_flow_control("frame_too_large");
      return;
    }
    if (this->events_.size() >= MAX_EVENT_QUEUE) {
      this->fatal_flow_control("queue_full");
      return;
    }
    this->events_.push_back(std::move(value));
    this->record_enqueued(this->events_.back());
  }

  void record_enqueued(const std::string &frame) {
    this->diagnostics_.total_frames_enqueued++;
    const bool is_response = frame.find("\"kind\":\"response\"") != std::string::npos;
    this->diagnostics_.last_enqueued_kind = is_response ? "response" : "event";
    if (frame.find("\"op\":\"CONNECT\"") != std::string::npos && is_response)
      this->diagnostics_.connect_responses_enqueued++;
    if (frame.find("\"op\":\"CONNECTION_STATE\"") != std::string::npos &&
        frame.find("\"state\":\"connected\"") != std::string::npos)
      this->diagnostics_.connected_state_enqueued++;
    const char *ops[] = {"CONNECT", "CONNECTION_STATE", "CAPABILITY", "FLOW_CONTROL",
                         "DISCONNECT", "PAIR_ENCRYPT", "DISCOVER", "HANDLE_LOOKUP",
                         "SUBSCRIBE", "WRITE_CHAR", "WRITE_DESCRIPTOR", "CANCEL",
                         "ENCRYPTION_STATE", "BOND_STATE", "NOTIFICATION", "ERROR"};
    for (const char *op : ops) {
      std::string marker = "\"op\":\"" + std::string(op) + "\"";
      if (frame.find(marker) != std::string::npos) {
        this->diagnostics_.last_enqueued_op = op;
        break;
      }
    }
  }

  void fatal_flow_control(const char *reason) {
    if (this->desynchronized_)
      return;
    this->desynchronized_ = true;
    this->events_.clear();
    // A bootstrap CONNECT has no PendingOp slot, so fence it explicitly with
    // its original operation before emitting the fatal marker.  Clearing the
    // request first makes this exactly once even if disconnect callbacks race.
    if (this->pending_connect_request_id_ != 0) {
      const uint32_t request_id = this->pending_connect_request_id_;
      this->pending_connect_request_id_ = 0;
      this->response(request_id, "CONNECT", "ERROR", "event_overflow");
    }
    // The forced disconnect also fences an in-flight ATT operation.  Emit its
    // original cancellation once; the queue was cleared above so this remains
    // bounded even when overflow was caused by a full ring.
    this->cancel_pending_emit_once();
    // This envelope is intentionally tiny and bounded, so it remains
    // deliverable even when the triggering frame was oversized.
    const uint32_t sequence = ++this->next_seq_;
    auto overflow = esphome::json::build_json([&](JsonObject root) {
      root["v"] = 1;
      root["kind"] = "event";
      root["op"] = "FLOW_CONTROL";
      root["epoch"] = this->epoch_;
      root["gattc_if"] = static_cast<uint8_t>(this->current_gattc_if_);
      root["conn_id"] = this->current_conn_id_;
      root["request_id"] = 0;
      root["seq"] = sequence;
      root["payload"]["state"] = "overflow";
      root["payload"]["reason"] = reason;
    });
    this->events_.push_back(static_cast<std::string>(overflow));
    this->record_enqueued(this->events_.back());
    // A fatal stream cannot be repaired in-place.  Force a physical epoch
    // transition; callers may issue DISCONNECT while the host unwinds.
    if (this->parent_ != nullptr && this->link_active_)
      this->parent_->disconnect();
  }

  static constexpr const char *TAG = "openrbus_gatt_rpc";
  static constexpr uint32_t ATT_TIMEOUT_MS = 10000;
  struct ScanSeen { uint64_t address; };
  esphome::ble_client::BLEClient *parent_{nullptr};
  std::deque<std::string> events_;
  Diagnostics diagnostics_{};
  std::vector<KnownHandle> known_handles_;
  PendingOp pending_op_{PendingOp::NONE};
  uint32_t epoch_{0};
  uint32_t next_seq_{0};
  uint32_t pending_request_id_{0};
  uint32_t pending_connect_request_id_{0};
  esp_gatt_if_t current_gattc_if_{ESP_GATT_IF_NONE};
  uint16_t current_conn_id_{0};
  uint8_t current_peer_bda_[ESP_BD_ADDR_LEN]{};
  esp_gatt_if_t pending_gattc_if_{ESP_GATT_IF_NONE};
  uint16_t pending_conn_id_{0};
  bool link_active_{false};
  bool physical_disconnect_emitted_{true};
  bool desynchronized_{false};
  bool encrypted_current_{false};
  bool capability_pending_{false};
  bool ready_{false};
  uint32_t pending_epoch_{0};
  uint16_t pending_handle_{0};
  uint16_t pending_cccd_handle_{0};
  uint16_t pending_notification_handle_{0};
  bool pending_expect_notification_{false};
  uint32_t pending_started_ms_{0};
  uint32_t scan_request_id_{0};
  uint32_t scan_deadline_ms_{0};
  std::vector<ScanSeen> scan_results_;
};

inline Rpc &instance() {
  static Rpc rpc;
  return rpc;
}

}  // namespace openrbus_thin_gatt
