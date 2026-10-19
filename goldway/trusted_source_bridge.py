"""Host-injected local registry -> existing KnowledgeIndex refresh boundary.

No discovery, HTTP, file access, credentials or provider payload diagnostics.
Original provider observation/expiry is never replaced with refresh wall time.
"""
from contextlib import contextmanager
import hashlib
import json
import math
import re

from .trusted_provider import SourceSpec, TrustedProviders


class BridgeError(ValueError):
    pass


class TrustedSourceBridge:
    @staticmethod
    def validate_bindings(registry,bindings,*,business,members,existing_source_ids=()):
        """Metadata only: no provider/clock access or initialization effects."""
        try:
            if type(registry) is not TrustedProviders or type(business) is not str or registry.business!=business or re.fullmatch(r'[A-Za-z0-9._-]{1,64}',business) is None:raise ValueError()
            if type(bindings) is not tuple or len(bindings)>16 or any(type(spec) is not SourceSpec for spec in bindings):raise ValueError()
            if len({spec.source_id for spec in bindings})!=len(bindings):raise ValueError()
            existing=tuple(existing_source_ids)
            if any(type(value) is not str or not value for value in existing) or len(set(existing))!=len(existing):raise ValueError()
            ids=[]
            for spec in bindings:
                spec.__post_init__()
                registered=registry._sources.get(spec.source_id)
                if registered is None or registered.spec!=spec or not spec.audience<=members:raise ValueError()
                ids.append('trusted:'+business+':'+spec.provider_id+':'+spec.source_id)
            if len(existing)+len(ids)>16 or set(existing)&set(ids):raise ValueError()
            return tuple(ids)
        except Exception:raise BridgeError('trusted_injection_invalid') from None

    def __init__(self,index,registry,bindings,*,business,members):
        self.validate_bindings(registry,bindings,business=business,members=members)
        self.index,self.registry,self.bindings,self.business,self.members=index,registry,bindings,business,members
        if bindings:
            index.store.execute('''CREATE TABLE IF NOT EXISTS trusted_source_versions(
                business TEXT NOT NULL,source_id TEXT NOT NULL,version INTEGER NOT NULL,
                observed_at REAL NOT NULL,fingerprint TEXT NOT NULL,
                PRIMARY KEY(business,source_id))''')

    def source_id(self,spec):
        return 'trusted:'+self.business+':'+spec.provider_id+':'+spec.source_id

    @contextmanager
    def _atomic(self):
        store=self.index.store
        store.execute('SAVEPOINT trusted_source_bridge')
        try:
            yield
        except BaseException:
            store.execute('ROLLBACK TO trusted_source_bridge')
            store.execute('RELEASE trusted_source_bridge')
            raise
        else:
            store.execute('RELEASE trusted_source_bridge')

    def _withdraw(self,source,now):
        # Inclusive withdrawal also blocks a cached snapshot from the same second.
        # An older clock must never override a newer index revocation watermark.
        with self._atomic():
            try:self.index.revoke_source(source,observed_at=now)
            except ValueError:return
            self.index.revoke_source(source)

    def _check_replacement(self,source,snapshot,documents):
        # revoke_source(T) intentionally permits fresh same-time replacement;
        # first preserve any existing *inclusive* withdrawal of that timestamp.
        marker=self.index.store.execute('SELECT observed_at,inclusive FROM knowledge_source_revocations WHERE source_id=?',
                                        (source,)).fetchone()
        if marker and (snapshot.observed_at<marker['observed_at'] or
                       (marker['inclusive'] and snapshot.observed_at==marker['observed_at'])):
            raise BridgeError('trusted_observation_predates_revocation')
        content=sorted([doc['document_id'],doc['title'],doc['text']] for doc in documents)
        fingerprint=hashlib.sha256(json.dumps(content,ensure_ascii=False,separators=(',',':')).encode('utf-8')).hexdigest()
        previous=self.index.store.execute('''SELECT version,observed_at,fingerprint FROM trusted_source_versions
            WHERE business=? AND source_id=?''',(self.business,source)).fetchone()
        if previous and (snapshot.version<previous['version'] or snapshot.observed_at<previous['observed_at']
                         or (snapshot.version==previous['version'] and fingerprint!=previous['fingerprint'])):
            raise BridgeError('trusted_persisted_version_rollback')
        return fingerprint

    def refresh(self,now):
        if type(now) not in (int,float) or not 0<=now<=2**53-1 or not math.isfinite(now):
            raise BridgeError('trusted_refresh_clock_invalid')
        status={}
        for binding in self.bindings:
            source=self.source_id(binding)
            failure_time=now
            try:
                if self.registry.business!=self.business or not binding.audience<=self.members:
                    raise BridgeError('trusted_policy_changed')
                registered=self.registry._source(binding.source_id)
                if registered.spec!=binding:raise BridgeError('trusted_policy_changed')
                self.registry.refresh(binding.source_id)
                documents=self.registry.documents(binding.source_id,reader=sorted(binding.audience)[0])
                registered=self.registry._source(binding.source_id)
                snapshot=registered.snapshot
                if snapshot is None:raise BridgeError('trusted_snapshot_unavailable')
                # Use the registry's completion-time clock, including slow callbacks.
                failure_time=max(failure_time,self.registry._now())
                if registered.spec!=binding:raise BridgeError('trusted_policy_changed')
                for document in documents:
                    if (document['source_id']!=source or document['provider_id']!=binding.provider_id
                            or document['version']!=snapshot.version
                            or document['observed_at']!=snapshot.observed_at
                            or document['permission_expires_at']!=snapshot.observed_at+binding.ttl_seconds
                            or document['readers']!=sorted(binding.audience)
                            or document['confirmed'] is not binding.confirmed):
                        raise BridgeError('trusted_projection_mismatch')
                # Host membership/business may change during a provider callback.
                # Recheck the complete audience, not just documents()' selected reader.
                if self.registry.business!=self.business or not binding.audience<=self.members:
                    raise BridgeError('trusted_policy_changed')
                # Full batch rollback works even inside an existing outer transaction.
                with self._atomic():
                    fingerprint=self._check_replacement(source,snapshot,documents)
                    self.index.revoke_source(source,observed_at=snapshot.observed_at)
                    for document in documents:self.index.upsert(document)
                    self.index.store.execute('''INSERT INTO trusted_source_versions VALUES(?,?,?,?,?)
                        ON CONFLICT(business,source_id) DO UPDATE SET version=excluded.version,
                        observed_at=excluded.observed_at,fingerprint=excluded.fingerprint''',
                        (self.business,source,snapshot.version,snapshot.observed_at,fingerprint))
                status[source]='ok'
            except Exception:
                try:failure_time=max(failure_time,self.registry._now())
                except Exception:pass
                self._withdraw(source,failure_time)
                status[source]='blocked'
        return status
