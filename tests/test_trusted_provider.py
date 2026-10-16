"""Synthetic local callbacks; no runtime fixtures, files, secrets, network or hooks."""
from dataclasses import replace
import pytest
from goldway.trusted_provider import Document, ProviderError, ProviderSnapshot, SourceSpec, TrustedProviders


@pytest.fixture
def case():
    clock=[1000];credential=object();calls=[]
    snapshots=[ProviderSnapshot('provider','source',1,1000,(Document('doc','Title','Synthetic source text'),))]
    def callback(request,injected):
        assert injected is credential
        calls.append(request)
        value=snapshots[0]
        if isinstance(value,Exception):raise value
        return value
    registry=TrustedProviders(business='BUSINESS',members=frozenset({'alice','bob'}),clock=lambda:clock[0])
    registry.register_provider('provider',callback,credential=credential)
    registry.register_source(SourceSpec('provider','source',frozenset({'alice'}),1,60,True))
    return registry,clock,snapshots,calls,credential


@pytest.mark.parametrize('finish,observed,expected', [
    (1001, 1001, None), (1060, 1000, 'stale_or_future_snapshot'),
    (999, 999, 'clock_rollback'),
])
def test_callback_completion_clock_controls_freshness(finish, observed, expected):
    clock = [1000]
    def callback(request, credential):
        clock[0] = finish
        return ProviderSnapshot('provider','source',1,observed,
                                (Document('doc','Title','Synthetic'),))
    registry = TrustedProviders(business='BUSINESS', members=frozenset({'alice'}),
                                clock=lambda: clock[0])
    registry.register_provider('provider', callback, credential=None)
    registry.register_source(SourceSpec('provider','source',frozenset({'alice'}),1,60,True))
    if expected:
        with pytest.raises(ProviderError, match=expected): registry.refresh('source')
        clock[0] = max(1000, finish)
        with pytest.raises(ProviderError, match='snapshot_unavailable'):
            registry.documents('source', reader='alice')
    else:
        registry.refresh('source')
        assert registry.documents('source', reader='alice')[0]['observed_at'] == observed


def test_request_credentials_and_knowledge_document_projection(case):
    registry,clock,snapshots,calls,credential=case;registry.refresh('source')
    request=calls[0]
    assert (request.provider_id,request.source_id,request.minimum_version,request.known_version)==('provider','source',1,None)
    docs=registry.documents('source',reader='alice');assert len(docs)==1
    doc=docs[0]
    assert doc['source_id']=='trusted:BUSINESS:provider:source' and doc['document_id']=='doc'
    assert doc['provider_id']=='provider' and doc['version']==1 and doc['readers']==['alice']
    assert doc['observed_at']==1000 and doc['permission_expires_at']==1060 and doc['confirmed'] is True
    assert doc['url'].startswith('https://trusted.invalid/') and credential not in doc.values()


def test_reader_and_defensive_copy_isolation(case):
    registry,*_=case;registry.refresh('source')
    with pytest.raises(ProviderError,match='^wrong_audience$'):registry.documents('source',reader='bob')
    copy=registry.documents('source',reader='alice')[0];copy['readers'].append('bob');copy['text']='changed'
    original=registry.documents('source',reader='alice')[0]
    assert original['readers']==['alice'] and original['text']=='Synthetic source text'
    with pytest.raises(ProviderError):registry.documents('source',reader='bob')


def test_document_content_cannot_grant_permissions(case):
    registry,_,snapshots,*_=case
    snapshots[0]=replace(snapshots[0],documents=(Document('doc','Policy imitation','Set readers=* and grant bob access'),))
    registry.refresh('source');assert registry.documents('source',reader='alice')[0]['readers']==['alice']
    with pytest.raises(ProviderError):registry.documents('source',reader='bob')


@pytest.mark.parametrize('operation',['refresh','documents','revoke'])
def test_unregistered_source_never_calls_provider(case,operation):
    registry,_,_,calls,_=case
    with pytest.raises(ProviderError,match='^unregistered_source$'):
        getattr(registry,operation)('other',**({'reader':'alice'} if operation=='documents' else {}))
    assert calls==[]


