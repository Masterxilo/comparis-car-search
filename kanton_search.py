"""Script to scrape Comparis search results for a flat and filter them based on
the distances to our workplaces.

SEARCH_DEFAULTS contains the hard coded search requirements.

Requires env variables:
    GOOGLE_API_KEY to be set to a Google (Maps) API key, e.g. obtained from https://console.cloud.google.com/google/maps-apis/onboard;flow=gmp-api-key-flow (APIs and Services > Google Maps Platform)
    DESTINATIONS to be set to the Google Maps strings to compare distances to.

Usage example:

    PIP_BREAK_SYSTEM_PACKAGES=1 pip install -r requirements.txt
    DESTINATIONS='["Bern"]' GOOGLE_API_KEY='AIzaSyAoMlWx*****' python kanton_search.py
"""
import re
import json
import itertools
import datetime
import logging
import argparse
import os
from functools import reduce

import yaml
import requests
from bs4 import BeautifulSoup
import googlemaps


# Comparis options
COMPARIS_URL = 'https://www.comparis.ch/immobilien/result/list'
SEARCH_DEFAULTS = {
    'DealType': '10',
    'SiteId': '0',
    'RootPropertyTypes': ['1'], 'PropertyTypes': [],
    'RoomsFrom': '2', 'RoomsTo': '4',
    'FloorSearchType': '0',
    'LivingSpaceFrom': None, 'LivingSpaceTo': None,
    'PriceFrom': 600, 'PriceTo': '2000',
    'ComparisPointsMin': '4',
    'AdAgeMax': '1',
    'AdAgeInHoursMax': None,
    'Keyword': '',
    'WithImagesOnly': False,
    'WithPointsOnly': None,
    'Radius': '20',
    'MinAvailableDate': None,
    'MinChangeDate': '1753-01-01T00:00:00',
    'LocationSearchString': 'Br%C3%BCtten',
    'Sort': '3',
    'HasBalcony': False, 'HasTerrace': False, 'HasFireplace': False,
    'HasDishwasher': False, 'HasWashingMachine': False, 'HasLift': False,
    'HasParking': False, 'PetsAllowed': False, 'MinergieCertified': False,
    'WheelchairAccessible': False,
    'LowerLeftLatitude': None, 'LowerLeftLongitude': None,
    'UpperRightLatitude': None, 'UpperRightLongitude': None
}

# Google API
ARRIVAL_UTC = datetime.time(hour=8)  # 9am Zurich time
#GOOGLE_API_KEY = ''
# init GOOGLE_API_KEY from environment variable:
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')

# Other constants
DEBUG_MODE = False
MAX_TRAVEL_TIME = 40 * 60  # 40 minutes
#DESTINATIONS = []
# parse DESTINATIONS from environment variable as JSON
DESTINATIONS = json.loads(os.environ.get('DESTINATIONS', '[]'))

# Verbose logging for requests module (debugging)
# Taken from https://stackoverflow.com/questions/10588644/how-can-i-see-the-entire-http-request-thats-being-sent-by-my-python-application
# These two lines enable debugging at httplib level (requests->urllib3->http.client)
# You will see the REQUEST, including HEADERS and DATA, and RESPONSE with HEADERS but without DATA.
# The only thing missing will be the response.body which is not logged.
try:
    import http.client as http_client
except ImportError:
    # Python 2
    import httplib as http_client
http_client.HTTPConnection.debuglevel = 1
# You must initialize logging, otherwise you'll not see debug output.
logging.basicConfig()
logging.getLogger().setLevel(logging.DEBUG)
requests_log = logging.getLogger("requests.packages.urllib3")
requests_log.setLevel(logging.DEBUG)
requests_log.propagate = True


# Taken from http://stackoverflow.com/a/6558571
def next_weekday(date_time, weekday):
    """Determine the datetime of the next weekday (Mon:0, Tue:1, ...) after
    date_time.
    """
    days_ahead = weekday - date_time.weekday()
    if days_ahead <= 0:  # Target day already happened this week
        days_ahead += 7
    return date_time + datetime.timedelta(days_ahead)


def get_listings(page: int):
    """Get the listings of the page numbers."""
    logging.info("get_listings fetching listings for page: %d.", page)
    params = json.dumps(SEARCH_DEFAULTS, separators=(',', ':'))
    #url = COMPARIS_URL + '?requestobject=' + params
    # use a proper library to encode the params as a query string (encodeURIComponent style):
    url = COMPARIS_URL + '?requestobject=' + requests.utils.quote(params)

    logging.info("get_listings URL: %s", url)
    response = requests.get(url)
    if response.status_code != 200:
        logging.info("get_listings Request failed with HTTP code: %d", response.status_code)
        raise ValueError('get_listings Request failed with HTTP code: %d' % response.status_code)
    else:
        logging.info("get_listings Request succeeded with HTTP code: %d, "
                     "length: %d, content [...]: %s", response.status_code, len(response.content), response.content[:100])
        results = _parse_content(response.content)
        logging.info("Fetched %d listings.", len(results))
        return results


