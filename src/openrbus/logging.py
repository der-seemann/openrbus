"""Safe logging helpers that redact credentials and installation identifiers."""

from __future__ import annotations

import logging
import re

_MAC = re.compile(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(pin|passkey|password|authorization[_ -]?key|auth[_ -]?payload)\b\s*[:=]\s*\S+"
)


def redact(value: object) -> str:
    """Return a log-safe representation of common sensitive values."""

    text = str(value)
    text = _MAC.sub("<redacted-mac>", text)
    return _SENSITIVE_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=<redacted>", text)


class RedactingFilter(logging.Filter):
    """Redact message text and arguments before a record reaches handlers."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Render first so numeric format specifiers (for example ``%02x``)
        # continue to work, then replace the record with a redacted literal.
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    """Configure the package logger once with redaction enabled."""

    logger = logging.getLogger("openrbus")
    if not any(isinstance(item, RedactingFilter) for item in logger.filters):
        logger.addFilter(RedactingFilter())
    logger.setLevel(level)