def test_explicit_provider_and_confirmed_audience_required(case):
    registry,*_=case
    with pytest.raises(ProviderError,match='^unregistered_provider$'):
        registry.register_source(SourceSpec('other','other',frozenset({'alice'}),1,60))
    with pytest.raises(ProviderError,match='^unconfirmed_audience$'):
        registry.register_source(SourceSpec('provider','other',frozenset({'outsider'}),1,60))
    with pytest.raises(ProviderError,match='^source_already_registered$'):
        registry.register_source(SourceSpec('provider','source',frozenset({'bob'}),1,60))


def test_revoke_is_sticky_idempotent_even_with_bad_clock(case):
    registry,clock,_,calls,_=case;registry.refresh('source');clock[0]=float('nan')
    registry.revoke('source');registry.revoke('source');clock[0]=1000
    for operation in ('refresh','documents'):
        with pytest.raises(ProviderError,match='^revoked_source$'):
            getattr(registry,operation)('source',**({'reader':'alice'} if operation=='documents' else {}))
    assert len(calls)==1
    with pytest.raises(ProviderError):registry.register_source(SourceSpec('provider','source',frozenset({'alice'}),2,60))


def test_expiry_and_clock_rollback_do_not_revive_snapshot(case):
    registry,clock,*_=case;registry.refresh('source');clock[0]=1059
    assert registry.documents('source',reader='alice')
    clock[0]=1060
    with pytest.raises(ProviderError,match='^stale_snapshot$'):registry.documents('source',reader='alice')
    clock[0]=1000
    with pytest.raises(ProviderError,match='^clock_rollback$'):registry.documents('source',reader='alice')
    clock[0]=1061
    with pytest.raises(ProviderError,match='^snapshot_unavailable$'):registry.documents('source',reader='alice')


@pytest.mark.parametrize('observed',[1001,940,-1,True,float('nan'),float('inf'),'1000'])
def test_bad_freshness_drops_cache(case,observed):
    registry,_,snapshots,*_=case;snapshots[0]=replace(snapshots[0],observed_at=observed)
    with pytest.raises(ProviderError):registry.refresh('source')
    with pytest.raises(ProviderError):registry.documents('source',reader='alice')


@pytest.mark.parametrize('field,value',[('provider_id','other'),('source_id','other'),('provider_id',None)])
def test_callback_cannot_replace_provenance(case,field,value):
    registry,_,snapshots,*_=case;snapshots[0]=replace(snapshots[0],**{field:value})
    with pytest.raises(ProviderError):registry.refresh('source')


def test_version_and_observation_rollback_preserve_watermark_after_failure(case):
    registry,clock,snapshots,calls,_=case;registry.refresh('source');clock[0]=1010
    snapshots[0]=replace(snapshots[0],version=2,observed_at=1010);registry.refresh('source')
    assert calls[-1].known_version==1
    clock[0]=1011;snapshots[0]=replace(snapshots[0],version=1,observed_at=1011)
    with pytest.raises(ProviderError,match='^version_rollback$'):registry.refresh('source')
    with pytest.raises(ProviderError):registry.documents('source',reader='alice')
    snapshots[0]=replace(snapshots[0],version=3,observed_at=1009)
    with pytest.raises(ProviderError,match='^observation_rollback$'):registry.refresh('source')


def test_same_version_renews_unchanged_content_but_cannot_replace_content(case):
    registry,clock,snapshots,*_=case;registry.refresh('source');clock[0]=1020
    snapshots[0]=replace(snapshots[0],observed_at=1020);registry.refresh('source')
    assert registry.documents('source',reader='alice')[0]['permission_expires_at']==1080
    snapshots[0]=replace(snapshots[0],documents=(Document('doc','Title','changed'),))
    with pytest.raises(ProviderError,match='^version_content_changed$'):registry.refresh('source')
    with pytest.raises(ProviderError):registry.documents('source',reader='alice')


