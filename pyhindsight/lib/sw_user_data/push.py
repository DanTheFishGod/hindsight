"""Push Messaging subscription decoder.

Source: content/browser/push_messaging/push_messaging_manager.cc
The push subscription is split across several `REG_USER_DATA:` keys:
  - push_registration_id: GCM/FCM registration identifier (UTF-8 string)
  - push_subscription_id: alternative identifier (UTF-8 string)
  - push_endpoint: full FCM endpoint URL the push server delivers to
  - push_sender_id: P-256 EC public key of the *application server* allowed to
    push to this subscription (65 bytes: 0x04 prefix + 32 X + 32 Y)
"""

KEY_PREFIXES = (
    'push_registration_id',
    'push_subscription_id',
    'push_endpoint',
    'push_sender_id',
)
SUBSYSTEM_LABEL = 'push subscription'


def matches(user_data_key):
    return user_data_key in KEY_PREFIXES


def decode(user_data_key, value, timezone):
    if user_data_key == 'push_sender_id':
        if len(value) == 65 and value[:1] == b'\x04':
            return (SUBSYSTEM_LABEL,
                    f'p256_application_server_key=0x{value.hex()}', None)
        return (SUBSYSTEM_LABEL, f'sender_id_bytes=0x{value.hex()}', None)
    return (SUBSYSTEM_LABEL, value.decode('utf-8', errors='replace'), None)
