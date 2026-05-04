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
