"""Bounded in-memory recovery rehearsal and payload-free health projection.

No SQLite/file/network/clock access, restore execution, service control or alerts.
Manifest schema metadata is an explicit declaration, not database inspection.
"""
from dataclasses import dataclass, fields
import hashlib
import hmac
import json
import re

MAX_ARTIFACTS = 16
MAX_ARTIFACT_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024


class RecoveryError(ValueError):
    """Fixed payload-free failure codes."""


def _label(value):
    if type(value) is not str or re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value) is None:
        raise RecoveryError('invalid_label')
    return value


def _integer(value, maximum=2**63-1, minimum=0):
    if type(value) is not int or not minimum <= value <= maximum:
        raise RecoveryError('invalid_integer')
    return value


def _path(value):
    if type(value) is not str or len(value) > 200:
        raise RecoveryError('unsafe_artifact_name')
    parts = value.split('/')
    if (not 1 <= len(parts) <= 4 or any(part in ('.', '..') or re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9._-]{0,63}', part) is None for part in parts)):
        raise RecoveryError('unsafe_artifact_name')
    return value


def _digest(value):
    if type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        raise RecoveryError('invalid_digest')
    return value


def _names(names):
    lowered = [name.casefold() for name in names]
    if len(lowered) != len(set(lowered)):
        raise RecoveryError('duplicate_artifact')
    if any(other.startswith(name + '/') for name in lowered for other in lowered if other != name):
        raise RecoveryError('artifact_path_conflict')


@dataclass(frozen=True)
class Artifact:
    name: str
    content: bytes

    def __post_init__(self):
        _path(self.name)
        if type(self.content) is not bytes or not 1 <= len(self.content) <= MAX_ARTIFACT_BYTES:
            raise RecoveryError('invalid_artifact_payload')


@dataclass(frozen=True)
class ArtifactInfo:
    name: str
    size_bytes: int
    sha256: str

    def __post_init__(self):
        _path(self.name)
        _integer(self.size_bytes, MAX_ARTIFACT_BYTES, 1)
        _digest(self.sha256)


def _artifacts(value, record_type, allow_empty=False):
    if (type(value) is not tuple or not (0 if allow_empty else 1) <= len(value) <= MAX_ARTIFACTS
            or any(type(item) is not record_type for item in value)):
        raise RecoveryError('invalid_artifact_set')
    _names([item.name for item in value])
    total = sum(len(item.content) if record_type is Artifact else item.size_bytes for item in value)
    if total > MAX_TOTAL_BYTES:
        raise RecoveryError('artifact_set_too_large')


@dataclass(frozen=True)
class Manifest:
    source_name: str
    business: str
    schema_version: int
    created_at: int
    artifacts: tuple[ArtifactInfo, ...]
    format_version: int = 1

    def __post_init__(self):
        _label(self.source_name)
        _label(self.business)
        _integer(self.schema_version, 2**31-1, 1)
        _integer(self.created_at)
        if type(self.format_version) is not int or self.format_version != 1:
            raise RecoveryError('unsupported_manifest_format')
        _artifacts(self.artifacts, ArtifactInfo)


def build_manifest(*, source_name: str, business: str, schema_version: int,
                   created_at: int, artifacts: tuple[Artifact, ...]) -> Manifest:
    _artifacts(artifacts, Artifact)
    infos = tuple(ArtifactInfo(item.name, len(item.content), hashlib.sha256(item.content).hexdigest())
                  for item in sorted(artifacts, key=lambda item: item.name))
    return Manifest(source_name, business, schema_version, created_at, infos)


def manifest_digest(manifest: Manifest) -> str:
    if type(manifest) is not Manifest:
        raise RecoveryError('invalid_manifest')
    body = {'format':'synthetic-recovery-v1', 'format_version':manifest.format_version,
        'source_name':manifest.source_name, 'business':manifest.business,
        'schema_version':manifest.schema_version, 'created_at':manifest.created_at,
        'artifacts':[[item.name,item.size_bytes,item.sha256]
                     for item in sorted(manifest.artifacts,key=lambda item:item.name)]}
    return hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode('ascii')).hexdigest()


@dataclass(frozen=True)
class FakeTarget:
    name: str
    business: str
    schema_version: int
    artifacts: tuple[Artifact, ...] = ()

    def __post_init__(self):
        _label(self.name)
        _label(self.business)
        _integer(self.schema_version, 2**31-1, 1)
        _artifacts(self.artifacts, Artifact, allow_empty=True)


@dataclass(frozen=True)
class RestorePreview:
    target: FakeTarget
    manifest_digest: str
    artifact_count: int
    total_bytes: int
    dry_run: bool = True


