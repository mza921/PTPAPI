"""Represent a movie"""
import re
import logging
import os.path
from datetime import datetime

from bs4 import BeautifulSoup as bs4 # pylint: disable=import-error

from session import session
from torrent import Torrent
from error import PTPAPIException

LOGGER = logging.getLogger(__name__)

class Movie(object):
    """A class representing a movie"""
    def __init__(self, ID=None, data=None):
        self.torrents = []
        self.key_finder = {
            'json': [
                'ImdbId',
                'ImdbRating',
                'ImdbVoteCount',
                'Torrents',
                'CoverImage'
            ],
            'html': [
                'Title',
                'Year',
                'Cover',
                'Tags',
                'Directors'
            ],
            'inferred': [
                'Link',
                'Id',
                'GroupId'
            ]
        }

        if data:
            self.data = data
            self.conv_json_torrents()
            self.ID = data['GroupId'] # pylint: disable=invalid-name
        elif ID:
            self.ID = ID
            self.data = {}
        else:
            raise PTPAPIException("Could not load necessary data for Movie class")

    def __repr__(self):
        return "<ptpapi.Movie ID %s>" % self.ID

    def __str__(self):
        return "<ptpapi.Movie ID %s>" % self.ID

    def __getitem__(self, name):
        if name not in self.data or self.data[name] is None:
            for key, val in self.key_finder.iteritems():
                if name in val:
                    getattr(self, "load_%s_data" % key)()
        return self.data[name]

    def items(self):
        """Passthru function for underlying dict"""
        return self.data.items()

    def __setitem__(self, key, value):
        self.data[key] = value

    def load_inferred_data(self):
        self.data['Id'] = self.ID
        self.data['GroupId'] = self.ID
        self.data['Link'] = 'https://passthepopcorn.me/torrents.php?id=' + self.ID

    def load_json_data(self):
        """Load movie JSON data"""
        self.data.update(session.base_get("torrents.php",
                                          params={'id': self.ID,
                                                  'json': '1'}).json())
        if 'ImdbId' not in self.data:
            self.data['ImdbId'] = ''
        if 'Directors' not in self.data:
            self.data['Directors'] = []
        self.conv_json_torrents()

    def conv_json_torrents(self):
        """Util function to normalize data"""
        if self.data['Torrents']:
            torrents = self.data['Torrents']
            for t in torrents:
                if 'RemasterTitle' not in t:
                    t['RemasterTitle'] = ''
            self.data['Torrents'] = [Torrent(data=t) for t in torrents]

    def load_html_data(self):
        """Scrape all data from a movie's HTML page"""
        soup = bs4(session.base_get("torrents.php", params={'id': self.ID}).text, "html.parser")
        self.data['Cover'] = soup.find('img', class_='sidebar-cover-image')['src']
        # Title and Year
        match = re.match(r'(.*) \[(\d{4})\]', soup.find('h2', class_='page__title').encode_contents())
        self.data['Title'] = match.group(1)
        self.data['Year'] = match.group(2)
        # Genre tags
        self.data['Tags'] = []
        for tagbox in soup.find_all('div', class_="box_tags"):
            for tag in tagbox.find_all("li"):
                self.data['Tags'].append(tag.find('a').string)
        self.data['Directors'] = []
        for director in soup.find('h2', class_='page__title').find_all('a', class_='artist-info-link'):
            self.data['Directors'].append({'Name': director.string.strip()})
        # File list & trumpability
        for tor in self['Torrents']:
            # Get file list
            filediv = soup.find("div", id="files_%s" % tor.ID)
            tor.data['Filelist'] = {}
            basepath = re.match(r'\/(.*)\/', filediv.find("thead").find_all("div")[1].get_text()).group(1)
            for elem in filediv.find("tbody").find_all("tr"):
                bytesize = elem("td")[1]("span")[0]['title'].replace(",", "").replace(' bytes', '')
                filepath = os.path.join(basepath, elem("td")[0].string)
                tor.data['Filelist'][filepath] = bytesize
            # Check if trumpable
            if soup.find(id="trumpable_%s" % tor.ID):
                tor.data['Trumpable'] = [s.get_text() for s in soup.find(id="trumpable_%s" % tor.ID).find_all('span')]
            else:
                tor.data['Trumpable'] = []

    def best_match(self, profile):
        """A function to pull the best match of a movie, based on a human-readable filter

        :param profile: a filter string
        :rtype: The best matching movie, or None"""
        # We're going to emulate what.cd's collector option
        profiles = profile.lower().split(',')
        current_sort = None
        if 'Torrents' not in self.data:
            self.load_json_data()
        for profile in profiles:
            LOGGER.debug("Attempting to match movie to profile '%s'", profile)
            matches = self.data['Torrents']
            filter_dict = {
                'gp': (lambda t: t['GoldenPopcorn']),
                'scene': (lambda t: t['Scene']),
                '576p': (lambda t: t['Resolution'] == '576p'),
                '480p': (lambda t: t['Resolution'] == '480p'),
                '720p': (lambda t: t['Resolution'] == '720p'),
                '1080p': (lambda t: t['Resolution'] == '1080p'),
                'HD': (lambda t: t['Quality'] == 'High Definition'),
                'SD': (lambda t: t['Quality'] == 'Standard Definition'),
                'remux': (lambda t: 'remux' in t['RemasterTitle'].lower()),
                'x264': (lambda t: t['Codec'] == 'x264'),
                'seeded': (lambda t: t['Seeders'] > 0),
            }
            for (name, func) in filter_dict.items():
                if name.lower() in profile:
                    matches = [t for t in matches if func(t)]
                    LOGGER.debug("%i matches after filtering by parameter '%s'", len(matches), name)
            sort_dict = {
                'most recent': (True, (lambda t: datetime.strptime(t['UploadTime'], "%Y-%m-%d %H:%M:%S"))),
                'smallest': (True, (lambda t: t['Size'])),
                'seeders': (True, (lambda t: t['Seeders'])),
                'largest': (False, (lambda t: t['Size'])),
            }
            if len(matches) == 1:
                return matches[0]
            elif len(matches) > 1:
                for name, (rev, sort) in sort_dict.items():
                    if name in profile:
                        current_sort = name
                if current_sort is None:
                    current_sort = 'most recent'
                LOGGER.debug("Sorting by parameter %s", current_sort)
                (rev, sort) = sort_dict[current_sort]
                return sorted(matches, key=sort, reverse=rev)[0]
        LOGGER.info("Could not find best match for movie %s", self.ID)
        return None
