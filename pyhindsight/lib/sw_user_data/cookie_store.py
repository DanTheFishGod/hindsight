"""Cookie Store API subscription decoder.

Sources:
  - Schema: content/browser/cookie_store/cookie_change_subscriptions.proto
  - Writer: content/browser/cookie_store/cookie_store_manager.cc

A service worker can register `cookiechange` event listeners describing which
cookies it wants to be notified about. All subscriptions for one registration
are bundled into a single `CookieChangeSubscriptionsProto` value under the
fixed `cookie_store_subscriptions` key.
"""

from pyhindsight.lib.proto.content.browser.cookie_store.cookie_change_subscriptions_pb2 import (
    CookieChangeSubscriptionsProto, CookieMatchType)


KEY = 'cookie_store_subscriptions'


def matches(user_data_key):
    return user_data_key == KEY


def decode(user_data_key, value, timezone):
    subs = CookieChangeSubscriptionsProto()
    try:
        subs.ParseFromString(value)
    except Exception as e:
        return ('cookie subscription',
                f'<CookieChangeSubscriptions decode failed: {e}>', None)

    if not subs.subscriptions:
        return ('cookie subscription', '<empty subscription list>', None)

    sub_strs = []
    for s in subs.subscriptions:
        bits = [f'url={s.url}']
        if s.name:
            bits.append(f'name={s.name!r}')
        bits.append(f'match_type={CookieMatchType.Name(s.match_type)}')
        sub_strs.append('{' + ', '.join(bits) + '}')
    return ('cookie subscription',
            f'subscriptions=[{", ".join(sub_strs)}]', None)