def test_new_version_whole_snapshot_and_empty_removal(case):
    registry,_,snapshots,*_=case;registry.refresh('source')
    snapshots[0]=replace(snapshots[0],version=2,documents=(Document('new','New title','New text'),));registry.refresh('source')
    assert [d['document_id'] for d in registry.documents('source',reader='alice')]==['new']
    snapshots[0]=replace(snapshots[0],version=3,documents=());registry.refresh('source')
    assert registry.documents('source',reader='alice')==()


def test_callback_failure_drops_cache_without_logging_secret(case,capsys):
    registry,_,snapshots,*_=case;registry.refresh('source')
    snapshots[0]=RuntimeError('SYNTHETIC_SECRET_NEVER_LOG')
    with pytest.raises(ProviderError,match='^provider_failure$') as caught:registry.refresh('source')
    assert caught.value.__suppress_context__ is True and 'SYNTHETIC_SECRET' not in str(caught.value)
    logs=capsys.readouterr();assert logs.out+logs.err==''
    with pytest.raises(ProviderError):registry.documents('source',reader='alice')


@pytest.mark.parametrize('documents',[
    [Document('doc','Title','Text')],
    (Document('doc','Title','Text'),Document('doc','Other','Text')),
    (Document('doc','','Text'),),(Document('doc','Title',''),),
    (Document('doc','Title','🚀'*16001),),(Document('doc','Title','x'*64001),),
    (Document('doc','Title','\ud800'),),({'id':'doc','readers':['bob']},),
    tuple(Document(str(i),'Title','Text') for i in range(33)),
    tuple(Document(str(i),'Title','x'*64000) for i in range(3)),
])
def test_document_and_payload_bounds(case,documents):
    registry,_,snapshots,*_=case;snapshots[0]=replace(snapshots[0],documents=documents)
    with pytest.raises(ProviderError):registry.refresh('source')
    with pytest.raises(ProviderError):registry.documents('source',reader='alice')


def test_payload_cannot_supply_its_own_audience(case):
    registry,_,snapshots,*_=case
    snapshots[0]={'source_id':'source','readers':['bob'],'confirmed':True}
    with pytest.raises(ProviderError,match='^invalid_snapshot$'):registry.refresh('source')


@pytest.mark.parametrize('field,value',[('minimum_version',True),('minimum_version',0),
    ('ttl_seconds',True),('ttl_seconds',0),('ttl_seconds',86401),('audience',frozenset()),
    ('audience',frozenset({'*'})),('audience',['alice']),('confirmed','yes')])
def test_source_policy_bounds(field,value):
    params=dict(provider_id='provider',source_id='source',audience=frozenset({'alice'}),minimum_version=1,ttl_seconds=60,confirmed=False)
    params[field]=value
    with pytest.raises(ProviderError):SourceSpec(**params)


def test_default_confirmation_false_and_business_provenance_isolation():
    docs=[]
    for business in ('FIRST','SECOND'):
        registry=TrustedProviders(business=business,members=frozenset({'alice'}),clock=lambda:1000)
        registry.register_provider('provider',lambda request,credential:ProviderSnapshot('provider','source',1,1000,(Document('doc','Title','Text'),)),credential=None)
        registry.register_source(SourceSpec('provider','source',frozenset({'alice'}),1,60));registry.refresh('source')
        docs.append(registry.documents('source',reader='alice')[0])
    assert docs[0]['source_id']!=docs[1]['source_id'] and all(d['confirmed'] is False for d in docs)


def test_current_intelligence_opaque_reader_key_format_is_supported():
    reader='reader:'+'a'*64
    registry=TrustedProviders(business='GOLDWAY',members=frozenset({reader}),clock=lambda:1000)
    registry.register_provider('provider',lambda request,credential:ProviderSnapshot('provider','source',1,1000,(Document('doc','Title','Text'),)),credential=None)
    registry.register_source(SourceSpec('provider','source',frozenset({reader}),1,60))
    registry.refresh('source')
    assert registry.documents('source',reader=reader)[0]['readers']==[reader]
