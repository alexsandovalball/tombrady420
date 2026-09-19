"""
PII-safe logging filter.

Masks personally identifiable information (PII) in log records before they
are emitted to any handler.  Attach this filter to any logger or handler where
long-term log retention must not include raw user identifiers.

Fields masked
-------------
* ``user_id``   – replaced with a stable truncated SHA-256 hash (first 8 hex chars)
* ``username``  – replaced with first char + ``***``
* ``photo_url`` – replaced with ``<redacted>``

The masking approach uses regex substitution on the *formatted* message string
so it works regardless of how the message was constructed (f-string, %s, etc.).

Usage
-----
Attach globally in ``configs/logging_config.py``::

    from utils.logging_filters import PIIRedactFilter
    logging.getLogger().addFilter(PIIRedactFilter())

Or selectively to a single handler::

    handler.addFilter(PIIRedactFilter())
"""

import hashlib
import logging
import re
import traceback
from typing import Pattern

# ---------------------------------------------------------------------------
# Compiled patterns – kept at module level for speed
# ---------------------------------------------------------------------------

# user_id=<digits>  →  user_id=<hash>
_RE_USER_ID: Pattern = re.compile(r"(user_id=)(\d+)")

# username=<word chars / hyphens>  →  username=<first>***
_RE_USERNAME: Pattern = re.compile(r"(username=)([A-Za-z0-9_\-\.])([A-Za-z0-9_\-\.]*)")

# photo_url=<non-whitespace>  →  photo_url=<redacted>
_RE_PHOTO_URL: Pattern = re.compile(r'(photo_url=")([^"]+)(")')
# Also handle JSON-escaped / unquoted forms that appear in log details
_RE_PHOTO_URL_JSON: Pattern = re.compile(
    r'("photo_url"\s*:\s*")([^"]+)(")',
    re.IGNORECASE,
)

# Telegram bot tokens can be included verbatim in exceptions raised while an
# Application is being initialized. Match both plain tokens and tokens embedded
# in Telegram API URLs without relying on the configured token list.
_RE_TELEGRAM_BOT_TOKEN: Pattern = re.compile(
    r"(?<![A-Za-z0-9_-])\d{5,20}:[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])"
)


def _hash_user_id(raw: str) -> str:
    """Return a stable 8-char hex digest of a raw user_id string."""
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def _mask_username(prefix: str, first_char: str, rest: str) -> str:
    return f"{prefix}{first_char}***"


def redact_sensitive_text(value: object) -> str:
    """Return log-safe text with credential-shaped values removed."""
    return _RE_TELEGRAM_BOT_TOKEN.sub("<redacted-telegram-bot-token>", str(value or ""))


class PIIRedactFilter(logging.Filter):
    """Logging filter that redacts PII from log record messages in-place.

    Works on ``LogRecord.getMessage()`` output, replacing the final
    formatted string stored in ``record.msg`` so all downstream handlers
    see the redacted version.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        # Format the final message (applies % formatting if used)
        try:
            msg = record.getMessage()
        except Exception:
            return True  # Don't suppress on formatting errors

        # Apply credential and PII masks.
        msg = redact_sensitive_text(msg)
        msg = _RE_USER_ID.sub(
            lambda m: m.group(1) + _hash_user_id(m.group(2)), msg
        )
        msg = _RE_USERNAME.sub(
            lambda m: _mask_username(m.group(1), m.group(2), m.group(3)), msg
        )
        msg = _RE_PHOTO_URL.sub(r'\1<redacted>\3', msg)
        msg = _RE_PHOTO_URL_JSON.sub(r'\1<redacted>\3', msg)

        # Store back as a plain string so the handler doesn't re-format
        record.msg = msg
        record.args = ()

        # A traceback is formatted after filters run and can repeat the raw
        # exception value. Pre-format and redact it, then clear exc_info so the
        # handler uses only the safe copy.
        if record.exc_info:
            try:
                record.exc_text = redact_sensitive_text(
                    "".join(traceback.format_exception(*record.exc_info))
                )
                record.exc_info = None
            except Exception:
                record.exc_text = "<exception traceback unavailable>"
                record.exc_info = None
        return True
