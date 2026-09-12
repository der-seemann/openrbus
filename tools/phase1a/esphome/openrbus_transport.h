#pragma once

// Phase-2 ESPHome transport bridge.  This node deliberately reuses the
// existing ESPHome BLEClient; it does not create a second connection or
// security state machine.
#include "esphome/components/api/custom_api_device.h"
#include "esphome/components/ble_client/ble_client.h"
#include "esphome/core/log.h"

#include <esp_gattc_api.h>

#include <cstdint>
#include <string>
#include <vector>

namespace openrbus_phase2 {

class Transport : public esphome::ble_client::BLEClientNode,
                  public esphome::api::CustomAPIDevice {
 public:
  enum class State : uint8_t { NOT_READY, AUTHENTICATING, READY, REQUEST_IN_FLIGHT, ERROR };

  void begin(esphome::ble_client::BLEClient *client) {
    if (this->parent_ != nullptr || client == nullptr)
      return;
    this->parent_ = client;
    client->register_ble_node(this);
    this->register_service(&Transport::api_request, "openrbus_raw_read", {"request_id", "frame"});
    ESP_LOGI(TAG, "runtime raw transport attached to existing BLE client");
  }

  void set_authenticated(bool authenticated) {
    this->authenticated_ = authenticated;
    if (!authenticated) {
      this->state_ = State::NOT_READY;
      this->request_in_flight_ = false;
      this->write_completed_ = false;
      this->request_id_ = 0;
      this->response_.clear();
      this->response_ready_ = false;
    } else if (this->enabled_ && this->transport_handle_ != 0) {
      this->state_ = State::READY;
    } else {
      this->state_ = State::AUTHENTICATING;
    }
  }

  void set_enabled(bool enabled) {
    this->enabled_ = enabled;
    if (!enabled) {
      this->request_in_flight_ = false;
      this->write_completed_ = false;
      this->request_id_ = 0;
      this->response_.clear();
      this->response_ready_ = false;
      this->state_ = State::NOT_READY;
    } else if (this->authenticated_ && this->transport_handle_ != 0) {
      this->state_ = State::READY;
    } else {
      this->state_ = State::AUTHENTICATING;
    }
  }

  bool take_response(std::vector<uint8_t> &response, uint32_t &request_id, uint32_t &generation) {
    if (!this->response_ready_)
      return false;
    response = this->response_;
    request_id = this->request_id_;
    generation = this->generation_;
    this->response_.clear();
    this->response_ready_ = false;
    this->state_ = this->enabled_ && this->authenticated_ && this->transport_handle_ != 0 ? State::READY : State::NOT_READY;
    return true;
  }

  State state() const { return this->state_; }

  void loop() override {
    if (this->request_in_flight_ && millis() - this->request_started_ms_ > REQUEST_TIMEOUT_MS) {
      this->request_in_flight_ = false;
      this->write_completed_ = false;
      this->request_id_ = 0;
      this->state_ = State::ERROR;
      ESP_LOGW(TAG, "raw request timed out; transport returned to ERROR");
    }
  }

  void gattc_event_handler(esp_gattc_cb_event_t event, esp_gatt_if_t gattc_if,
                           esp_ble_gattc_cb_param_t *param) override {
    (void)gattc_if;
    switch (event) {
      case ESP_GATTC_SEARCH_CMPL_EVT: {
        static const uint8_t service_uuid[] = {0xF8, 0xFC, 0x98, 0xE4, 0x59, 0x19, 0x4A, 0x5C,
                                               0x85, 0x2E, 0xDF, 0xE0, 0x4A, 0xD3, 0x83, 0xC0};
        static const uint8_t write_uuid[] = {0x49, 0x6B, 0x1B, 0x03, 0xCE, 0xBC, 0x4D, 0x59,
                                             0x9C, 0x32, 0x14, 0xEA, 0x88, 0xC2, 0x66, 0xF9};
        auto *chr = this->parent_->get_characteristic(
            esphome::esp32_ble_tracker::ESPBTUUID::from_raw(service_uuid),
            esphome::esp32_ble_tracker::ESPBTUUID::from_raw(write_uuid));
        this->transport_handle_ = chr == nullptr ? 0 : chr->handle;
        this->node_state = esphome::esp32_ble_tracker::ClientState::ESTABLISHED;
        this->state_ = this->enabled_ && this->authenticated_ && this->transport_handle_ != 0 ? State::READY : State::AUTHENTICATING;
        break;
      }
      case ESP_GATTC_WRITE_CHAR_EVT:
        if (this->request_in_flight_ && param->write.handle == this->transport_handle_) {
          if (param->write.status != ESP_GATT_OK) {
            this->state_ = State::ERROR;
            this->request_in_flight_ = false;
            this->write_completed_ = false;
            this->request_id_ = 0;
          } else {
            this->write_completed_ = true;
          }
        }
        break;
      case ESP_GATTC_NOTIFY_EVT:
        if (this->request_in_flight_ && this->write_completed_ &&
            param->notify.handle == this->transport_handle_ &&
            param->notify.value_len != 0) {
          this->response_.assign(param->notify.value, param->notify.value + param->notify.value_len);
          this->response_ready_ = true;
          this->request_in_flight_ = false;
          this->write_completed_ = false;
          this->generation_++;
          this->state_ = State::READY;
        }
        break;
      case ESP_GATTC_DISCONNECT_EVT:
      case ESP_GATTC_CLOSE_EVT:
        this->transport_handle_ = 0;
        this->request_in_flight_ = false;
        this->write_completed_ = false;
        this->response_.clear();
        this->response_ready_ = false;
        this->request_id_ = 0;
        this->state_ = State::NOT_READY;
        this->authenticated_ = false;
        this->node_state = esphome::esp32_ble_tracker::ClientState::IDLE;
        break;
      default:
        break;
    }
  }

 protected:
  void api_request(int32_t request_id, std::string frame) {
    std::vector<uint8_t> bytes;
    if (request_id < 0 || frame.size() > 128 || !parse_hex(frame, bytes) || bytes.empty() || bytes.size() > 64) {
      this->state_ = State::ERROR;
      ESP_LOGW(TAG, "raw request rejected: malformed frame");
      return;
    }
    if (!this->enabled_ || !this->authenticated_ || this->transport_handle_ == 0 || this->request_in_flight_ ||
        this->response_ready_) {
      this->state_ = State::ERROR;
      ESP_LOGW(TAG, "raw request rejected: transport not ready or busy");
      return;
    }
    this->request_id_ = static_cast<uint32_t>(request_id);
    this->write_completed_ = false;
    this->request_started_ms_ = millis();
    esp_err_t err = esp_ble_gattc_write_char(
        this->parent_->get_gattc_if(), this->parent_->get_conn_id(), this->transport_handle_, bytes.size(),
        bytes.data(), ESP_GATT_WRITE_TYPE_RSP, ESP_GATT_AUTH_REQ_MITM);
    if (err != ESP_OK) {
      this->state_ = State::ERROR;
      ESP_LOGW(TAG, "raw request write failed: %s", esp_err_to_name(err));
      return;
    }
    this->request_in_flight_ = true;
    this->state_ = State::REQUEST_IN_FLIGHT;
  }

  static bool parse_hex(const std::string &text, std::vector<uint8_t> &out) {
    if (text.empty() || text.size() > 128 || (text.size() & 1) != 0)
      return false;
    out.reserve(text.size() / 2);
    for (size_t i = 0; i < text.size(); i += 2) {
      const int hi = hex_digit(text[i]);
      const int lo = hex_digit(text[i + 1]);
      if (hi < 0 || lo < 0)
        return false;
      out.push_back(static_cast<uint8_t>((hi << 4) | lo));
    }
    return true;
  }

  static int hex_digit(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
  }

  static constexpr const char *TAG = "openrbus_transport";
  static constexpr uint32_t REQUEST_TIMEOUT_MS = 10000;
  esphome::ble_client::BLEClient *parent_{nullptr};
  uint16_t transport_handle_{0};
  uint32_t request_id_{0};
  uint32_t generation_{0};
  uint32_t request_started_ms_{0};
  bool enabled_{false};
  bool authenticated_{false};
  bool request_in_flight_{false};
  bool write_completed_{false};
  bool response_ready_{false};
  State state_{State::NOT_READY};
  std::vector<uint8_t> response_;
};

inline Transport &instance() {
  static Transport transport;
  return transport;
}

}  // namespace openrbus_phase2

