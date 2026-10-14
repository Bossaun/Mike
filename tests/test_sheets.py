import copy
import pytest
from goldway.sheets import Sheets, HEADERS

class Worksheet:
    def __init__(self,rows=None): self.rows=rows or []; self.calls=[]
    def get_all_values(self): return copy.deepcopy(self.rows)
    def update(self,values,range_name):
        self.calls.append(range_name)
        start=int(range_name.split(':')[0][1:])-1
        while len(self.rows)<=start: self.rows.append([])
        for i,row in enumerate(values): self.rows[start+i]=copy.deepcopy(row)
class Workbook:
    def __init__(self,ws): self.ws=ws
    def worksheet(self,name): return self.ws[name]

def test_reads_task_beyond_149(env,seed):
    e,s,*_=env
    for i in range(170):
        s.execute('INSERT INTO tasks(business,id,title,next_step,criterion,status,source_id,created) VALUES(?,?,?,?,?,?,?,?)',(e.b,'extra'+str(i).zfill(3),'Task '+str(i),'next','done','confirmed','source',e.now()))
    ws=Worksheet(); sync=Sheets(e,Workbook({'Задачи_GOLDWAY':ws})); sync.sync()
    assert len(ws.rows)==172
    sync.sync()
    assert len(ws.rows)==172
    assert any(r[0]=='extra169' for r in ws.rows[150:])

@pytest.mark.parametrize('tab',['Ален_день','Касания_GOLDWAY'])
def test_record_in_row_201_found(env,tab):
    e,*_=env
    ws=Worksheet([['ID']]+[[str(i)] for i in range(1,201)])
    sync=Sheets(e,Workbook({tab:ws}))
    assert sync.has_record(tab,'200')
    assert not ws.calls

def test_manual_deadline_round_trip(env,seed):
    e,*_=env; ws=Worksheet(); sync=Sheets(e,Workbook({'Задачи_GOLDWAY':ws})); sync.sync()
    ws.rows[1][3]='2026-10-25'; sync.sync()
    assert e.tasks()[0]['deadline']=='2026-10-25'
    assert ws.rows[1][8]=='2'

def test_concurrent_sheet_edit_is_not_overwritten(env,seed):
    e,s,*_=env; ws=Worksheet(); sync=Sheets(e,Workbook({'Задачи_GOLDWAY':ws})); sync.sync()
    ws.rows[1][3]='2026-10-25'; s.execute('UPDATE tasks SET revision=revision+1,status=?',('blocked',))
    sync.sync()
    assert e.tasks()[0]['deadline']=='2026-10-20'
    assert ws.rows[1][3]=='2026-10-25'
    assert s.rows("SELECT * FROM audit WHERE kind='sheet_conflict'")

@pytest.mark.parametrize('field,value',[(2,'unknown'),(3,'2026-02-30'),(6,'completed'),(8,'100')])
def test_invalid_or_protected_sheet_edit_rejected(env,seed,field,value):
    e,s,*_=env; ws=Worksheet(); sync=Sheets(e,Workbook({'Задачи_GOLDWAY':ws})); sync.sync()
    ws.rows[1][field]=value; sync.sync()
    assert e.tasks()[0]['revision']==1
    assert s.rows("SELECT * FROM audit WHERE kind IN ('sheet_rejected','sheet_conflict')")

def test_existing_sheet_layout_is_not_overwritten(env):
    e,*_=env; ws=Worksheet([['Original layout']]); sync=Sheets(e,Workbook({'Задачи_GOLDWAY':ws}))
    with pytest.raises(ValueError): sync.sync()
    assert not ws.calls
