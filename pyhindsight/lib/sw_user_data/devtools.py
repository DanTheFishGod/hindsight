"""DevTools Background Services event decoder.

Source: content/browser/devtools/devtools_background_services_context_impl.cc

When a developer has the DevTools "Background services" panel open with
recording enabled, every event in one of six SW-adjacent subsystems is
logged under a `REG_USER_DATA:` key of the form:

    devtools_background_services_<service_int>_<UUID>

`<service_int>` is the `BackgroundService` enum value identifying which
subsystem the developer was recording. The value is a
`BackgroundServiceEvent` protobuf carrying the event name, instance_id,
origin, the SW registration_id it concerns, an `event_metadata` string map,
and a per-event timestamp.

The timestamp is hoisted into the row's `last_modified` column so devtools
events sort chronologically alongside other timestamped artifacts.
"""

from pyhindsight import utils
from pyhindsight.lib.proto.content.browser.devtools.devtools_background_services_pb2 import (
    BackgroundServiceEvent, BackgroundService)


KEY_PREFIX = 'devtools_background_services_'


def matches(user_data_key):
    return user_data_key.startswith(KEY_PREFIX)


def decode(user_data_key, value, timezone):
    evt = BackgroundServiceEvent()
    try:
        evt.ParseFromString(value)
    except Exception as e:
        return ('devtools event',
                f'<BackgroundServiceEvent decode failed: {e}>', None)

    service_label = (BackgroundService.Name(evt.background_service).lower()
                     if evt.HasField('background_service') else 'unknown')
    ts = (utils.to_datetime(evt.timestamp, timezone)
          if evt.HasField('timestamp') else None)

    pieces = []
    if evt.HasField('event_name'):
        pieces.append(f'event={evt.event_name}')
    if evt.HasField('instance_id'):
        pieces.append(f'instance_id={evt.instance_id}')
    if evt.HasField('origin'):
        pieces.append(f'origin={evt.origin}')
    if evt.HasField('service_worker_registration_id'):
        pieces.append(f'sw_reg_id={evt.service_worker_registration_id}')
    if evt.event_metadata:
        meta = ', '.join(f'{k}={v}'
                         for k, v in sorted(evt.event_metadata.items()))
        pieces.append(f'metadata={{{meta}}}')

    return (f'devtools: {service_label}', '; '.join(pieces), ts)