def dry_run_restore(manifest: Manifest, artifacts: tuple[Artifact, ...], *,
                    expected_manifest_digest: str, expected_business: str,
                    expected_schema_version: int, required_artifacts: tuple[str, ...],
                    now: int, max_age_seconds: int, target: FakeTarget) -> RestorePreview:
    """Validate supplied bytes and construct a separate fake result; never write.

    Expected digest must come from an independently trusted observation. Hashes
    alone do not authenticate a backup if an attacker can replace both inputs.
    """
    _digest(expected_manifest_digest)
    actual_digest = manifest_digest(manifest)
    if not hmac.compare_digest(expected_manifest_digest, actual_digest):
        raise RecoveryError('manifest_digest_mismatch')
    _label(expected_business)
    _integer(expected_schema_version, 2**31-1, 1)
    _integer(now)
    _integer(max_age_seconds, 2**31-1, 1)
    if type(target) is not FakeTarget or target.name == manifest.source_name or target.artifacts:
        raise RecoveryError('separate_empty_fake_target_required')
    if manifest.business != expected_business or target.business != expected_business:
        raise RecoveryError('wrong_business')
    if manifest.schema_version != expected_schema_version or target.schema_version != expected_schema_version:
        raise RecoveryError('wrong_schema')
    if not 0 <= now - manifest.created_at <= max_age_seconds:
        raise RecoveryError('stale_or_future_backup')
    if type(required_artifacts) is not tuple or not 1 <= len(required_artifacts) <= MAX_ARTIFACTS:
        raise RecoveryError('explicit_required_artifacts_needed')
    for name in required_artifacts:
        _path(name)
    _names(required_artifacts)
    _artifacts(artifacts, Artifact)
    actual = {item.name:item for item in artifacts}
    declared = {item.name:item for item in manifest.artifacts}
    if set(actual) != set(declared) or not set(required_artifacts) <= set(declared):
        raise RecoveryError('missing_or_unexpected_artifacts')
    for name, item in actual.items():
        info = declared[name]
        if len(item.content) != info.size_bytes or not hmac.compare_digest(hashlib.sha256(item.content).hexdigest(), info.sha256):
            raise RecoveryError('artifact_corruption')
    # Tuples, frozen records and bytes cannot mutate source or original target.
    restored = FakeTarget(target.name, target.business, target.schema_version,
                          tuple(actual[name] for name in sorted(actual)))
    return RestorePreview(restored, actual_digest, len(actual), sum(len(item.content) for item in artifacts))


@dataclass(frozen=True)
class HealthCounts:
    event_pending: int
    event_failed: int
    outbox_pending: int
    outbox_retry: int
    outbox_blocked: int
    outbox_sending: int
    outbox_unknown: int

    def __post_init__(self):
        for field in fields(self):
            _integer(getattr(self,field.name),10**9)


def health_projection(*, counts: HealthCounts, store_state: str, worker_state: str,
                      observed_at: int, heartbeat_at: int | None, now: int,
                      observation_max_age_seconds: int, heartbeat_max_age_seconds: int) -> dict:
    """Allowlisted aggregate projection, not proof of service/delivery readiness."""
    if type(counts) is not HealthCounts:
        raise RecoveryError('validated_health_counts_required')
    if type(store_state) is not str or store_state not in ('ok','unavailable','corrupt','unknown'):
        raise RecoveryError('invalid_store_state')
    if type(worker_state) is not str or worker_state not in ('active','stopped','unknown'):
        raise RecoveryError('invalid_worker_state')
    for value in (observed_at,now):
        _integer(value)
    for value in (observation_max_age_seconds,heartbeat_max_age_seconds):
        _integer(value,2**31-1,1)
    if observed_at > now:
        raise RecoveryError('future_health_observation')
    if heartbeat_at is not None:
        _integer(heartbeat_at)
        if heartbeat_at > observed_at:
            raise RecoveryError('future_heartbeat')
    observation = 'recent' if now-observed_at <= observation_max_age_seconds else 'stale'
    heartbeat = ('missing' if heartbeat_at is None else
                 'recent' if now-heartbeat_at <= heartbeat_max_age_seconds else 'stale')
    attention=[]
    if store_state != 'ok':attention.append('store_not_ok')
    if worker_state != 'active':attention.append('worker_not_active')
    if observation != 'recent':attention.append('observation_stale')
    if heartbeat != 'recent':attention.append('heartbeat_'+heartbeat)
    for name in ('event_failed','outbox_retry','outbox_blocked','outbox_sending','outbox_unknown'):
        if getattr(counts,name):attention.append(name)
    return {'status':'attention' if attention else 'no_attention_in_supplied_metrics',
        'store_state':store_state,'worker_state':worker_state,
        'observation_state':observation,'heartbeat_state':heartbeat,
        'observed_at':observed_at,'heartbeat_at':heartbeat_at,
        'counts':{field.name:getattr(counts,field.name) for field in fields(counts)},
        'attention_codes':attention,'external_delivery':'not_evaluated'}
