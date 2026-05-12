# -*- coding: utf-8 -*-
import copy
import datetime
import logging
import os
import re
import urllib.parse

from pyhindsight import utils
from pyhindsight.browsers.webbrowser import WebBrowser

log = logging.getLogger(__name__)


# moz_historyvisits.visit_type values from PlacesUtils:
# https://searchfox.org/mozilla-central/source/toolkit/components/places/nsINavHistoryService.idl
FIREFOX_VISIT_TYPES = {
    1: 'Link',
    2: 'Typed',
    3: 'Bookmark',
    4: 'Embed',
    5: 'Redirect (permanent)',
    6: 'Redirect (temporary)',
    7: 'Download',
    8: 'Framed Link',
    9: 'Reload',
}

# moz_bookmarks.type values
BOOKMARK_TYPE_URL = 1
BOOKMARK_TYPE_FOLDER = 2
BOOKMARK_TYPE_SEPARATOR = 3


class Firefox(WebBrowser):
    def __init__(self, profile_path, browser_name=None, cache_path=None, version=None, timezone=None,
                 no_copy=None, temp_dir=None):
        WebBrowser.__init__(
            self, profile_path, browser_name=browser_name, cache_path=cache_path, version=version,
            timezone=timezone, no_copy=no_copy, temp_dir=temp_dir)
        self.profile_path = profile_path
        self.browser_name = "Firefox"
        self.cache_path = cache_path
        self.timezone = timezone
        self.no_copy = no_copy
        self.temp_dir = temp_dir

        if self.version is None:
            self.version = []
        if self.structure is None:
            self.structure = {}

    def _open(self, path, database):
        conn = utils.open_sqlite_db(self, path, database)
        if not conn:
            self.artifacts_counts[database] = 'Failed'
            return None
        return conn

    @staticmethod
    def _visit_type_friendly(visit_type):
        if visit_type is None:
            return None
        return FIREFOX_VISIT_TYPES.get(visit_type, f'Unknown ({visit_type})')

    def determine_version(self, path, database='places.sqlite'):
        # places.sqlite tracks schema with PRAGMA user_version; Firefox 62+ is >= 52.
        conn = self._open(path, database)
        if not conn:
            return
        try:
            cursor = conn.cursor()
            cursor.execute('PRAGMA user_version')
            row = cursor.fetchone()
            if row:
                user_version = list(row.values())[0]
                if user_version:
                    self.version.append(user_version)
                    self.display_version = f'places schema v{user_version}'
                    log.info(f' - places.sqlite user_version: {user_version}')
        except Exception as e:
            log.warning(f' - Could not read places.sqlite user_version: {e}')
        finally:
            conn.close()

    def get_history(self, path, database='places.sqlite', row_type='url'):
        results = []
        log.info(f'History items from {database}:')

        conn = self._open(path, database)
        if not conn:
            return

        try:
            cursor = conn.cursor()
            # `hidden` rows are framed/redirect-only entries the user didn't navigate to;
            # keep them so examiners can filter in the output rather than us deciding.
            query = (
                "SELECT p.id AS place_id, p.url, p.title, p.visit_count, "
                "       p.typed, p.hidden, p.last_visit_date, p.frecency, "
                "       p.description, p.preview_image_url, "
                "       v.id AS visit_id, v.visit_date, v.visit_type, "
                "       v.from_visit, v.session, "
                "       (SELECT url FROM moz_places "
                "         WHERE id = (SELECT place_id FROM moz_historyvisits "
                "                      WHERE id = v.from_visit)) AS from_url "
                "FROM moz_places p "
                "JOIN moz_historyvisits v ON p.id = v.place_id"
            )
            try:
                cursor.execute(query)
            except Exception as e:
                log.error(f' - Could not query history: {e}')
                self.artifacts_counts[database] = 'Failed'
                return

            source_item = os.path.relpath(os.path.join(path, database), self.profile_path)
            for row in cursor:
                visit_time = utils.to_datetime(row.get('visit_date'), self.timezone)
                last_visit_time = utils.to_datetime(row.get('last_visit_date'), self.timezone) \
                    if row.get('last_visit_date') else visit_time

                new_row = Firefox.URLItem(
                    profile=self.profile_path,
                    visit_id=row.get('visit_id'),
                    url=row.get('url'),
                    title=row.get('title'),
                    visit_time=visit_time,
                    last_visit_time=last_visit_time,
                    visit_count=row.get('visit_count'),
                    typed_count=row.get('typed'),  # 0/1 flag in Firefox, not a count
                    from_visit=row.get('from_visit'),
                    transition=row.get('visit_type'),
                    hidden=row.get('hidden'),
                    favicon_id=None,
                )
                new_row.row_type = row_type
                new_row.transition_friendly = Firefox._visit_type_friendly(row.get('visit_type'))
                new_row.source_item = source_item
                from_url = row.get('from_url')
                if from_url:
                    new_row.interpretation = f'Referrer: {from_url}'
                results.append(new_row)

            self.artifacts_counts[database] = len(results)
            log.info(f' - Parsed {len(results)} items')
            self.parsed_artifacts.extend(results)
        finally:
            conn.close()

    def get_bookmarks(self, path, database='places.sqlite'):
        # moz_bookmarks stores folders and bookmarks in the same table; split by `type`.
        results = []
        log.info(f'Bookmark items from {database}:')

        conn = self._open(path, database)
        if not conn:
            return

        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT b.id, b.type, b.fk, b.parent, b.title, b.dateAdded, "
                "       b.lastModified, b.guid, p.url "
                "FROM moz_bookmarks b "
                "LEFT JOIN moz_places p ON b.fk = p.id"
            )
            rows = cursor.fetchall()
            folder_titles = {r['id']: (r['title'] or '') for r in rows if r['type'] == BOOKMARK_TYPE_FOLDER}

            for row in rows:
                bm_type = row.get('type')
                parent_folder = folder_titles.get(row.get('parent'), '')
                date_added = utils.to_datetime(row.get('dateAdded'), self.timezone)
                date_modified = utils.to_datetime(row.get('lastModified'), self.timezone) \
                    if row.get('lastModified') else date_added

                if bm_type == BOOKMARK_TYPE_URL:
                    item = Firefox.BookmarkItem(
                        profile=self.profile_path,
                        date_added=date_added,
                        name=row.get('title') or '',
                        url=row.get('url'),
                        parent_folder=parent_folder,
                    )
                    results.append(item)
                elif bm_type == BOOKMARK_TYPE_FOLDER:
                    # Skip the synthetic top-level roots (menu/toolbar/tags/unfiled/mobile).
                    if row.get('parent') in (None, 0):
                        continue
                    item = Firefox.BookmarkFolderItem(
                        profile=self.profile_path,
                        date_added=date_added,
                        date_modified=date_modified,
                        name=row.get('title') or '',
                        parent_folder=parent_folder,
                    )
                    results.append(item)

            self.artifacts_counts['Bookmarks'] = len(results)
            log.info(f' - Parsed {len(results)} items')
            self.parsed_artifacts.extend(results)
        finally:
            conn.close()

    def get_cookies(self, path, database='cookies.sqlite'):
        # Firefox cookies are unencrypted at rest. Emit separate (created)
        # and (accessed) rows like the Chrome parser does.
        results = []
        log.info(f'Cookie items from {database}:')

        conn = self._open(path, database)
        if not conn:
            return

        try:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "SELECT name, value, host, path, expiry, lastAccessed, creationTime, "
                    "       isSecure, isHttpOnly, sameSite "
                    "FROM moz_cookies"
                )
            except Exception as e:
                log.error(f' - Could not query cookies: {e}')
                self.artifacts_counts[database] = 'Failed'
                return

            source_item = os.path.relpath(os.path.join(path, database), self.profile_path)
            zero_ts = datetime.datetime.fromtimestamp(0, datetime.timezone.utc)
            for row in cursor:
                creation = utils.to_datetime(row.get('creationTime'), self.timezone)
                accessed = utils.to_datetime(row.get('lastAccessed'), self.timezone)
                # `expiry` is unix seconds (not PRTime). 0 means session cookie.
                expiry_raw = row.get('expiry')
                if expiry_raw:
                    try:
                        expires = datetime.datetime.fromtimestamp(int(expiry_raw), datetime.timezone.utc)
                        if self.timezone:
                            expires = expires.astimezone(self.timezone)
                    except (OverflowError, OSError, ValueError):
                        expires = None
                else:
                    expires = None

                base = Firefox.CookieItem(
                    profile=self.profile_path,
                    host_key=row.get('host'),
                    path=row.get('path'),
                    name=row.get('name'),
                    value=row.get('value'),
                    creation_utc=creation,
                    last_access_utc=accessed,
                    secure=bool(row.get('isSecure')),
                    http_only=bool(row.get('isHttpOnly')),
                    persistent=bool(expiry_raw),
                    has_expires=bool(expiry_raw),
                    expires_utc=expires,
                )
                host = row.get('host') or ''
                base.url = host.lstrip('.')
                base.source_item = source_item

                created = copy.copy(base)
                created.row_type = 'cookie (created)'
                created.timestamp = creation
                results.append(created)

                if accessed and accessed not in (creation, zero_ts):
                    accessed_row = copy.copy(base)
                    accessed_row.row_type = 'cookie (accessed)'
                    accessed_row.timestamp = accessed
                    results.append(accessed_row)

            self.artifacts_counts['Cookies'] = len(results)
            log.info(f' - Parsed {len(results)} items')
            self.parsed_artifacts.extend(results)
        finally:
            conn.close()

    def get_downloads(self, path, database='places.sqlite'):
        # Firefox 24+ stores downloads as moz_annos rows (`downloads/destinationFileURI`)
        # rather than the legacy downloads.sqlite.
        results = []
        log.info(f'Download items from {database}:')

        conn = self._open(path, database)
        if not conn:
            return

        try:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "SELECT p.url, a.content AS target, a.dateAdded AS start_time, "
                    "       a.lastModified AS end_time, p.id AS place_id "
                    "FROM moz_places p "
                    "JOIN moz_annos a ON p.id = a.place_id "
                    "JOIN moz_anno_attributes aa ON a.anno_attribute_id = aa.id "
                    "WHERE aa.name = 'downloads/destinationFileURI'"
                )
            except Exception as e:
                log.error(f' - Could not query downloads: {e}')
                self.artifacts_counts[database + '_downloads'] = 'Failed'
                return

            source_item = os.path.relpath(os.path.join(path, database), self.profile_path)
            for row in cursor:
                start = utils.to_datetime(row.get('start_time'), self.timezone)
                end = utils.to_datetime(row.get('end_time'), self.timezone) if row.get('end_time') else start
                target = row.get('target') or ''
                if target.startswith('file:///'):
                    try:
                        target = urllib.parse.unquote(target[len('file:///'):])
                    except Exception:
                        pass

                item = Firefox.DownloadItem(
                    profile=self.profile_path,
                    download_id=row.get('place_id'),
                    url=row.get('url'),
                    received_bytes=None,
                    total_bytes=None,
                    state=None,
                    full_path=target,
                    start_time=start,
                    end_time=end,
                    target_path=target,
                    current_path=target,
                )
                item.row_type = 'download'
                item.timestamp = start
                item.value = target or 'Error retrieving download location'
                item.status_friendly = ''
                item.interrupt_reason_friendly = ''
                item.danger_type_friendly = ''
                item.state_friendly = ''
                item.source_item = source_item
                results.append(item)

            self.artifacts_counts[database + '_downloads'] = len(results)
            log.info(f' - Parsed {len(results)} items')
            self.parsed_artifacts.extend(results)
        finally:
            conn.close()

    def get_form_history(self, path, database='formhistory.sqlite'):
        # moz_formhistory rows are values typed into named form fields.
        # Firefox 64+ also tracks timesUsed/firstUsed/lastUsed.
        results = []
        log.info(f'Form history items from {database}:')

        conn = self._open(path, database)
        if not conn:
            return

        try:
            cursor = conn.cursor()
            has_usage = False
            try:
                cursor.execute("PRAGMA table_info(moz_formhistory)")
                cols = {r['name'] for r in cursor.fetchall()}
                has_usage = {'timesUsed', 'firstUsed', 'lastUsed'}.issubset(cols)
            except Exception:
                pass

            try:
                if has_usage:
                    cursor.execute(
                        "SELECT fieldname, value, timesUsed, firstUsed, lastUsed "
                        "FROM moz_formhistory"
                    )
                else:
                    cursor.execute("SELECT fieldname, value FROM moz_formhistory")
            except Exception as e:
                log.error(f' - Could not query form history: {e}')
                self.artifacts_counts[database] = 'Failed'
                return

            source_item = os.path.relpath(os.path.join(path, database), self.profile_path)
            for row in cursor:
                # 'it'/'ts' are internal timestamp-ish fields excluded by the autopsy parser.
                field = (row.get('fieldname') or '').strip()
                if field.lower() in ('it', 'ts'):
                    continue

                if has_usage:
                    first_used = utils.to_datetime(row.get('firstUsed'), self.timezone)
                    item = Firefox.AutofillItem(
                        profile=self.profile_path,
                        date_created=first_used,
                        name=field,
                        value=row.get('value'),
                        count=row.get('timesUsed'),
                    )
                    item.timestamp = first_used
                else:
                    item = Firefox.AutofillItem(
                        profile=self.profile_path,
                        date_created=None,
                        name=field,
                        value=row.get('value'),
                        count=None,
                    )
                    item.timestamp = datetime.datetime.fromtimestamp(0, datetime.timezone.utc)
                item.row_type = 'autofill'
                item.source_item = source_item
                results.append(item)

            self.artifacts_counts[database] = len(results)
            self.artifacts_display['Autofill'] = 'Form history records'
            log.info(f' - Parsed {len(results)} items')
            self.parsed_artifacts.extend(results)
        finally:
            conn.close()

    # moz_perms.permission integer; sourced from nsIPermissionManager.idl.
    _PERMISSION_VALUES = {
        0: 'Unknown',
        1: 'Allow',
        2: 'Deny',
        3: 'Prompt',
        8: 'Allow for session',
    }

    _EXPIRE_TYPES = {
        0: 'Never',
        1: 'At session end',
        2: 'At a specific time',
        3: 'Policy-controlled',
    }

    def get_permissions(self, path, database='permissions.sqlite'):
        # moz_perms timestamps are unix milliseconds (not PRTime).
        results = []
        log.info(f'Permissions items from {database}:')

        conn = self._open(path, database)
        if not conn:
            return

        try:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "SELECT origin, type, permission, expireType, expireTime, "
                    "       modificationTime "
                    "FROM moz_perms"
                )
            except Exception as e:
                log.error(f' - Could not query permissions: {e}')
                self.artifacts_counts[database] = 'Failed'
                return

            source_item = os.path.relpath(os.path.join(path, database), self.profile_path)
            for row in cursor:
                # ms -> PRTime us so to_datetime hits its 16-digit branch.
                mod_ms = row.get('modificationTime') or 0
                mod_time = utils.to_datetime(mod_ms * 1000, self.timezone) if mod_ms else \
                    datetime.datetime.fromtimestamp(0, datetime.timezone.utc)

                perm_value = row.get('permission')
                perm_label = self._PERMISSION_VALUES.get(perm_value, f'Unknown ({perm_value})')
                expire_type = row.get('expireType')
                expire_label = self._EXPIRE_TYPES.get(expire_type, f'Unknown ({expire_type})')

                exp_ms = row.get('expireTime') or 0
                if expire_type == 2 and exp_ms:
                    expires = utils.to_datetime(exp_ms * 1000, self.timezone)
                    interpretation = f'{expire_label}: {expires.isoformat()}'
                else:
                    interpretation = expire_label

                item = Firefox.SiteSetting(
                    profile=self.profile_path,
                    url=row.get('origin'),
                    timestamp=mod_time,
                    key=row.get('type'),
                    value=perm_label,
                    interpretation=interpretation,
                )
                item.row_type = 'site setting'
                item.source_item = source_item
                item.name = row.get('type')
                item.value = perm_label
                results.append(item)

            self.artifacts_counts['Permissions'] = len(results)
            log.info(f' - Parsed {len(results)} items')
            self.parsed_artifacts.extend(results)
        finally:
            conn.close()

    # SiteSecurityServiceState.bin record layout (DataStorage format):
    # 286-byte fixed slot: hash[0:2], flags[2:4], key[4:260] (ASCII, null-padded,
    # 2-char persistence prefix + hostname), value[260:] as `<expiry_ms>,<state>,<sub>`.
    _HSTS_RECORD_SIZE = 286
    _HSTS_KEY_OFFSET = 4
    _HSTS_KEY_MAX = 256
    _HSTS_VALUE_OFFSET = 260
    _HSTS_VALUE_RE = re.compile(rb'(\d+),(\d+),(\d+)')

    def get_hsts(self, path, filename='SiteSecurityServiceState.bin'):
        results = []
        full_path = os.path.join(path, filename)
        log.info(f'HSTS items from {filename}:')
        if not os.path.isfile(full_path):
            log.info(f' - {full_path} not present')
            return

        try:
            with open(full_path, 'rb') as fh:
                blob = fh.read()
        except OSError as e:
            log.error(f' - Could not read {full_path}: {e}')
            self.artifacts_counts['HSTS'] = 'Failed'
            return

        source_item = os.path.relpath(full_path, self.profile_path)
        n_records = len(blob) // self._HSTS_RECORD_SIZE
        for i in range(n_records):
            rec = blob[i * self._HSTS_RECORD_SIZE:(i + 1) * self._HSTS_RECORD_SIZE]

            key_bytes = rec[self._HSTS_KEY_OFFSET:self._HSTS_KEY_OFFSET + self._HSTS_KEY_MAX]
            key_bytes = key_bytes.split(b'\x00', 1)[0]
            if len(key_bytes) < 3:
                continue
            try:
                key_str = key_bytes.decode('ascii')
            except UnicodeDecodeError:
                continue
            # 'P' prefix = persistent-storage bucket holding HSTS pins; skip others.
            if not key_str or key_str[0] != 'P':
                continue
            hostname = key_str[2:] if len(key_str) > 2 else key_str

            value_bytes = rec[self._HSTS_VALUE_OFFSET:]
            m = self._HSTS_VALUE_RE.search(value_bytes)
            if not m:
                continue
            expiry_ms = int(m.group(1))
            state = int(m.group(2))
            include_subdomains = int(m.group(3))

            # state 0 = unset/deleted; skip.
            if state == 0:
                continue

            try:
                expiry_dt = datetime.datetime.fromtimestamp(expiry_ms / 1000.0,
                                                             datetime.timezone.utc)
                if self.timezone:
                    expiry_dt = expiry_dt.astimezone(self.timezone)
                expiry_str = expiry_dt.isoformat()
            except (OSError, OverflowError, ValueError):
                expiry_str = str(expiry_ms)

            interpretation_parts = [f'expires {expiry_str}']
            if include_subdomains:
                interpretation_parts.append('includeSubdomains=true')
            else:
                interpretation_parts.append('includeSubdomains=false')
            if state == 2:
                interpretation_parts.append('state=2 (HSTS via header + includeSubdomains)')
            else:
                interpretation_parts.append(f'state={state}')

            # No creation ts in HSTS records; use file mtime as a soft observed-at.
            try:
                observed = datetime.datetime.fromtimestamp(
                    os.path.getmtime(full_path), datetime.timezone.utc)
                if self.timezone:
                    observed = observed.astimezone(self.timezone)
            except OSError:
                observed = datetime.datetime.fromtimestamp(0, datetime.timezone.utc)

            item = Firefox.SiteSetting(
                profile=self.profile_path,
                url=hostname,
                timestamp=observed,
                key='HSTS',
                value='Enforced',
                interpretation='; '.join(interpretation_parts),
            )
            item.row_type = 'site setting (HSTS)'
            item.name = 'HSTS'
            item.value = 'Enforced'
            item.source_item = source_item
            results.append(item)

        self.artifacts_counts['HSTS'] = len(results)
        log.info(f' - Parsed {len(results)} items')
        self.parsed_artifacts.extend(results)

    def process(self):
        try:
            input_listing = os.listdir(self.profile_path)
        except OSError as e:
            log.error(f'Unable to read Firefox profile {self.profile_path}: {e}')
            return

        if 'places.sqlite' in input_listing:
            self.determine_version(self.profile_path, 'places.sqlite')
            print((self.format_processing_output(
                f'Detected {self.browser_name} schema', self.display_version or 'unknown')))

            self.get_history(self.profile_path, 'places.sqlite')
            self.artifacts_display['places.sqlite'] = 'URL records'
            print((self.format_processing_output(
                'URL records', self.artifacts_counts.get('places.sqlite', 0))))

            self.get_bookmarks(self.profile_path, 'places.sqlite')
            self.artifacts_display['Bookmarks'] = 'Bookmark records'
            print((self.format_processing_output(
                'Bookmark records', self.artifacts_counts.get('Bookmarks', 0))))

            self.get_downloads(self.profile_path, 'places.sqlite')
            self.artifacts_display['places.sqlite_downloads'] = 'Download records'
            print((self.format_processing_output(
                'Download records', self.artifacts_counts.get('places.sqlite_downloads', 0))))

        if 'cookies.sqlite' in input_listing:
            self.get_cookies(self.profile_path, 'cookies.sqlite')
            self.artifacts_display['Cookies'] = 'Cookie records'
            print((self.format_processing_output(
                'Cookie records', self.artifacts_counts.get('Cookies', 0))))

        if 'formhistory.sqlite' in input_listing:
            self.get_form_history(self.profile_path, 'formhistory.sqlite')
            print((self.format_processing_output(
                'Form history records', self.artifacts_counts.get('formhistory.sqlite', 0))))

        if 'permissions.sqlite' in input_listing:
            self.get_permissions(self.profile_path, 'permissions.sqlite')
            self.artifacts_display['Permissions'] = 'Permission records'
            print((self.format_processing_output(
                'Permission records', self.artifacts_counts.get('Permissions', 0))))

        if 'SiteSecurityServiceState.bin' in input_listing:
            self.get_hsts(self.profile_path, 'SiteSecurityServiceState.bin')
            self.artifacts_display['HSTS'] = 'HSTS records'
            print((self.format_processing_output(
                'HSTS records', self.artifacts_counts.get('HSTS', 0))))

        self.parsed_artifacts.sort()

    class URLItem(WebBrowser.URLItem):
        pass

    class BookmarkItem(WebBrowser.BookmarkItem):
        pass

    class BookmarkFolderItem(WebBrowser.BookmarkFolderItem):
        pass

    class CookieItem(WebBrowser.CookieItem):
        pass

    class DownloadItem(WebBrowser.DownloadItem):
        pass

    class AutofillItem(WebBrowser.AutofillItem):
        pass

    class CacheItem(WebBrowser.CacheItem):
        pass

    class SiteSetting(WebBrowser.SiteSetting):
        pass

    class LoginItem(WebBrowser.LoginItem):
        pass

    class BrowserExtension(WebBrowser.BrowserExtension):
        pass

    class SessionItem(WebBrowser.SessionItem):
        pass

    class LocalStorageItem(WebBrowser.LocalStorageItem):
        pass

    class IndexedDBItem(WebBrowser.IndexedDBItem):
        pass
