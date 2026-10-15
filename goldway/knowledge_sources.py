"""Explicit read-only source adapters. No crawling, source discovery or write API.

Registrations are trusted configuration supplied by the owner, never model output.
Private Google content additionally requires a verifiable per-reader provider ACL.
Literal non-public addresses and local hostnames are blocked, without DNS lookups.
DNS resolution/pinning is not performed: trusted domain ownership and outbound
network policy remain necessary against DNS rebinding. Redirects are not followed.
"""
import hashlib
import ipaddress
import json
import re
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit

import httpx


class SourceBlocked(ValueError):
    pass


def _https(url):
    try:
        if (not isinstance(url, str) or not url or len(url) > 2048
                or any(c.isspace() or ord(c) < 32 for c in url) or '\\' in url):
            raise ValueError('Malformed URL')
        p = urlsplit(url)
        if (p.scheme != 'https' or not p.hostname or p.username is not None or p.password is not None
                or p.fragment or p.port not in (None, 443)):
            raise ValueError('Not a plain HTTPS URL')
        host = p.hostname.rstrip('.').encode('idna').decode('ascii').casefold()
        if not host or '%' in host:
            raise ValueError('Invalid hostname')
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            labels = host.split('.')
            local = ('localhost', 'localdomain', 'local', 'internal', 'intranet',
                     'lan', 'home', 'corp', 'private', 'home.arpa')
            if (len(labels) < 2 or len(host) > 253
                    or any(host == suffix or host.endswith('.' + suffix) for suffix in local)
                    or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                           for label in labels)
                    or all(re.fullmatch(r'(?:0x[0-9a-f]+|[0-9]+)', label) for label in labels)
                    or labels[-1].isdecimal()):
                raise ValueError('Local or noncanonical hostname')
        else:
            if (not address.is_global or address.is_private or address.is_loopback
                    or address.is_link_local or address.is_multicast
                    or address.is_reserved or address.is_unspecified):
                raise ValueError('Non-public address')
    except (ValueError, TypeError, UnicodeError) as exc:
        raise SourceBlocked('Invalid registered public HTTPS source') from exc
    return url


def _permission_expiry(permission, now):
    """Return an effective grant end, or None when this grant cannot authorize."""
    if permission.get('deleted', False) is not False:
        return None
    if 'expirationTime' not in permission:
        return float('inf')
    value = permission['expirationTime']
    if not isinstance(value, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})', value):
        return None
    try:
        expiry = datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, OverflowError):
        return None
    return expiry if expiry > now else None


def _readers(spec, members):
    values = spec.get('readers')
    if (not isinstance(values, list) or not values or len(values) > 50
            or any(not isinstance(v, str) or v not in members for v in values)):
        raise SourceBlocked('Explicit confirmed reader policy required')
    return sorted(set(values))


