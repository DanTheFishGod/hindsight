"""Background Sync and Periodic Background Sync decoder.

Sources:
  - Schema: content/browser/background_sync/background_sync.proto
  - Writer: content/browser/background_sync/background_sync_manager.cc

Both one-shot Background Sync and Periodic Background Sync share the same
`BackgroundSyncRegistrationProto` schema. The periodic variant is
distinguished by (a) the `PeriodicBackgroundSyncRegistration_` key prefix
instead of `BackgroundSyncRegistration_`, and (b) the presence of the
`periodic_sync_options` sub-message (carrying `min_interval` in ms).
"""

from pyhindsight import utils
from pyhindsight.lib.proto.content.browser.background_sync.background_sync_pb2 import (
    BackgroundSyncRegistrationProto)


ONE_SHOT_PREFIX = 'BackgroundSyncRegistration_'
PERIODIC_PREFIX = 'PeriodicBackgroundSyncRegistration_'


def matches(user_data_key):
    return (user_data_key.startswith(ONE_SHOT_PREFIX)
            or user_data_key.startswith(PERIODIC_PREFIX))


def decode(user_data_key, value, timezone):
    is_periodic = user_data_key.startswith(PERIODIC_PREFIX)
    label = 'periodic sync' if is_periodic else 'background sync'

    reg = BackgroundSyncRegistrationProto()
    try:
        reg.ParseFromString(value)
    except Exception as e:
        return (label,
                f'<BackgroundSyncRegistration decode failed: {e}; '
                f'len={len(value)}>', None)

    pieces = [f'tag={reg.tag!r}', f'num_attempts={reg.num_attempts}']
    if reg.HasField('max_attempts'):
        pieces.append(f'max_attempts={reg.max_attempts}')
    # delay_until is base::Time delta-since-Windows-epoch in microseconds;
    # 0 means "no delay / fire when conditions met".
    if reg.HasField('delay_until') and reg.delay_until:
        pieces.append(f'delay_until={utils.to_datetime(reg.delay_until, timezone)}')
    if reg.HasField('periodic_sync_options') \
            and reg.periodic_sync_options.HasField('min_interval'):
        pieces.append(f'min_interval_ms={reg.periodic_sync_options.min_interval}')
    return (label, '; '.join(pieces), None)
