"""Verify exact Spotify links on MusicBrainz-associated official public sites."""

import http.client
import ipaddress
import json
import socket
import ssl
from datetime import timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from app import storage
from app.services import mb_client


# A site still failing "temporarily" after this long is treated as gone until the negative cache expires.
FAILING_AS_GONE_AFTER = timedelta(days=1)


def spotify_artist(url):
    parsed = urlsplit(url)
    parts = parsed.path.strip('/').split('/')
    if parts and parts[0].startswith('intl-'):
        parts = parts[1:]
    if parts and parts[0] == 'embed':
        parts = parts[1:]
    if parsed.scheme in {'http', 'https'} and parsed.hostname == 'open.spotify.com' and len(parts) == 2 and parts[0] == 'artist':
        return parts[1]
    return None


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        if tag not in {'a', 'iframe'}:
            return
        for name, value in attrs:
            if name in {'href', 'src'} and value:
                artist = spotify_artist(value)
                if artist:
                    self.ids.add(artist)


class UnavailableSite(ValueError):
    """A site that cannot be checked until it changes (gone, refused, invalid)."""


def fetch_links(url):
    """Pin each connection to a checked public IP, including redirect targets."""
    for _ in range(5):
        parsed = urlsplit(url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            raise UnavailableSite('Unsupported official URL')
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        if port not in {80, 443}:
            raise UnavailableSite('Unsupported official port')
        try:
            addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
        except socket.gaierror as error:
            if error.errno in {socket.EAI_NONAME, getattr(socket, 'EAI_NODATA', socket.EAI_NONAME)}:
                raise UnavailableSite('Official website domain not found') from error
            raise
        if not addresses or any(not ipaddress.ip_address(entry[4][0]).is_global for entry in addresses):
            raise UnavailableSite('Official URL is not public')
        connection = http.client.HTTPConnection(parsed.hostname, port, timeout=10)
        address = (addresses[0][4][0], port)
        def connect():
            sock = socket.create_connection(address, timeout=10)
            connection.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parsed.hostname) if parsed.scheme == 'https' else sock
        connection.connect = connect
        try:
            try:
                connection.request('GET', parsed.path + ('?' + parsed.query if parsed.query else '') or '/',
                                   headers={'User-Agent': mb_client.USER_AGENT, 'Accept': 'text/html'})
                response = connection.getresponse()
            except ssl.SSLCertVerificationError as error:
                raise UnavailableSite('Official website certificate is invalid') from error
            if response.status in {301, 302, 303, 307, 308}:
                url = urljoin(url, response.getheader('Location', ''))
                continue
            if response.status in {404, 410}:
                return []
            if 400 <= response.status < 500 and response.status not in {408, 429}:
                raise UnavailableSite(f'Official website HTTP {response.status}')
            if response.status != 200:
                raise OSError(f'Official website HTTP {response.status}')
            body = response.read(1_000_001)
            if len(body) > 1_000_000:
                raise UnavailableSite('Official website response too large')
            parser = Links()
            parser.feed(body.decode('utf-8', errors='replace'))
            return sorted(parser.ids)
        finally:
            connection.close()
    raise UnavailableSite('Too many official website redirects')


def cached_links(url):
    key = f'artist-score-site:{url}'
    with storage.connect() as db:
        row = db.execute('SELECT payload,updated_at FROM mb_http_cache WHERE cache_key=?', (key,)).fetchone()
    cached = json.loads(row['payload']) if row else None
    if isinstance(cached, list) and not storage.is_stale(row['updated_at'], mb_client.CACHE_AGE):
        return cached
    if isinstance(cached, dict) and 'unavailable' in cached and not storage.is_stale(row['updated_at'], mb_client.NEGATIVE_CACHE_AGE):
        raise UnavailableSite(cached['unavailable'])
    def store(payload):
        with storage.connect() as db:
            db.execute('INSERT INTO mb_http_cache VALUES(?,?,?) ON CONFLICT(cache_key) '
                       'DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at',
                       (key, json.dumps(payload), storage.now()))
    try:
        result = fetch_links(url)
    except UnavailableSite as error:
        # Permanent failures are cached like empty searches, so dead sites are not refetched on every retry.
        store({'unavailable': str(error)})
        raise
    except (OSError, http.client.HTTPException) as error:
        since = cached.get('failing_since') if isinstance(cached, dict) else None
        if since and storage.is_stale(since, FAILING_AS_GONE_AFTER):
            store({'unavailable': f'Still failing since {since}: {error}'})
            raise UnavailableSite(f'Official website failing since {since}') from error
        store({'failing_since': since or storage.now()})
        raise
    store(result)
    return result


def identity_links(entity, spotify_id):
    direct, warnings, conflicts, complete = [], [], [], True
    sites = set()
    for relation in entity.get('relations', []):
        url = (relation.get('url') or {}).get('resource')
        if not url:
            continue
        other = spotify_artist(url)
        if other == spotify_id:
            direct.append(url)
        elif other:
            warnings.append({'kind': 'other_spotify', 'url': url})
        elif relation.get('type') in {'official homepage', 'bandcamp'}:
            sites.add(url)
    for url in sorted(sites):
        try:
            ids = cached_links(url)
        except UnavailableSite:
            # A dead site cannot become evidence; it must not block the whole analysis.
            warnings.append({'kind': 'site_unavailable', 'url': url})
            continue
        except (OSError, ValueError, http.client.HTTPException):
            complete = False
            warnings.append({'kind': 'site_unavailable', 'url': url, 'temporary': True})
            continue
        if spotify_id in ids:
            direct.append(url)
        elif ids:
            # Artist's own page points to another identity: require human review.
            conflicts.append({'kind': 'official_other_identity', 'url': url})
    return direct, warnings, conflicts, complete