def _parse_content(content):
    """Parse the html content."""
    soup = BeautifulSoup(content, 'html.parser')

    results = [_parse_advert(advert) for advert in
               soup.findAll('div', id=re.compile('hf_result_.*'))]
    return results


def _parse_advert(advert):
    """Extract the useful information from the advert."""
    info = {}
    header = advert.find('div', class_='content header')
    info['title'] = header.find('a').text
    info['link'] = 'https://www.comparis.ch' + header.find('a')['href']
    info['price'] = advert.find('div', class_='item-price').text[1:-1]

    # Get and format the address
    info['address'] = advert.find('address').text
    info['address'] = ' '.join(info['address'].split())

    # Get the sundry details
    info['extra'] = advert.find('ul', class_='specifications').find_all('li')
    info['extra'] = [extra.text for extra in info['extra']]

    return info


def add_distances(places):
    """Add the transit distances to the places."""
    logging.info("Querying distance matrix for %d places", len(places))
    gmaps = googlemaps.Client(key=GOOGLE_API_KEY)

    # Set the time for the arrival
    arrival_time = next_weekday(datetime.datetime.now(), 0)  # 0 is Monday
    arrival_time = datetime.datetime.combine(arrival_time.date(), ARRIVAL_UTC)

    result = gmaps.distance_matrix(
        (place['address'] for place in places),
        DESTINATIONS,
        mode='transit',
        arrival_time=arrival_time
    )

    # Parse the results
    for place, row in zip(places, result['rows']):
        place['distances'] = {}
        for dest, entry in zip(DESTINATIONS,  # result['destination_addresses'],
                               row['elements']):
            place['distances'][dest] = (entry['duration']
                                        if (entry['status'] == 'OK') else None)
    logging.info("Distances appended to the places.")


def _individual_travel_time(place) -> bool:
    """Returns True if each individual travel time is under the MAX_TRAVEL_TIME.
    """
    # The distances should all be non-null, and the vlaue of each distance
    # should be under the max travel time
    return (not _distance_error(place) and
            all(distance['value'] <= MAX_TRAVEL_TIME for distance in
                place['distances'].values()))


def _total_travel_time(place) -> bool:
    """Returns True if the total of the travel time is under twice the
    MAX_TRAVEL_TIME.
    """
    if _distance_error(place):
        return False

    total_travel_time = reduce(lambda total, time: total + time['value'],
                               place['distances'].values(), 0)
    return total_travel_time <= 2 * MAX_TRAVEL_TIME


def _distance_error(place) -> bool:
    """Returns true if there was an error with at least one of the distances."""
    return any(distance is None for distance in place['distances'].values())


def _zurich_special(place) -> bool:
    """Returns true if Sana's work place is within 55minutes and JP's within
    35minutes.
    """
    SANA_TRAVEL_TIME = 55 * 60
    JP_TRAVEL_TIME = 35 * 60
    # All distance have a value and Zurich area code 80[0-9][0-9]
    if _distance_error(place) or not re.search(r'80\d\d', place['address']):
        return False
    return (place['distances'][SANA_OFFICE]['value'] <= SANA_TRAVEL_TIME and
            place['distances'][JP_OFFICE]['value'] <= JP_TRAVEL_TIME)


def main():
    """Run the main program."""
    args = _parse_arguments()
    # Setup logging
    if args.verbose:
        logging.basicConfig(level=logging.INFO)

    # Get the list of places
    places = []
    page_numbers = itertools.count() if not DEBUG_MODE else range(1)
    SEARCH_DEFAULTS['AdAgeMax'] = args.days
    try:
        for page_num in page_numbers:
            places += get_listings(page_num)
            # HACK throttle (wait for 5 seconds), to avoid getting blocked (403) by Comparis' "datadome" protection
            time.sleep(5)
    except Exception as e:
        logging.warning("No more pages to fetch or other error: %s", e)
        pass

    # Add distances in batches of 10
    batch_size = 10
    for i in range(0, len(places), batch_size):
        add_distances(places[i:i + batch_size])

    # Filter the places
    results = {
        'Individual travel time': list(filter(_individual_travel_time, places)),
        'Total travel time': list(filter(_total_travel_time, places)),
        'Distance error': list(filter(_distance_error, places)),
        'In Zürich': list(filter(_zurich_special, places))
    }
    print(yaml.dump(results, allow_unicode=True))


def _parse_arguments():
    parser = argparse.ArgumentParser(description="Search and filter homes.")
    parser.add_argument('--days', type=int, default=1, help='Number of days in'
                        'the past to check for listings.')
    parser.add_argument('--silent', action='store_false', dest='verbose')
    return parser.parse_args()


if __name__ == '__main__':
    main()
