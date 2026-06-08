# Run from the repo root: python tests/fixtures/firefox/make_fixtures.py
import datetime
import json
import os
import sqlite3
import struct


HERE = os.path.dirname(os.path.abspath(__file__))


# PRTime microseconds for 2024-01-15 12:00:00 UTC.
REF_DT = datetime.datetime(2024, 1, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
REF_PRTIME = int(REF_DT.timestamp() * 1_000_000)


def _write_sqlite(path, schema_sql, rows):
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(schema_sql)
        for table, table_rows in rows.items():
            if not table_rows:
                continue
            placeholders = ','.join(['?'] * len(table_rows[0]))
            conn.executemany(
                f'INSERT INTO {table} VALUES ({placeholders})', table_rows)
        conn.commit()
    finally:
        conn.close()


def make_places():
    """places.sqlite with 3 URL visits, 2 bookmarks, 1 download annotation."""
    schema = """
    CREATE TABLE moz_places (
        id INTEGER PRIMARY KEY,
        url LONGVARCHAR,
        title LONGVARCHAR,
        rev_host LONGVARCHAR,
        visit_count INTEGER DEFAULT 0,
        hidden INTEGER DEFAULT 0,
        typed INTEGER DEFAULT 0,
        frecency INTEGER DEFAULT -1,
        last_visit_date INTEGER,
        guid TEXT,
        foreign_count INTEGER DEFAULT 0,
        url_hash INTEGER NOT NULL DEFAULT 0,
        description TEXT,
        preview_image_url TEXT,
        site_name TEXT,
        origin_id INTEGER,
        recalc_frecency INTEGER NOT NULL DEFAULT 0,
        alt_frecency INTEGER,
        recalc_alt_frecency INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE moz_historyvisits (
        id INTEGER PRIMARY KEY,
        from_visit INTEGER,
        place_id INTEGER,
        visit_date INTEGER,
        visit_type INTEGER,
        session INTEGER,
        source INTEGER NOT NULL DEFAULT 0,
        triggeringPlaceId INTEGER
    );
    CREATE TABLE moz_bookmarks (
        id INTEGER PRIMARY KEY,
        type INTEGER,
        fk INTEGER DEFAULT NULL,
        parent INTEGER,
        position INTEGER,
        title LONGVARCHAR,
        keyword_id INTEGER,
        folder_type TEXT,
        dateAdded INTEGER,
        lastModified INTEGER,
        guid TEXT,
        syncStatus INTEGER NOT NULL DEFAULT 0,
        syncChangeCounter INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE moz_anno_attributes (
        id INTEGER PRIMARY KEY,
        name VARCHAR(32) UNIQUE NOT NULL
    );
    CREATE TABLE moz_annos (
        id INTEGER PRIMARY KEY,
        place_id INTEGER NOT NULL,
        anno_attribute_id INTEGER,
        content LONGVARCHAR,
        flags INTEGER DEFAULT 0,
        expiration INTEGER DEFAULT 0,
        type INTEGER DEFAULT 0,
        dateAdded INTEGER DEFAULT 0,
        lastModified INTEGER DEFAULT 0
    );
    CREATE TABLE moz_origins (
        id INTEGER PRIMARY KEY,
        prefix TEXT,
        host TEXT,
        frecency INTEGER,
        recalc_frecency INTEGER NOT NULL DEFAULT 0,
        alt_frecency INTEGER,
        recalc_alt_frecency INTEGER NOT NULL DEFAULT 0
    );
    PRAGMA user_version = 80;
    """

    places = [
        (1, 'https://en.wikipedia.org/wiki/Computer_forensics',
         'Computer forensics - Wikipedia', 'gro.aidepikiw.ne.',
         2, 0, 1, 100, REF_PRTIME, 'placewiki1234567',
         0, 0, None, None, None, 1, 0, None, 0),
        (2, 'https://www.mozilla.org/en-US/firefox/',
         'Firefox Browser', 'gro.allizom.www.',
         1, 0, 0, 50, REF_PRTIME + 60_000_000, 'placemoz12345678',
         0, 0, None, None, None, 2, 0, None, 0),
        (3, 'https://example.com/big_file.zip',
         'big_file.zip', 'moc.elpmaxe.',
         1, 0, 0, 10, REF_PRTIME + 120_000_000, 'placedlsource12',
         0, 0, None, None, None, 3, 0, None, 0),
    ]
    # visit_type: 2=typed, 1=link, 7=download.
    visits = [
        (1, 0, 1, REF_PRTIME, 2, 0, 0, None),
        (2, 1, 2, REF_PRTIME + 60_000_000, 1, 0, 0, None),
        (3, 0, 3, REF_PRTIME + 120_000_000, 7, 0, 0, None),
    ]
    # id=1 root, id=2 'menu' folder, id=3 URL bookmark under menu.
    bookmarks = [
        (1, 2, None, 0, 0, '', None, None, REF_PRTIME, REF_PRTIME,
         'root________', 0, 1),
        (2, 2, None, 1, 0, 'menu', None, None, REF_PRTIME, REF_PRTIME,
         'menu________', 0, 1),
        (3, 1, 1, 2, 0, 'Computer forensics', None, None,
         REF_PRTIME, REF_PRTIME, 'bmkwiki12345', 0, 1),
    ]
    anno_attrs = [
        (1, 'downloads/destinationFileURI'),
        (2, 'downloads/metaData'),
    ]
    annos = [
        (1, 3, 1,
         'file:///C:/Users/test/Downloads/big_file.zip',
         0, 4, 3, REF_PRTIME + 120_000_000, REF_PRTIME + 121_000_000),
    ]
    origins = [
        (1, 'https://', 'en.wikipedia.org', 100, 0, None, 0),
        (2, 'https://', 'www.mozilla.org', 50, 0, None, 0),
        (3, 'https://', 'example.com', 10, 0, None, 0),
    ]

    _write_sqlite(
        os.path.join(HERE, 'places.sqlite'),
        schema,
        {
            'moz_places': places,
            'moz_historyvisits': visits,
            'moz_bookmarks': bookmarks,
            'moz_anno_attributes': anno_attrs,
            'moz_annos': annos,
            'moz_origins': origins,
        }
    )


def make_cookies():
    """cookies.sqlite with 2 cookies (one HttpOnly+Secure, one session)."""
    schema = """
    CREATE TABLE moz_cookies (
        id INTEGER PRIMARY KEY,
        originAttributes TEXT NOT NULL DEFAULT '',
        name TEXT,
        value TEXT,
        host TEXT,
        path TEXT,
        expiry INTEGER,
        lastAccessed INTEGER,
        creationTime INTEGER,
        isSecure INTEGER,
        isHttpOnly INTEGER,
        inBrowserElement INTEGER DEFAULT 0,
        sameSite INTEGER DEFAULT 0,
        rawSameSite INTEGER DEFAULT 0,
        schemeMap INTEGER DEFAULT 0,
        isPartitionedAttributeSet INTEGER DEFAULT 0
    );
    """
    rows = [
        # Wikipedia cookie: last-access a day after creation -> both rows emitted.
        (1, '', 'WMF-Last-Access', '15-Jan-2024',
         '.wikipedia.org', '/',
         int((REF_DT + datetime.timedelta(days=365)).timestamp()),
         REF_PRTIME + 86_400_000_000, REF_PRTIME,
         1, 1, 0, 0, 0, 0, 0),
        # Session cookie (expiry=0); creation == last-access -> only created row.
        (2, '', 'session', 'abc123',
         '.example.com', '/',
         0,
         REF_PRTIME + 1_000_000, REF_PRTIME + 1_000_000,
         0, 0, 0, 0, 0, 0, 0),
    ]
    _write_sqlite(
        os.path.join(HERE, 'cookies.sqlite'),
        schema,
        {'moz_cookies': rows},
    )


def make_form_history():
    """formhistory.sqlite with 2 saved form-field values."""
    schema = """
    CREATE TABLE moz_formhistory (
        id INTEGER PRIMARY KEY,
        fieldname TEXT NOT NULL,
        value TEXT NOT NULL,
        timesUsed INTEGER,
        firstUsed INTEGER,
        lastUsed INTEGER,
        guid TEXT
    );
    """
    rows = [
        (1, 'email', 'forensic@example.com', 3, REF_PRTIME, REF_PRTIME + 86_400_000_000, 'guid1aaa'),
        (2, 'searchbar-history', 'computer forensics', 1, REF_PRTIME, REF_PRTIME, 'guid2bbb'),
    ]
    _write_sqlite(
        os.path.join(HERE, 'formhistory.sqlite'),
        schema,
        {'moz_formhistory': rows},
    )


def make_permissions():
    """permissions.sqlite with 2 site permissions."""
    schema = """
    CREATE TABLE moz_perms (
        id INTEGER PRIMARY KEY,
        origin TEXT,
        type TEXT,
        permission INTEGER,
        expireType INTEGER,
        expireTime INTEGER,
        modificationTime INTEGER
    );
    """
    # modificationTime is unix ms (not PRTime).
    ref_ms = int(REF_DT.timestamp() * 1000)
    rows = [
        (1, 'https://en.wikipedia.org', 'desktop-notification', 2, 0, 0, ref_ms),
        (2, 'https://example.com', 'geo', 1, 2,
         ref_ms + 365 * 24 * 3600 * 1000, ref_ms),
    ]
    _write_sqlite(
        os.path.join(HERE, 'permissions.sqlite'),
        schema,
        {'moz_perms': rows},
    )


def make_logins_json():
    """logins.json with one saved credential (encrypted blobs are opaque)."""
    ref_ms = int(REF_DT.timestamp() * 1000)
    data = {
        'nextId': 2,
        'logins': [{
            'id': 1,
            'hostname': 'https://en.wikipedia.org',
            'httpRealm': None,
            'formSubmitURL': 'https://en.wikipedia.org',
            'usernameField': 'wpName',
            'passwordField': 'wpPassword',
            'encryptedUsername': 'MDoEEPgAAAAAAAAAAAAAAAAAAAEwFAYIKoZIhvcNAwcECEXAMPLE==',
            'encryptedPassword': 'MDoEEPgAAAAAAAAAAAAAAAAAAAEwFAYIKoZIhvcNAwcECEXAMPLE==',
            'guid': '{deadbeef-dead-beef-dead-beefdeadbeef}',
            'encType': 1,
            'timeCreated': ref_ms,
            'timeLastUsed': ref_ms + 86_400_000,
            'timePasswordChanged': ref_ms + 86_400_000 * 2,
            'timesUsed': 5,
            'syncCounter': 1,
            'everSynced': False,
            'encryptedUnknownFields': None,
        }],
        'potentiallyVulnerablePasswords': [],
        'dismissedBreachAlertsByLoginGUID': {},
        'version': 3,
    }
    with open(os.path.join(HERE, 'logins.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


def make_extensions_json():
    """extensions.json with one installed addon."""
    ref_ms = int(REF_DT.timestamp() * 1000)
    data = {
        'schemaVersion': 37,
        'addons': [{
            'id': 'uBlock0@raymondhill.net',
            'version': '1.55.0',
            'type': 'extension',
            'active': True,
            'userDisabled': False,
            'appDisabled': False,
            'signedState': 2,
            'sourceURI': 'https://addons.mozilla.org/firefox/downloads/file/4188948/ublock_origin-1.55.0.xpi',
            'location': 'app-profile',
            'path': 'C:\\Users\\test\\AppData\\Roaming\\Mozilla\\Firefox\\Profiles\\test.default\\extensions\\uBlock0@raymondhill.net.xpi',
            'rootURI': 'jar:file:///C:/Users/test/AppData/Roaming/Mozilla/Firefox/Profiles/test.default/extensions/uBlock0@raymondhill.net.xpi!/',
            'installDate': ref_ms,
            'updateDate': ref_ms + 86_400_000 * 7,
            'defaultLocale': {
                'name': 'uBlock Origin',
                'description': 'Finally, an efficient blocker.',
            },
            'userPermissions': {
                'permissions': ['storage', 'webRequest'],
                'origins': ['<all_urls>'],
            },
        }],
    }
    with open(os.path.join(HERE, 'extensions.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


def make_prefs_js():
    """prefs.js with a handful of forensically interesting preferences."""
    content = '''// Mozilla User Preferences

user_pref("browser.startup.homepage", "https://en.wikipedia.org/");
user_pref("browser.download.lastDir", "C:\\\\Users\\\\test\\\\Downloads");
user_pref("browser.download.useDownloadDir", false);
user_pref("network.proxy.type", 0);
user_pref("signon.rememberSignons", true);
user_pref("toolkit.telemetry.enabled", false);
user_pref("services.sync.username", "test@example.com");
user_pref("browser.search.region", "US");
user_pref("intl.accept_languages", "en-US, en");
user_pref("app.installation.timestamp", "1705320000000000");
'''
    with open(os.path.join(HERE, 'prefs.js'), 'w', encoding='utf-8') as f:
        f.write(content)


def _snappy_encode_raw(data):
    # Literal-tag-only encoder; round-trips through the decoder Firefox uses.
    out = bytearray()
    n = len(data)
    while True:
        if n < 0x80:
            out.append(n)
            break
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    i = 0
    while i < len(data):
        chunk_len = min(60, len(data) - i)
        out.append((chunk_len - 1) << 2)
        out.extend(data[i:i + chunk_len])
        i += chunk_len
    return bytes(out)


def make_local_storage():
    # storage/default/https+++en.wikipedia.org/ls/data.sqlite with one
    # uncompressed and one snappy-compressed value.
    origin_dir = os.path.join(
        HERE, 'storage', 'default', 'https+++en.wikipedia.org', 'ls')
    os.makedirs(origin_dir, exist_ok=True)

    schema = """
    CREATE TABLE database (
        origin TEXT NOT NULL,
        usage INTEGER NOT NULL DEFAULT 0,
        last_vacuum_time INTEGER NOT NULL DEFAULT 0,
        last_analyze_time INTEGER NOT NULL DEFAULT 0,
        last_vacuum_size INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE data (
        key TEXT PRIMARY KEY,
        utf16_length INTEGER NOT NULL,
        conversion_type INTEGER NOT NULL,
        compression_type INTEGER NOT NULL,
        last_access_time INTEGER NOT NULL,
        value BLOB NOT NULL
    );
    """

    long_value = b'{"recent_articles": ["Computer forensics", "Cryptography", "Steganography"], "sessionCount": 42}'
    rows_data = [
        ('theme', 4, 1, 0, REF_PRTIME, b'dark'),
        ('app_state', len(long_value), 1, 1, REF_PRTIME,
         _snappy_encode_raw(long_value)),
    ]
    rows_database = [
        ('https://en.wikipedia.org', 4096, 0, 0, 0),
    ]
    _write_sqlite(
        os.path.join(origin_dir, 'data.sqlite'),
        schema,
        {'database': rows_database, 'data': rows_data},
    )


def make_cache2_entry():
    # Layout (BE unless noted): [body | chunk_hashes | metadata_block | u32 meta_offset]
    cache_dir = os.path.join(HERE, 'cache2', 'entries')
    os.makedirs(cache_dir, exist_ok=True)

    body = b'<html><head><title>Computer forensics</title></head></html>'
    meta_offset = len(body)

    key = b':https://en.wikipedia.org/wiki/Computer_forensics'
    key_size = len(key)
    fetched_secs = int(REF_DT.timestamp())
    header = struct.pack(
        '>IIIIIIII',
        3,
        7,
        fetched_secs,
        fetched_secs - 60,
        100,
        fetched_secs + 86400,
        key_size,
        0,
    )
    elements = (
        b'response-head\x00'
        b'HTTP/2 200 OK\r\ncontent-type: text/html; charset=UTF-8\r\netag: "fixture-etag"\r\nlast-modified: Mon, 15 Jan 2024 11:00:00 GMT\r\n\x00'
        b'request-method\x00GET\x00'
    )
    crc = b'\x00' * 4  # parser ignores CRC
    meta_block = crc + header + key + b'\x00' + elements

    chunk_size = 256 * 1024
    n_chunks = (meta_offset + chunk_size - 1) // chunk_size
    hash_arr = b'\x00' * (n_chunks * 2)

    trailing = struct.pack('>I', meta_offset)

    entry_path = os.path.join(cache_dir, 'FIXTUREENTRY00000000000000000000FIXTURE0')
    with open(entry_path, 'wb') as f:
        f.write(body)
        f.write(hash_arr)
        f.write(meta_block)
        f.write(trailing)


def main():
    print(f'Writing fixtures to {HERE}')
    make_places()
    make_cookies()
    make_form_history()
    make_permissions()
    make_logins_json()
    make_extensions_json()
    make_prefs_js()
    make_local_storage()
    make_cache2_entry()
    print('done.')


if __name__ == '__main__':
    main()
