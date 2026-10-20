"""Explicit local provider contracts; no discovery, I/O, storage, or runtime hooks.

Callbacks are trusted application code, not sandboxed. Returned content is data:
registration alone owns source identity, audience, TTL and confirmation status.
"""
from dataclasses import dataclass, field
import hashlib
import json
import math
import re
from typing import Callable

MAX_DOCUMENTS = 32
MAX_TEXT_BYTES = 64000
MAX_SNAPSHOT_BYTES = 128000


class ProviderError(ValueError):
    """Fixed payload-free reason code; never includes callback exception text."""


def _label(value):
    if type(value) is not str or re.fullmatch(r'[A-Za-z0-9._-]{1,64}', value) is None:
        raise ProviderError('invalid_identifier')
    return value


def _reader(value):
    # Existing KnowledgeIndex accepts opaque reader IDs up to 128 characters;
    # Intelligence.reader_key uses reader:<sha256>, not just roster names.
    if type(value) is not str or re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', value) is None:
        raise ProviderError('invalid_reader')
    return value


def _version(value):
    if type(value) is not int or not 1 <= value < 2**63:
        raise ProviderError('invalid_version')
    return value


def _time(value):
    if type(value) not in (int, float):
        raise ProviderError('invalid_time')
    try:
        if value < 0 or not math.isfinite(value):
            raise ProviderError('invalid_time')
    except OverflowError:
        raise ProviderError('invalid_time') from None
    return value


@dataclass(frozen=True)
class SourceSpec:
    provider_id: str
    source_id: str
    audience: frozenset[str]
    minimum_version: int
    ttl_seconds: int
    confirmed: bool = False

    def __post_init__(self):
        _label(self.provider_id)
        _label(self.source_id)
        _version(self.minimum_version)
        if (type(self.audience) is not frozenset or not 1 <= len(self.audience) <= 50
                or type(self.confirmed) is not bool):
            raise ProviderError('invalid_source_policy')
        for reader in self.audience:
            _reader(reader)
        if type(self.ttl_seconds) is not int or not 1 <= self.ttl_seconds <= 86400:
            raise ProviderError('invalid_ttl')


@dataclass(frozen=True)
class Document:
    id: str
    title: str
    text: str


@dataclass(frozen=True)
class ProviderSnapshot:
    provider_id: str
    source_id: str
    version: int
    observed_at: int | float
    documents: tuple[Document, ...]


@dataclass(frozen=True)
class SourceRequest:
    provider_id: str
    source_id: str
    minimum_version: int
    known_version: int | None


@dataclass(frozen=True)
class SourceMetadata:
    """Current cached permission/version metadata, without documents or I/O."""
    business: str
    spec: SourceSpec
    version: int
    observed_at: int | float
    expires_at: int | float
    fingerprint: str


@dataclass
class _Provider:
    callback: Callable = field(repr=False)
    credential: object = field(repr=False)


@dataclass
class _Source:
    spec: SourceSpec
    revoked: bool = False
    known_version: int | None = None
    observed_at: int | float | None = None
    fingerprint: str | None = None
    snapshot: ProviderSnapshot | None = field(default=None, repr=False)


