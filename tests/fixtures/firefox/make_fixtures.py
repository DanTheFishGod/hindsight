# Run from the repo root: python tests/fixtures/firefox/make_fixtures.py
import datetime
import os
import sqlite3


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


def main():
    print(f'Writing fixtures to {HERE}')
    make_places()
    print('done.')


if __name__ == '__main__':
    main()
