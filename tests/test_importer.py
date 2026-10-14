import pytest
from goldway.importer import LEGACY_HEADERS, import_tasks

def rows(owner='Ален',deadline='',status='В работе',proof=''):
    return [LEGACY_HEADERS,['1','Сверить отправки',owner,'','Сверить накладные','Список номеров',status,deadline,proof,'']]

def test_preview_never_modifies_database(env):
    e,s,*_=env
    summary=import_tasks(e,rows(),'/reviewed/export.json')
    assert summary['tasks']==1 and summary['unknown_deadline']==1 and not e.tasks()
    assert not s.rows('SELECT * FROM outbox')

def test_import_idempotent_and_preserves_later_changes(env):
    e,s,*_=env
    assert import_tasks(e,rows(deadline='20.10.2026'),'source',True)['added']==1
    s.execute("UPDATE tasks SET next_step='Обновлённый шаг'")
    assert import_tasks(e,rows(),'source',True)['skipped']==1
    assert e.tasks()[0]['next_step']=='Обновлённый шаг'
    assert e.tasks()[0]['deadline']=='2026-10-20'
    assert not s.rows('SELECT * FROM outbox')

def test_compound_owner_and_relative_deadline_not_inferred(env):
    e,s,*_=env; import_tasks(e,rows(owner='Я + разработчик',deadline='срочно'),'source',True)
    t=e.tasks()[0];assert t['owner'] is None and t['deadline'] is None

@pytest.mark.parametrize('proof',['','почти готово, проверяем'])
def test_old_completed_without_result_requires_review(env,proof):
    e,*_=env;import_tasks(e,rows(status='Выполнено',proof=proof),'source',True)
    assert e.tasks()[0]['status']=='in_progress'

def test_duplicate_ids_fail_before_writes(env):
    e,*_=env;data=rows();data.append(data[1])
    with pytest.raises(ValueError): import_tasks(e,data,'source',True)
    assert not e.tasks()

def test_unexpected_headers_rejected(env):
    e,*_=env
    with pytest.raises(ValueError):import_tasks(e,[['wrong']],'source',True)
