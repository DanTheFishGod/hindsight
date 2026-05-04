# -*- coding: utf-8 -*-
import datetime
import logging
import os

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
