"""Payment Handler / payment instrument decoder.

Sources:
  - Schema: content/browser/payments/payment_app.proto
  - Writer: content/browser/payments/payment_app_database.cc

The Payment Handler API stores three flavors of record per SW registration:
  PaymentApp:<scope_pattern>          → StoredPaymentAppProto (the SW-bound
                                         payment app: scope, name, icon, etc.)
  PaymentInstrument:<key>             → StoredPaymentInstrumentProto (one per
                                         payment method/credential the app
                                         offers: name, method, supported cards,
                                         icons)
  PaymentInstrumentKeyInfo:<key>      → StoredPaymentInstrumentKeyInfoProto
                                         (reverse-index helper: insertion
                                         order + key)
"""

from pyhindsight.lib.proto.content.browser.payments.payment_app_pb2 import (
    StoredPaymentAppProto,
    StoredPaymentInstrumentProto,
    StoredPaymentInstrumentKeyInfoProto,
)


APP_PREFIX = 'PaymentApp:'
INSTRUMENT_PREFIX = 'PaymentInstrument:'
KEY_INFO_PREFIX = 'PaymentInstrumentKeyInfo:'


def matches(user_data_key):
    return (user_data_key.startswith(APP_PREFIX)
            or user_data_key.startswith(INSTRUMENT_PREFIX)
            or user_data_key.startswith(KEY_INFO_PREFIX))


def decode(user_data_key, value, timezone):
    if user_data_key.startswith(KEY_INFO_PREFIX):
        # Reverse-index helper; lightweight.
        proto = StoredPaymentInstrumentKeyInfoProto()
        try:
            proto.ParseFromString(value)
        except Exception as e:
            return ('payment instrument',
                    f'<PaymentInstrumentKeyInfo decode failed: {e}>', None)
        pieces = []
        if proto.HasField('key'):
            pieces.append(f'key={proto.key!r}')
        if proto.HasField('insertion_order'):
            pieces.append(f'insertion_order={proto.insertion_order}')
        return ('payment instrument (key info)', '; '.join(pieces), None)

    if user_data_key.startswith(INSTRUMENT_PREFIX):
        # PaymentInstrument: one per payment credential the SW offers.
        proto = StoredPaymentInstrumentProto()
        try:
            proto.ParseFromString(value)
        except Exception as e:
            return ('payment instrument',
                    f'<PaymentInstrument decode failed: {e}>', None)
        pieces = []
        if proto.HasField('instrument_key'):
            pieces.append(f'instrument_key={proto.instrument_key!r}')
        if proto.HasField('name'):
            pieces.append(f'name={proto.name!r}')
        if proto.HasField('method'):
            pieces.append(f'method={proto.method!r}')
        if proto.HasField('registration_id'):
            pieces.append(f'registration_id={proto.registration_id}')
        if proto.HasField('stringified_capabilities') \
                and proto.stringified_capabilities:
            pieces.append(f'capabilities={proto.stringified_capabilities!r}')
        if proto.supported_card_networks:
            pieces.append(
                f'supported_card_networks={list(proto.supported_card_networks)}')
        if proto.icons:
            icon_strs = [f'src={i.src!r}' for i in proto.icons if i.HasField('src')]
            if icon_strs:
                pieces.append(f'icons=[{", ".join(icon_strs)}]')
        return ('payment instrument', '; '.join(pieces), None)

    # PaymentApp: the SW-bound payment-app entry.
    proto = StoredPaymentAppProto()
    try:
        proto.ParseFromString(value)
    except Exception as e:
        return ('payment app',
                f'<PaymentApp decode failed: {e}>', None)
    pieces = []
    if proto.HasField('registration_id'):
        pieces.append(f'registration_id={proto.registration_id}')
    if proto.HasField('scope'):
        pieces.append(f'scope={proto.scope}')
    if proto.HasField('name'):
        pieces.append(f'name={proto.name!r}')
    if proto.HasField('icon') and proto.icon:
        pieces.append(f'icon={proto.icon}')
    if proto.HasField('user_hint') and proto.user_hint:
        pieces.append(f'user_hint={proto.user_hint!r}')
    if proto.HasField('prefer_related_applications'):
        pieces.append(
            f'prefer_related_applications={proto.prefer_related_applications}')
    if proto.related_applications:
        ras = [f'{{platform={a.platform!r}, id={a.id!r}}}'
               for a in proto.related_applications]
        pieces.append(f'related_applications=[{", ".join(ras)}]')
    if proto.HasField('supported_delegations'):
        sd = proto.supported_delegations
        delegations = [name for name, val in [
            ('shipping_address', sd.shipping_address),
            ('payer_name', sd.payer_name),
            ('payer_phone', sd.payer_phone),
            ('payer_email', sd.payer_email),
        ] if val]
        if delegations:
            pieces.append(f'supported_delegations={delegations}')
    return ('payment app', '; '.join(pieces), None)