class TrustedProviders:
    """In-memory permission authority; each instance owns one explicit business.

    Host code registers callbacks and policies. refresh() replaces a whole source
    snapshot atomically in memory; every failure invalidates cached content.
    documents() checks the current clock, registration and reader on every use.
    """
    def __init__(self, *, business: str, members: frozenset[str], clock: Callable):
        self.business = _label(business)
        if type(members) is not frozenset or not 1 <= len(members) <= 200 or not callable(clock):
            raise ProviderError('invalid_registry_policy')
        for member in members:
            _reader(member)
        self._members, self._clock = members, clock
        self._providers, self._sources = {}, {}
        self._last_now = None

    def _now(self):
        try:
            now = _time(self._clock())
        except Exception:
            raise ProviderError('invalid_clock') from None
        if self._last_now is not None and now < self._last_now:
            raise ProviderError('clock_rollback')
        self._last_now = now
        return now

    def source_metadata(self, source_id: str, *, reader: str, now: int | float) -> SourceMetadata:
        """Read only; explicit time never invokes the registry clock/provider."""
        source = self._source(source_id)
        _reader(reader)
        now = _time(now)
        spec = source.spec
        if type(spec) is not SourceSpec or source.revoked is not False:
            raise ProviderError('invalid_source_policy')
        spec.__post_init__()
        if spec.source_id != source_id or reader not in spec.audience or reader not in self._members:
            raise ProviderError('wrong_audience')
        snapshot = source.snapshot
        if type(snapshot) is not ProviderSnapshot:
            raise ProviderError('snapshot_unavailable')
        _version(snapshot.version)
        observed = _time(snapshot.observed_at)
        expires = observed + spec.ttl_seconds
        if (snapshot.provider_id != spec.provider_id or snapshot.source_id != spec.source_id
                or snapshot.version < spec.minimum_version or source.known_version != snapshot.version
                or source.observed_at != observed or type(source.fingerprint) is not str
                or re.fullmatch('[0-9a-f]{64}', source.fingerprint) is None):
            raise ProviderError('snapshot_source_mismatch')
        if not observed <= now < expires or not observed < expires:
            raise ProviderError('stale_or_future_snapshot')
        return SourceMetadata(self.business, spec, snapshot.version, observed, expires, source.fingerprint)

    def register_provider(self, provider_id: str, callback: Callable, *, credential: object):
        _label(provider_id)
        if provider_id in self._providers or not callable(callback):
            raise ProviderError('invalid_provider_registration')
        self._providers[provider_id] = _Provider(callback, credential)

    def register_source(self, spec: SourceSpec):
        if type(spec) is not SourceSpec:
            raise ProviderError('invalid_source_policy')
        if spec.provider_id not in self._providers:
            raise ProviderError('unregistered_provider')
        if spec.source_id in self._sources:
            raise ProviderError('source_already_registered')
        if not spec.audience <= self._members:
            raise ProviderError('unconfirmed_audience')
        self._sources[spec.source_id] = _Source(spec)

    def _source(self, source_id):
        _label(source_id)
        source = self._sources.get(source_id)
        if source is None:
            raise ProviderError('unregistered_source')
        if source.revoked:
            raise ProviderError('revoked_source')
        return source

    def revoke(self, source_id: str):
        _label(source_id)
        source = self._sources.get(source_id)
        if source is None:
            raise ProviderError('unregistered_source')
        # Revocation is sticky/idempotent and independent of clock availability.
        source.revoked, source.snapshot = True, None

    def refresh(self, source_id: str):
        source = self._source(source_id)
        source.snapshot = None  # No stale fallback after any failed refresh.
        now = self._now()
        spec = source.spec
        provider = self._providers[spec.provider_id]
        request = SourceRequest(spec.provider_id, spec.source_id, spec.minimum_version, source.known_version)
        try:
            value = provider.callback(request, provider.credential)
        except Exception:
            raise ProviderError('provider_failure') from None
        # Retrieval may advance the clock; use completion time for freshness.
        now = self._now()
        if type(value) is not ProviderSnapshot:
            raise ProviderError('invalid_snapshot')
        _label(value.provider_id)
        _label(value.source_id)
        if value.provider_id != spec.provider_id or value.source_id != spec.source_id:
            raise ProviderError('snapshot_source_mismatch')
        _version(value.version)
        observed = _time(value.observed_at)
        expires = observed + spec.ttl_seconds
        if observed > now or not observed < expires or not now < expires:
            raise ProviderError('stale_or_future_snapshot')
        if value.version < max(spec.minimum_version, source.known_version or spec.minimum_version):
            raise ProviderError('version_rollback')
        if source.observed_at is not None and observed < source.observed_at:
            raise ProviderError('observation_rollback')
        if type(value.documents) is not tuple or len(value.documents) > MAX_DOCUMENTS:
            raise ProviderError('invalid_document_count')
        ids, content = set(), []
        for document in value.documents:
            if type(document) is not Document:
                raise ProviderError('invalid_document')
            _label(document.id)
            if document.id in ids:
                raise ProviderError('duplicate_document')
            ids.add(document.id)
            if (type(document.title) is not str or not document.title.strip() or len(document.title) > 300
                    or type(document.text) is not str or not document.text.strip() or len(document.text) > MAX_TEXT_BYTES):
                raise ProviderError('invalid_document_content')
            try:
                if len(document.text.encode('utf-8')) > MAX_TEXT_BYTES:
                    raise ProviderError('document_too_large')
            except UnicodeError:
                raise ProviderError('invalid_document_content') from None
            content.append([document.id, document.title, document.text])
        try:
            encoded = json.dumps(sorted(content), ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        except UnicodeError:
            raise ProviderError('invalid_document_content') from None
        if len(encoded) > MAX_SNAPSHOT_BYTES:
            raise ProviderError('snapshot_too_large')
        fingerprint = hashlib.sha256(encoded).hexdigest()
        if value.version == source.known_version and fingerprint != source.fingerprint:
            raise ProviderError('version_content_changed')
        source.known_version, source.observed_at, source.fingerprint = value.version, observed, fingerprint
        source.snapshot = value

    def documents(self, source_id: str, *, reader: str) -> tuple[dict, ...]:
        source = self._source(source_id)
        _reader(reader)
        if reader not in source.spec.audience or reader not in self._members:
            raise ProviderError('wrong_audience')
        now = self._now()
        snapshot, spec = source.snapshot, source.spec
        if snapshot is None:
            raise ProviderError('snapshot_unavailable')
        expires = snapshot.observed_at + spec.ttl_seconds
        if not snapshot.observed_at <= now < expires:
            source.snapshot = None
            raise ProviderError('stale_snapshot')
        # A fresh projection prevents callers mutating the stored ACL or snapshot.
        provenance = 'trusted:' + self.business + ':' + spec.provider_id + ':' + spec.source_id
        return tuple({'source_id': provenance, 'document_id': document.id,
            'provider_id': spec.provider_id, 'version': snapshot.version,
            'title': document.title, 'text': document.text,
            'url': f'https://trusted.invalid/{self.business}/{spec.provider_id}/{spec.source_id}/{document.id}?version={snapshot.version}',
            'readers': sorted(spec.audience), 'observed_at': snapshot.observed_at,
            'permission_expires_at': expires, 'confirmed': spec.confirmed}
            for document in sorted(snapshot.documents, key=lambda item: item.id))