class _HTML(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript', 'template'): self.skip += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'template'): self.skip = max(0, self.skip - 1)

    def handle_data(self, text):
        if not self.skip and text.strip(): self.parts.append(text.strip())


class ReadOnlySources:
    """Refresh only registered exact documents into a local KnowledgeIndex.

    client/Google credentials are injected. A source failure invalidates that
    source's old grants instead of serving a stale authorization snapshot.
    Status contains codes, never remote bodies, credentials or private titles.
    """
    def __init__(self, index, specs, members, *, client=None, google_credentials=None,
                 refresh_seconds=900, max_bytes=524288):
        if not isinstance(specs, list) or len(specs) > 16:
            raise ValueError('At most sixteen explicitly registered sources')
        if type(refresh_seconds) is not int or not 60 <= refresh_seconds <= 3600:
            raise ValueError('Invalid permission refresh period')
        self.index, self.specs, self.members = index, specs, set(members)
        self.client, self.credentials = client, google_credentials
        self.interval, self.max_bytes = refresh_seconds, max_bytes
        self.next_refresh, self.status = {}, {}
        ids = [s.get('id') for s in specs if isinstance(s, dict)]
        if (len(ids) != len(specs) or any(not isinstance(v, str) or not v for v in ids)
                or len(ids) != len(set(ids))):
            raise ValueError('Unique explicit source identifiers required')

    def _get(self, client, url, *, headers=None):
        _https(url)
        with client.stream('GET', url, headers=headers or {}, follow_redirects=False,
                           timeout=15) as response:
            if response.status_code != 200:
                raise SourceBlocked('Source is not anonymously/read-only accessible')
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > self.max_bytes: raise SourceBlocked('Source exceeds byte cap')
            return bytes(body)

    def _document(self, spec, readers, now, *, document_id, title, url, text, **extra):
        if not text.strip() or len(text) > 100000:
            raise SourceBlocked('Empty or oversized extracted document')
        return dict(source_id=spec['id'], document_id=document_id, title=title[:300],
                    url=url, text=text, readers=readers, observed_at=now,
                    permission_expires_at=now + self.interval, confirmed=True,
                    updated_at=spec.get('updated_at'), priority=spec.get('priority'), **extra)

    def _google(self, client, spec, readers, now):
        # Scope restriction is explicit; ambient broad Drive credentials are not used.
        if self.credentials is None:
            raise SourceBlocked('Google read-only credentials not configured')
        scopes = set(getattr(self.credentials, 'scopes', ()) or ())
        if not scopes or not scopes <= {'https://www.googleapis.com/auth/drive.readonly'}:
            raise SourceBlocked('Google credentials must use drive.readonly scope')
        file = spec.get('file_id')
        principals = spec.get('reader_emails')
        if not isinstance(file, str) or not re.fullmatch(r'[\w-]{8,200}', file):
            raise SourceBlocked('Exact approved Drive file required')
        if not isinstance(principals, dict) or set(principals) != set(readers):
            raise SourceBlocked('Verified reader identity mapping required')
        if not all(isinstance(v, str) and '@' in v for v in principals.values()):
            raise SourceBlocked('Invalid reader identity mapping')
        headers = {}
        from google.auth.transport.requests import Request
        url = 'https://www.googleapis.com/drive/v3/files/' + quote(file, safe='')
        metadata_url = url + '?fields=id,name,mimeType,modifiedTime,trashed,permissions(type,role,emailAddress,expirationTime,deleted)'
        self.credentials.before_request(Request(), 'GET', metadata_url, headers)
        meta = json.loads(self._get(client, metadata_url, headers=headers))
        if meta.get('id') != file or meta.get('trashed') is not False:
            raise SourceBlocked('Drive identity or state not verified')
        permissions = meta.get('permissions')
        if not isinstance(permissions, list): raise SourceBlocked('Drive permissions unavailable')
        grant_ends = {}
        for permission in permissions:
            if not isinstance(permission, dict): continue
            if permission.get('role') not in ('reader', 'commenter', 'writer', 'owner'): continue
            expiry = _permission_expiry(permission, now)
            if expiry is None: continue
            effective = []
            if permission.get('type') == 'anyone': effective = readers
            elif permission.get('type') == 'user':
                effective = [reader for reader, email in principals.items()
                             if email.casefold() == str(permission.get('emailAddress', '')).casefold()]
            for reader in effective:
                grant_ends[reader] = max(grant_ends.get(reader, expiry), expiry)
            # Group/domain membership cannot safely be guessed.
        granted = sorted(set(readers) & grant_ends.keys())
        if not granted: raise SourceBlocked('No verified effective document readers')
        mime = meta.get('mimeType')
        if mime in ('application/vnd.google-apps.document', 'application/vnd.google-apps.presentation'):
            content_url = url + '/export?mimeType=text%2Fplain'
        elif mime == 'text/plain': content_url = url + '?alt=media'
        else: raise SourceBlocked('Exact readable text document required; Sheets/PDF not enabled')
        headers = {}
        self.credentials.before_request(Request(), 'GET', content_url, headers)
        text = self._get(client, content_url, headers=headers).decode('utf-8-sig')
        document = self._document(spec, granted, now, document_id=file,
            title=str(meta.get('name') or 'Разрешённый документ'),
            url='https://drive.google.com/file/d/' + file + '/view', text=text)
        document['updated_at'] = meta.get('modifiedTime')
        document['permission_expires_at'] = min(now + self.interval,
                                                *(grant_ends[reader] for reader in granted))
        return [document]

    def documents(self, client, spec, now):
        readers = _readers(spec, self.members)
        kind = spec.get('kind')
        if kind == 'drive': return self._google(client, spec, readers, now)
        if kind not in ('website', 'github', 'public_catalog'):
            raise SourceBlocked('Unsupported source; no account/CRM/Sheets discovery')
        url = _https(spec.get('url', ''))
        if kind == 'website' and urlsplit(url).query:
            raise SourceBlocked('Website knowledge sources must be exact read-only pages without action queries')
        if kind == 'github':
            p = urlsplit(url); path = p.path.split('/')
            if (p.hostname != 'raw.githubusercontent.com' or len(path) < 5
                    or not re.fullmatch('[0-9a-f]{40}', path[3])
                    or '..' in path or not p.path.endswith(('.md', '.txt')) or p.query):
                raise SourceBlocked('Pinned exact GitHub text document required')
        if kind == 'public_catalog':
            if urlsplit(url).query != 'api=catalog':
                raise SourceBlocked('Only the registered public api=catalog GET is allowed')
        body = self._get(client, url)
        if kind == 'public_catalog':
            payload = json.loads(body)
            items = payload.get('items') if isinstance(payload, dict) else payload
            if not isinstance(items, list) or len(items) > 1000:
                raise SourceBlocked('Invalid or oversized public catalog')
            groups = {}
            for item in items:
                if not isinstance(item, dict): continue
                brand, model = item.get('brand'), item.get('model')
                if not isinstance(brand, str) or not brand.strip() or len(brand) > 160: continue
                entry = groups.setdefault(brand.strip().casefold(), {'name': brand.strip(), 'products': []})
                if isinstance(model, str) and model.strip(): entry['products'].append(model.strip()[:300])
            labels = [g['name'] for _, g in sorted(groups.items())]
            text = ('Публичный каталог GOLDWAY. Значения поля brand (не отдельный внутренний справочник брендов):\n'
                    + '; '.join(labels) + '.\nПолнота внутреннего ассортимента и ответственные здесь не подтверждены.')
            docs = [self._document(spec, readers, now, document_id='catalog-labels',
                    title='GOLDWAY: поле бренда публичного каталога', url=url, text=text)]
            for key, group in sorted(groups.items()):
                text = ('Публичный каталог GOLDWAY. В поле brand указано: ' + group['name']
                        + '.\nПозиции каталога:\n' + '\n'.join(dict.fromkeys(group['products']))
                        + '\nЭто публичные названия товаров, не данные об остатках, оплатах или ответственном.')
                docs.append(self._document(spec, readers, now,
                    document_id='label-' + hashlib.sha256(key.encode()).hexdigest()[:16],
                    title=group['name'] + ': публичные позиции каталога', url=url, text=text))
            return docs
        text = body.decode('utf-8-sig')
        if kind == 'website':
            parser = _HTML(); parser.feed(text); text = '\n'.join(parser.parts)
        return [self._document(spec, readers, now, document_id='document',
                title=spec.get('title', 'Разрешённый источник'), url=url, text=text)]

    def refresh(self, now, *, force=False):
        owned = self.client is None
        client = self.client or httpx.Client()
        try:
            for spec in self.specs:
                source = spec['id']
                if not force and now < self.next_refresh.get(source, 0): continue
                self.next_refresh[source] = now + self.interval
                try:
                    documents = self.documents(client, spec, now)
                    if len(documents) > 128: raise SourceBlocked('Source document cap')
                    if documents:
                        self.next_refresh[source] = min(self.next_refresh[source],
                            *(doc['permission_expires_at'] for doc in documents))
                    # Replace whole source atomically, removing stale grants/docs.
                    from contextlib import nullcontext
                    with (nullcontext() if self.index.store.db.in_transaction else self.index.store.transaction()):
                        self.index.revoke_source(source, observed_at=now)
                        for doc in documents: self.index.upsert(doc)
                    self.status[source] = 'ok'
                except Exception as exc:
                    try:
                        self.index.revoke_source(source, observed_at=now)
                    except ValueError:
                        # An older observation cannot revoke newer grants. Never
                        # fall back to an untimed revoke and bypass the watermark.
                        pass
                    self.status[source] = 'blocked' if isinstance(exc, SourceBlocked) else 'unavailable'
        finally:
            if owned: client.close()
        return dict(self.status)
