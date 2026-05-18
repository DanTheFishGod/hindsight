# -*- coding: utf-8 -*-
import copy
import datetime
import json
import logging
import os
import re
import struct
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

# Matches `user_pref("key", value);` lines in prefs.js.
_PREFS_LINE_RE = re.compile(
    r'^user_pref\(\s*"([^"]+)"\s*,\s*(.*?)\s*\)\s*;\s*$'
)

# Forensically interesting Firefox preferences, grouped for the XLSX sheet.
INTERESTING_PREFS = [
    ('Identity & Account', [
        ('services.sync.username', 'Firefox Account email (logged-in user)'),
        ('services.sync.lastSync', 'Last Firefox Sync time (unix seconds)'),
        ('services.sync.numClients', 'Number of devices linked to this account'),
        ('identity.fxaccounts.lastSignedInUserHash', 'Hashed identity of last FxA user'),
    ]),
    ('Startup & Homepage', [
        ('browser.startup.homepage', 'Configured homepage URL(s)'),
        ('browser.startup.page', 'What to show on startup (1=homepage, 3=last session)'),
        ('browser.newtabpage.enabled', 'New tab page enabled'),
        ('browser.startup.lastColdStartupCheck', 'Last cold-start check (unix seconds)'),
        ('app.installation.timestamp', 'Firefox installation timestamp (PRTime)'),
    ]),
    ('Downloads', [
        ('browser.download.lastDir', 'Last directory used to save a download'),
        ('browser.download.dir', 'Configured default download directory'),
        ('browser.download.folderList',
         'Default download folder type (0=Desktop, 1=Downloads, 2=custom)'),
        ('browser.download.useDownloadDir', 'Skip the Save As prompt'),
        ('browser.download.alwaysOpenPanel', 'Always open the downloads panel'),
    ]),
    ('Network & Proxy', [
        ('network.proxy.type',
         'Proxy mode (0=none, 1=manual, 2=PAC, 4=auto-detect, 5=system)'),
        ('network.proxy.http', 'Manual HTTP proxy host'),
        ('network.proxy.http_port', 'Manual HTTP proxy port'),
        ('network.proxy.ssl', 'Manual HTTPS proxy host'),
        ('network.proxy.socks', 'Manual SOCKS proxy host'),
        ('network.proxy.no_proxies_on', 'Domains bypassing the proxy'),
        ('network.proxy.autoconfig_url', 'PAC file URL'),
        ('network.trr.mode', 'DNS-over-HTTPS mode'),
        ('network.trr.uri', 'DNS-over-HTTPS resolver URL'),
    ]),
    ('Privacy & Tracking Protection', [
        ('privacy.donottrackheader.enabled', 'Send Do-Not-Track header'),
        ('privacy.globalprivacycontrol.enabled', 'Send Global Privacy Control'),
        ('privacy.trackingprotection.enabled', 'Enhanced Tracking Protection'),
        ('privacy.history.custom', 'Using custom history settings'),
        ('privacy.sanitize.sanitizeOnShutdown', 'Clear history on shutdown'),
        ('privacy.clearOnShutdown.history', 'Clear browsing history on shutdown'),
        ('privacy.clearOnShutdown.cookies', 'Clear cookies on shutdown'),
        ('privacy.clearOnShutdown.downloads', 'Clear download list on shutdown'),
        ('privacy.clearOnShutdown.formdata', 'Clear form data on shutdown'),
    ]),
    ('Search & Region', [
        ('browser.search.region', 'Country code used to pick default engines'),
        ('browser.search.suggest.enabled', 'Show search suggestions'),
        ('browser.urlbar.placeholderName', 'Active default search engine name'),
        ('browser.search.defaultenginename', 'Configured default search engine'),
        ('browser.search.lastModifiedTopic', 'Last search-config modification (PRTime)'),
        ('intl.accept_languages', 'Languages sent in HTTP Accept-Language'),
    ]),
    ('Passwords & Autofill', [
        ('signon.rememberSignons', 'Save logins and passwords for websites'),
        ('signon.management.page.breach-alerts.enabled', 'Show login breach alerts'),
        ('signon.autofillForms', 'Autofill saved usernames/passwords'),
        ('browser.formfill.enable', 'Save form entries'),
        ('extensions.formautofill.addresses.enabled', 'Save and fill addresses'),
        ('extensions.formautofill.creditCards.enabled', 'Save and fill credit cards'),
    ]),
    ('Telemetry & Updates', [
        ('app.update.auto', 'Apply updates automatically'),
        ('app.update.background.lastInstalledTaskVersion', 'Last background-update task version'),
        ('toolkit.telemetry.enabled', 'Send telemetry to Mozilla'),
        ('datareporting.healthreport.uploadEnabled', 'Send Health Report data'),
        ('toolkit.telemetry.lastUpdate', 'Last telemetry upload (unix seconds)'),
    ]),
    ('Containers & Profiles', [
        ('privacy.userContext.enabled', 'Container tabs enabled'),
        ('extensions.installedFromFXA', 'Add-ons installed via FxA'),
        ('browser.engagement.profileCount', 'Number of profiles on this install'),
    ]),
]


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

    @staticmethod
    def _parse_prefs_value(raw):
        if raw == 'true':
            return True
        if raw == 'false':
            return False
        if raw == 'null':
            return None
        try:
            return json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
                return raw[1:-1].replace('\\\\', '\\').replace('\\"', '"')
            return raw

    def get_preferences(self, path, prefs_file='prefs.js'):
        full_path = os.path.join(path, prefs_file)
        log.info(f'Preferences from {prefs_file}:')
        if not os.path.isfile(full_path):
            log.info(f' - {full_path} not present')
            return

        all_prefs = {}
        try:
            with open(full_path, 'r', encoding='utf-8', errors='replace') as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith('//'):
                        continue
                    m = _PREFS_LINE_RE.match(line)
                    if not m:
                        continue
                    name = m.group(1)
                    value = self._parse_prefs_value(m.group(2))
                    all_prefs[name] = value
        except OSError as e:
            log.error(f' - Could not read {full_path}: {e}')
            self.artifacts_counts['prefs.js'] = 'Failed'
            return

        results = []
        seen = set()

        for group_name, entries in INTERESTING_PREFS:
            results.append({'group': group_name, 'name': None, 'value': None, 'description': None})
            for key, description in entries:
                if key in all_prefs:
                    value = all_prefs[key]
                    seen.add(key)
                else:
                    value = '<not set>'
                results.append({
                    'group': None,
                    'name': key,
                    'value': value if not isinstance(value, (dict, list)) else json.dumps(value),
                    'description': description,
                })

        # Long tail: every other set pref, grouped by dotted prefix.
        remaining = sorted(k for k in all_prefs if k not in seen)
        if remaining:
            results.append({'group': 'All Other Preferences', 'name': None, 'value': None, 'description': None})
            current_prefix = None
            for key in remaining:
                prefix = key.split('.', 1)[0]
                if prefix != current_prefix:
                    results.append({
                        'group': f'{prefix}.*', 'name': None, 'value': None, 'description': None,
                    })
                    current_prefix = prefix
                value = all_prefs[key]
                results.append({
                    'group': None,
                    'name': key,
                    'value': value if not isinstance(value, (dict, list)) else json.dumps(value),
                    'description': None,
                })

        pref_count = sum(1 for r in results if r['name'] is not None)
        self.artifacts_counts['Preferences'] = pref_count

        profile_folder = os.path.basename(path.rstrip(os.sep)) or 'profile'
        presentation = {
            'title': f'Preferences ({profile_folder})',
            'columns': [
                {'display_name': 'Group', 'data_name': 'group', 'display_width': 24},
                {'display_name': 'Setting Name', 'data_name': 'name', 'display_width': 50},
                {'display_name': 'Value', 'data_name': 'value', 'display_width': 50},
                {'display_name': 'Description', 'data_name': 'description', 'display_width': 60},
            ],
        }
        self.preferences.append({'data': results, 'presentation': presentation})
        log.info(f' - Parsed {pref_count} preferences')

    def get_logins(self, path, filename='logins.json'):
        # Username and password values are NSS/3DES-CBC encrypted (key wrapped in key4.db).
        # We do NOT decrypt; we surface unencrypted metadata + three timestamp rows per login.
        results = []
        full_path = os.path.join(path, filename)
        log.info(f'Saved logins from {filename}:')
        if not os.path.isfile(full_path):
            log.info(f' - {full_path} not present')
            return

        try:
            with open(full_path, 'r', encoding='utf-8', errors='replace') as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            log.error(f' - Could not read {full_path}: {e}')
            self.artifacts_counts['Logins'] = 'Failed'
            return

        source_item = os.path.relpath(full_path, self.profile_path)
        zero_ts = datetime.datetime.fromtimestamp(0, datetime.timezone.utc)

        for login in data.get('logins', []):
            hostname = login.get('hostname') or login.get('formSubmitURL') or ''
            form_url = login.get('formSubmitURL') or ''
            http_realm = login.get('httpRealm') or ''
            user_field = login.get('usernameField') or ''
            pass_field = login.get('passwordField') or ''
            guid = login.get('guid') or ''
            times_used = login.get('timesUsed', 0)
            ever_synced = login.get('everSynced')

            created_ms = login.get('timeCreated') or 0
            last_used_ms = login.get('timeLastUsed') or 0
            pw_changed_ms = login.get('timePasswordChanged') or 0

            def _to_dt(ms):
                if not ms:
                    return zero_ts
                return utils.to_datetime(ms * 1000, self.timezone)

            interp_parts = [
                f'GUID: {guid}',
                f'usernameField: {user_field!r}',
                f'passwordField: {pass_field!r}',
                f'timesUsed: {times_used}',
            ]
            if form_url and form_url != hostname:
                interp_parts.append(f'formSubmitURL: {form_url}')
            if http_realm:
                interp_parts.append(f'httpRealm: {http_realm}')
            if ever_synced is not None:
                interp_parts.append(f'everSynced: {ever_synced}')
            interpretation = '; '.join(interp_parts)

            for ts_ms, row_label, ts_name in [
                (created_ms, 'login (created)', 'timeCreated'),
                (last_used_ms, 'login (last used)', 'timeLastUsed'),
                (pw_changed_ms, 'login (password changed)', 'timePasswordChanged'),
            ]:
                if not ts_ms:
                    continue
                # Drop the duplicate row when the timestamp matches creation.
                if row_label.endswith('changed)') and ts_ms == created_ms:
                    continue
                if row_label.endswith('last used)') and ts_ms == created_ms:
                    continue

                item = Firefox.LoginItem(
                    profile=self.profile_path,
                    date_created=_to_dt(ts_ms),
                    url=hostname,
                    name=user_field or '(username field)',
                    value='<encrypted>',
                    count=times_used,
                    interpretation=f'{ts_name}; {interpretation}',
                )
                item.row_type = row_label
                item.timestamp = _to_dt(ts_ms)
                item.source_item = source_item
                results.append(item)

        for guid in data.get('potentiallyVulnerablePasswords', []) or []:
            item = Firefox.LoginItem(
                profile=self.profile_path,
                date_created=zero_ts,
                url='<aggregate>',
                name='potentiallyVulnerablePassword',
                value=guid if isinstance(guid, str) else json.dumps(guid),
                count=None,
                interpretation='Firefox flagged this saved login GUID as potentially exposed in a known breach',
            )
            item.row_type = 'login (vulnerable)'
            item.timestamp = zero_ts
            item.source_item = source_item
            results.append(item)

        self.artifacts_counts['Logins'] = len(results)
        log.info(f' - Parsed {len(results)} items')
        self.parsed_artifacts.extend(results)

    @staticmethod
    def _snappy_decompress(src):
        # Snappy "raw" block: varint(decompressed_len) + tag-prefixed literal/copy records.
        # Pure-Python so we don't take a C-extension dep on python-snappy.
        n = len(src)
        i = 0
        decompressed_len = 0
        shift = 0
        while i < n:
            b = src[i]
            i += 1
            decompressed_len |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
            if shift >= 32:
                raise ValueError('snappy: decompressed length varint too long')

        out = bytearray()
        while i < n and len(out) < decompressed_len:
            tag = src[i]
            i += 1
            tag_type = tag & 0x03
            if tag_type == 0:
                # Literal; top 6 bits = length-1 when < 60, else 60..63 = N-59 extra length bytes.
                length = (tag >> 2) + 1
                if length > 60:
                    extra = length - 60
                    length = 0
                    for j in range(extra):
                        length |= src[i + j] << (8 * j)
                    length += 1
                    i += extra
                out += src[i:i + length]
                i += length
            elif tag_type == 1:
                length = ((tag >> 2) & 0x07) + 4
                offset = ((tag & 0xE0) << 3) | src[i]
                i += 1
                for _ in range(length):
                    out.append(out[-offset])
            elif tag_type == 2:
                length = (tag >> 2) + 1
                offset = src[i] | (src[i + 1] << 8)
                i += 2
                for _ in range(length):
                    out.append(out[-offset])
            else:
                length = (tag >> 2) + 1
                offset = (src[i] | (src[i + 1] << 8) |
                          (src[i + 2] << 16) | (src[i + 3] << 24))
                i += 4
                for _ in range(length):
                    out.append(out[-offset])

        return bytes(out)

    # 8-byte magic for Mozilla's mozLz40 wrapper: magic + uint32 LE decompressed size + LZ4 block.
    _MOZLZ4_MAGIC = b'mozLz40\x00'

    @staticmethod
    def _lz4_block_decompress(src, dest_size):
        # LZ4 block format: token byte (hi=literal_len, lo=match_len), optional 0xff
        # overflow chains for both lengths, literal bytes, 2-byte LE match offset.
        out = bytearray()
        i = 0
        n = len(src)
        while i < n:
            token = src[i]
            i += 1
            literal_len = token >> 4
            if literal_len == 15:
                while i < n:
                    b = src[i]
                    i += 1
                    literal_len += b
                    if b != 0xFF:
                        break
            out.extend(src[i:i + literal_len])
            i += literal_len
            if i >= n:
                break
            if i + 2 > n:
                break
            offset = src[i] | (src[i + 1] << 8)
            i += 2
            if offset == 0:
                break
            match_len = (token & 0x0F) + 4
            if (token & 0x0F) == 15:
                while i < n:
                    b = src[i]
                    i += 1
                    match_len += b
                    if b != 0xFF:
                        break
            # Byte-by-byte copy so overlapping windows (RLE-style runs) work.
            for _ in range(match_len):
                out.append(out[-offset])
            if len(out) >= dest_size:
                break
        return bytes(out)

    @classmethod
    def _decompress_jsonlz4(cls, path):
        try:
            with open(path, 'rb') as fh:
                magic = fh.read(8)
                if magic != cls._MOZLZ4_MAGIC:
                    log.warning(f' - {path}: not a mozLz40 file (magic={magic!r})')
                    return None
                size_bytes = fh.read(4)
                if len(size_bytes) != 4:
                    return None
                dest_size = struct.unpack('<I', size_bytes)[0]
                payload = fh.read()
        except OSError as e:
            log.warning(f' - Could not read {path}: {e}')
            return None
        try:
            return cls._lz4_block_decompress(payload, dest_size)
        except Exception as e:
            log.warning(f' - LZ4 decompress failed for {path}: {e}')
            return None

    # signedState integers from mozapps/extensions/AddonManager.sys.mjs.
    _ADDON_SIGNED_STATES = {
        -2: 'Broken',
        -1: 'Unknown',
        0: 'Missing',
        1: 'Preliminary',
        2: 'Signed',
        3: 'System',
        4: 'Privileged',
    }

    def get_extensions(self, path, filename='extensions.json'):
        full_path = os.path.join(path, filename)
        log.info(f'Installed extensions from {filename}:')
        if not os.path.isfile(full_path):
            log.info(f' - {full_path} not present')
            return

        try:
            with open(full_path, 'r', encoding='utf-8', errors='replace') as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            log.error(f' - Could not read {full_path}: {e}')
            self.artifacts_counts['Extensions'] = 'Failed'
            return

        results = []
        for addon in data.get('addons', []):
            ext_id = addon.get('id') or ''
            version = addon.get('version') or ''
            addon_type = addon.get('type') or ''
            active = addon.get('active')
            user_disabled = addon.get('userDisabled')
            app_disabled = addon.get('appDisabled')
            signed_state_raw = addon.get('signedState')
            signed_state = self._ADDON_SIGNED_STATES.get(
                signed_state_raw, f'Unknown ({signed_state_raw})')
            source_uri = addon.get('sourceURI') or ''
            location = addon.get('location') or ''
            on_disk_path = addon.get('path') or ''
            root_uri = addon.get('rootURI') or ''
            install_ms = addon.get('installDate') or 0
            update_ms = addon.get('updateDate') or 0

            default_locale = addon.get('defaultLocale') or {}
            name = default_locale.get('name') or ext_id
            description = default_locale.get('description') or ''

            user_perms = addon.get('userPermissions') or {}
            perms_list = list(user_perms.get('permissions', []))
            origins_list = list(user_perms.get('origins', []))
            permissions_str = json.dumps({
                'permissions': perms_list, 'origins': origins_list
            }) if (perms_list or origins_list) else ''

            # Compact manifest summary; full extension manifests can be 500KB.
            manifest_summary = {
                'id': ext_id,
                'version': version,
                'type': addon_type,
                'active': active,
                'userDisabled': user_disabled,
                'appDisabled': app_disabled,
                'signedState': signed_state,
                'sourceURI': source_uri,
                'installLocation': location,
                'path': on_disk_path,
                'rootURI': root_uri,
                'installDate': install_ms,
                'updateDate': update_ms,
            }

            results.append(Firefox.BrowserExtension(
                profile=self.profile_path,
                ext_id=ext_id,
                name=name,
                description=description,
                version=version,
                permissions=permissions_str,
                manifest=json.dumps(manifest_summary),
            ))

        self.artifacts_counts['Extensions'] = len(results)
        log.info(f' - Parsed {len(results)} items')

        presentation = {
            'title': 'Installed Extensions',
            'columns': [
                {'display_name': 'Extension Name', 'data_name': 'name', 'display_width': 26},
                {'display_name': 'Description', 'data_name': 'description', 'display_width': 60},
                {'display_name': 'Version', 'data_name': 'version', 'display_width': 10},
                {'display_name': 'App ID', 'data_name': 'ext_id', 'display_width': 40},
                {'display_name': 'Profile Folder', 'data_name': 'profile', 'display_width': 30},
                {'display_name': 'Permissions', 'data_name': 'permissions', 'display_width': 45},
                {'display_name': 'Manifest', 'data_name': 'manifest', 'display_width': 80},
            ],
        }
        self.installed_extensions = {'data': results, 'presentation': presentation}

    def _walk_sessionstore(self, doc, source_label, source_item, results):
        # Emit one SessionItem per navigation entry so the timeline shows tab
        # history rather than just the currently selected URL.
        zero_ts = datetime.datetime.fromtimestamp(0, datetime.timezone.utc)

        def _emit_entries(entries, window_idx, tab_idx, selected_index,
                           tab_last_accessed_ms, row_label):
            for nav_idx, entry in enumerate(entries or []):
                url = entry.get('url')
                if not url:
                    continue
                title = entry.get('title') or ''
                referrer = entry.get('referrer') or entry.get('originalURI') or ''
                # Only the selected nav-entry gets a real timestamp; others sort to epoch 0.
                if nav_idx + 1 == selected_index and tab_last_accessed_ms:
                    ts = utils.to_datetime(tab_last_accessed_ms * 1000, self.timezone)
                else:
                    ts = zero_ts

                item = Firefox.SessionItem(
                    profile=self.profile_path,
                    url=url,
                    title=title,
                    timestamp=ts,
                    session_id=f'win{window_idx}.tab{tab_idx}',
                    nav_index=nav_idx,
                    referrer_url=referrer,
                    original_request_url=entry.get('originalURI'),
                    source_path=source_item,
                )
                item.row_type = row_label
                item.value = ''
                item.transition_type = (
                    'selected' if nav_idx + 1 == selected_index else 'history'
                )
                results.append(item)

        for w_idx, window in enumerate(doc.get('windows', []) or []):
            for t_idx, tab in enumerate(window.get('tabs', []) or []):
                _emit_entries(
                    tab.get('entries', []), w_idx, t_idx,
                    tab.get('index'), tab.get('lastAccessed'),
                    f'session (open tab, {source_label})')

            for c_idx, closed in enumerate(window.get('_closedTabs', []) or []):
                state = closed.get('state') or {}
                ts_ms = closed.get('closedAt')
                ts = utils.to_datetime(ts_ms * 1000, self.timezone) \
                    if ts_ms else zero_ts
                for nav_idx, entry in enumerate(state.get('entries', []) or []):
                    url = entry.get('url')
                    if not url:
                        continue
                    item = Firefox.SessionItem(
                        profile=self.profile_path,
                        url=url,
                        title=entry.get('title') or '',
                        timestamp=ts,
                        session_id=f'win{w_idx}.closed{c_idx}',
                        nav_index=nav_idx,
                        referrer_url=entry.get('referrer') or '',
                        original_request_url=entry.get('originalURI'),
                        source_path=source_item,
                    )
                    item.row_type = f'session (closed tab, {source_label})'
                    item.value = ''
                    item.transition_type = 'closed'
                    results.append(item)

        for cw_idx, cwin in enumerate(doc.get('_closedWindows', []) or []):
            for t_idx, tab in enumerate(cwin.get('tabs', []) or []):
                ts_ms = tab.get('lastAccessed')
                ts = utils.to_datetime(ts_ms * 1000, self.timezone) \
                    if ts_ms else zero_ts
                for nav_idx, entry in enumerate(tab.get('entries', []) or []):
                    url = entry.get('url')
                    if not url:
                        continue
                    item = Firefox.SessionItem(
                        profile=self.profile_path,
                        url=url,
                        title=entry.get('title') or '',
                        timestamp=ts,
                        session_id=f'closedwin{cw_idx}.tab{t_idx}',
                        nav_index=nav_idx,
                        referrer_url=entry.get('referrer') or '',
                        original_request_url=entry.get('originalURI'),
                        source_path=source_item,
                    )
                    item.row_type = f'session (closed window, {source_label})'
                    item.value = ''
                    item.transition_type = 'closed'
                    results.append(item)

    def get_sessionstore(self, path):
        # sessionstore-backups/ rotates: recovery.jsonlz4 (live), recovery.baklz4
        # (previous live), previous.jsonlz4 (last clean close), upgrade.jsonlz4-<ts>
        # (per-upgrade snapshot, often preserves state from older Firefox versions).
        results = []
        log.info('Sessionstore items:')

        candidates = []
        primary = os.path.join(path, 'sessionstore.jsonlz4')
        if os.path.isfile(primary):
            candidates.append((primary, 'current'))

        backups_dir = os.path.join(path, 'sessionstore-backups')
        if os.path.isdir(backups_dir):
            for name in sorted(os.listdir(backups_dir)):
                full = os.path.join(backups_dir, name)
                if not os.path.isfile(full):
                    continue
                if not (name.endswith('.jsonlz4') or name.endswith('.baklz4')
                        or '.jsonlz4-' in name):
                    continue
                candidates.append((full, name))

        if not candidates:
            log.info(' - No sessionstore files found')
            return

        for full_path, label in candidates:
            try:
                raw = self._decompress_jsonlz4(full_path)
                if raw is None:
                    continue
                doc = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                log.warning(f' - Could not parse {full_path}: {e}')
                continue
            source_item = os.path.relpath(full_path, self.profile_path)
            before = len(results)
            self._walk_sessionstore(doc, label, source_item, results)
            log.info(f' - {label}: parsed {len(results) - before} entries')

        self.artifacts_counts['Sessions'] = len(results)
        log.info(f' - Parsed {len(results)} items total')
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

        if 'prefs.js' in input_listing:
            self.get_preferences(self.profile_path, 'prefs.js')
            self.artifacts_display['Preferences'] = 'Preference items'
            print((self.format_processing_output(
                'Preference items', self.artifacts_counts.get('Preferences', 0))))

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

        if 'logins.json' in input_listing:
            self.get_logins(self.profile_path, 'logins.json')
            self.artifacts_display['Logins'] = 'Saved login records'
            print((self.format_processing_output(
                'Saved login records', self.artifacts_counts.get('Logins', 0))))

        if 'extensions.json' in input_listing:
            self.get_extensions(self.profile_path, 'extensions.json')
            self.artifacts_display['Extensions'] = 'Installed Extensions'
            print((self.format_processing_output(
                'Installed Extensions', self.artifacts_counts.get('Extensions', 0))))

        if 'sessionstore.jsonlz4' in input_listing or 'sessionstore-backups' in input_listing:
            self.get_sessionstore(self.profile_path)
            self.artifacts_display['Sessions'] = 'Session (tab) records'
            print((self.format_processing_output(
                'Session (tab) records', self.artifacts_counts.get('Sessions', 0))))

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
