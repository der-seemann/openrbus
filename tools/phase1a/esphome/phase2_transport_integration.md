# Phase-2 ESPHome transport wiring

The productive YAML remains `/config/esphome/heizungskeller-ble-proxy.yaml`.
The following additive wiring is the reviewed offline experiment; it must be
applied only after a separate review and must not be sent to the remote proxy
without explicit approval.

1. Add `openrbus_transport.h` beside `openrbus_zero_write.h` and include it in
   `esphome.includes`.
2. Set `api.custom_services: true`.
3. Call `openrbus_phase2::instance().begin(id(ehc16_ble_client))` from the
   existing 250-ms interval. The bridge registers exactly one
   `BLEClientNode` on the existing client.
4. In the existing gateway-auth notification callback call
   `openrbus_phase2::instance().set_authenticated(id(openrbus_gateway_auth_ok))`.
5. The bridge registers the Native API service `openrbus_raw_read` with
   arguments `request_id: int` and `frame: string` (hexadecimal bytes).
6. Enable the long-lived authenticated session with the existing API action
   `openrbus_enable_dynamic_transport`; the default remains the original
   connect/auth/read/disconnect behavior. Disable it with
   `openrbus_disable_dynamic_transport`.

The bridge resolves the transport write characteristic by UUID after the
existing service discovery, writes one non-empty frame with response, and
observes the response notification already owned by the BLE text-sensor node.
It does not register another CCCD, perform pairing, or use `ble_write` for
runtime frames. Response bytes are exposed through the existing raw-response
sensor plus the monotonic generation marker and last-request-id diagnostic
sensor.
