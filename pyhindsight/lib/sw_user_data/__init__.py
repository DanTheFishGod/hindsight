"""Decoders for `REG_USER_DATA:` records in the Service Worker LevelDB.

Each Chromium subsystem that attaches data to a service worker registration
(Push Messaging, Background Sync, Notifications, DevTools, etc.) writes
opaque bytes into the Service Worker database under a key it owns. This
package contains one module per subsystem, each exposing:

    KEY_PREFIXES: tuple[str, ...]
    SUBSYSTEM_LABEL: str
    def matches(user_data_key: str) -> bool
    def decode(user_data_key: str, value: bytes, timezone) -> tuple[
            str, str, datetime.datetime | None]

`decode` returns `(label, formatted_value, event_time)`:
  - `label` is the friendly subsystem name surfaced in the SW sheet Type column
    (also used to build the `service worker (<label>)` row_type).
  - `formatted_value` is the human-readable string for the Value column.
  - `event_time` (or None) is hoisted into the row's last_modified column when
    the subsystem records a per-event timestamp (e.g. DevTools events,
    Notification creation times).

The dispatcher walks `DECODERS` in order and returns the first match. If no
decoder matches, the value falls through to a generic UTF-8/hex preview.
"""

from . import (
    push,
    background_sync,
    notifications,
    devtools,
    payment,
    cookie_store,
    content_index,
    background_fetch,
)

DECODERS = [
    push,
    background_sync,
    notifications,
    devtools,
    payment,
    cookie_store,
    content_index,
    background_fetch,
]


def decode_user_data(user_data_key, value, timezone):
    """Dispatch a single `REG_USER_DATA:` record to its subsystem decoder.

    Falls back to a generic UTF-8/hex preview when no decoder matches —
    surfaces unknown rows under the `service worker (user data)` row_type so
    they aren't silently dropped.
    """
    for d in DECODERS:
        if d.matches(user_data_key):
            return d.decode(user_data_key, value, timezone)
    try:
        return ('user data', value.decode('utf-8'), None)
    except UnicodeDecodeError:
        return ('user data',
                f'<binary len={len(value)}, first 32 bytes=0x{value[:32].hex()}>',
                None)
