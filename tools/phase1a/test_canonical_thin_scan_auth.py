"""Contract checks for the canonical unified Thin scan/auth composition."""

import hashlib
import unittest
from pathlib import Path

ROOT = Path(__file__).parent / "esphome" / "canonical-thin-scan-auth-20260920"


class CanonicalThinScanAuthChecks(unittest.TestCase):
    def test_composition_contains_scan_and_auth_fences(self) -> None:
        scan = (ROOT / "openrbus_gatt_rpc.h").read_text()
        transport = (ROOT / "openrbus_transport.h").read_text()
        runtime = (ROOT / "base-runtime.yaml").read_text()
        self.assertIn('if (op == "SCAN")', scan)
        self.assertIn('"SCAN_RESULT"', scan)
        self.assertIn('"SCAN_DONE"', scan)
        self.assertIn('if (op == "CANCEL"', scan)
        scan_branch = scan.index('if (op == "SCAN")')
        identity_guard = scan.index("if (request_gattc_if !=", scan_branch)
        self.assertLess(scan_branch, identity_guard)
        scan_contract = scan[scan_branch:identity_guard]
        self.assertIn("epoch != 0 || request_gattc_if != 0 || request_conn_id != 0", scan_contract)
        self.assertIn(
            'response_session_independent(request_id, op.c_str(), "ERROR",', scan_contract
        )
        cancel_branch = scan.index('if (op == "CANCEL" && this->scan_request_id_ != 0)')
        self.assertLess(cancel_branch, identity_guard)
        cancel_contract = scan[cancel_branch:identity_guard]
        self.assertIn(
            "epoch != 0 || request_gattc_if != 0 || request_conn_id != 0", cancel_contract
        )
        result = scan[scan.index('"SCAN_RESULT"') : scan.index("void finish_scan_if_due")]
        done = scan[scan.index('"SCAN_DONE"') : scan.index("void fail_connect_bootstrap")]
        for emitted in (result, done):
            self.assertIn('root["epoch"] = 0;', emitted)
            self.assertIn('root["gattc_if"] = 0;', emitted)
            self.assertIn('root["conn_id"] = 0;', emitted)
        self.assertIn("try_begin_auth()", transport)
        self.assertIn("end_auth()", transport)
        self.assertIn("auth_busy_", transport)
        self.assertIn("openrbus_phase2::instance().end_auth();", runtime)
        self.assertIn("openrbus_phase2::instance().set_authenticated", runtime)
        self.assertIn("terminal_result: success", runtime)

    def test_pinned_header_hashes(self) -> None:
        expected = {
            "openrbus_gatt_rpc.h": (
                "64c7a3b155905840e5169ddcbe4c0dbff0732c5e27dde2a0db91591a54ad9e47"
            ),
            "openrbus_transport.h": (
                "163d1b13a700e9037bb858563553b02cae9d92cb867c014588ba3ed1abbbfe87"
            ),
            "openrbus_zero_write.h": (
                "79682d699eab0db5ae5cc07861752a526e6fa34c67c2878feb6c4796fcf62d0f"
            ),
        }
        for name, digest in expected.items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), digest, name)


if __name__ == "__main__":
    unittest.main()
