from __future__ import annotations

import logging

from openrbus.logging import RedactingFilter


def test_redaction_preserves_numeric_formatting_and_removes_identifiers() -> None:
    synthetic_identifier = ":".join(("aa", "bb", "cc", "dd", "ee", "ff"))
    record = logging.LogRecord(
        "openrbus.test",
        logging.INFO,
        __file__,
        1,
        "node=%02x device=%s pin=%s",
        (3, synthetic_identifier, "123456"),
        None,
    )
    assert RedactingFilter().filter(record)
    assert record.getMessage() == "node=03 device=<redacted-mac> pin=<redacted>"
