"""SQLite is authoritative; Sheets is an editable projection with optimistic revision checks."""
import json
from datetime import date
from .engine import STATUSES
from .task_transition_history import snapshot as history_snapshot, append_transition

HEADERS=['ID','Задача','Ответственный','Срок_ISO','Следующий_шаг','Критерий','Статус','Источник','Версия','Результат']
EDITABLE={1:'title',2:'owner',3:'deadline',4:'next_step',5:'criterion',6:'status',9:'evidence'}

class Sheets:
    def __init__(self, engine, workbook):
        self.e,self.s,self.book=engine,engine.s,workbook

    @staticmethod
    def all_rows(worksheet):
        # No A1:J150 or A1:J200 cutoffs: includes every populated row.
        return worksheet.get_all_values()

    def sync(self):
        ws=self.book.worksheet('Задачи_GOLDWAY')
        rows=self.all_rows(ws)
        if rows and rows[0]!=HEADERS:
            raise ValueError('Sheet layout differs; use a separate pilot workbook or explicitly migrate it')
        by_id={}
        for index,raw in enumerate(rows[1:],2):
            row=(raw+['']*10)[:10]
            if not row[0]:
                if any(row): raise ValueError('Nonempty row without task ID; import requires review')
                continue
            if row[0] in by_id: raise ValueError('Duplicate task IDs in Sheets')
            by_id[row[0]]=(index,row)
        tasks={t['id']:t for t in self.e.tasks()}
        if set(by_id)-set(tasks): raise ValueError('Unknown Sheets task IDs; explicit initial import required')
        output=[]
        for id,t in tasks.items():
            saved=self.s.execute('SELECT row_json FROM sheet_snapshots WHERE business=? AND task_id=?',(self.e.b,id)).fetchone()
            previous=json.loads(saved[0]) if saved else None
            remote=by_id.get(id,(None,None))[1]
            if previous and remote is None:
                self.s.audit(self.e.b,'sheet_conflict',{'task':id,'reason':'row deleted; resolve manually'},self.e.now())
                continue
            if remote and previous and remote!=previous:
                if t['revision']!=int(previous[8]) or remote[8]!=previous[8] or remote[7]!=previous[7]:
                    self.s.audit(self.e.b,'sheet_conflict',{'task':id,'reason':'concurrent or protected-column edit'},self.e.now())
                    continue
                changes={field:(remote[i] or None if field in ('owner','deadline') else remote[i]) for i,field in EDITABLE.items() if remote[i]!=previous[i]}
                try:
                    self._manual(t,changes)
                except ValueError as exc:
                    self.s.audit(self.e.b,'sheet_rejected',{'task':id,'reason':str(exc)},self.e.now()); continue
                t=dict(self.s.execute('SELECT * FROM tasks WHERE business=? AND id=?',(self.e.b,id)).fetchone())
            elif remote and previous is None:
                self.s.audit(self.e.b,'sheet_conflict',{'task':id,'reason':'missing baseline'},self.e.now()); continue
            source=self.s.execute('SELECT link FROM sources WHERE business=? AND id=?',(self.e.b,t['source_id'])).fetchone()
            desired=[id,t['title'],t['owner'] or '',t['deadline'] or '',t['next_step'],t['criterion'],t['status'],source[0] if source else t['source_id'],str(t['revision']),t['evidence'] or '']
            output.append((id,desired,by_id.get(id,(None,None))[0]))
        if not rows: ws.update(values=[HEADERS],range_name='A1:J1')
        next_row=max(len(rows)+1,2)
        for id,row,index in output:
            if index is None: index=next_row; next_row+=1
            # Absolute range updates are repeatable, unlike blind append.
            ws.update(values=[row],range_name=f'A{index}:J{index}')
            self.s.execute('INSERT INTO sheet_snapshots VALUES(?,?,?) ON CONFLICT(business,task_id) DO UPDATE SET row_json=excluded.row_json',
                (self.e.b,id,json.dumps(row,ensure_ascii=False)))

    def _manual(self,t,changes):
        if changes.get('owner') and changes['owner'] not in self.e.c['employees']: raise ValueError('Unknown owner')
        if changes.get('deadline'): date.fromisoformat(changes['deadline'])
        if 'status' in changes and changes['status'] not in STATUSES: raise ValueError('Invalid status')
        if changes.get('status')=='completed':
            evidence=changes.get('evidence','')
            if len(evidence)<12 or 'почти' in evidence.casefold(): raise ValueError('Concrete new result required')
        if not changes: return
        with self.s.transaction():
            before_history=history_snapshot(self.s,self.e.b,t['id'])
            sql=','.join(f'{k}=?' for k in changes)
            self.s.execute(f'UPDATE tasks SET {sql},revision=revision+1 WHERE business=? AND id=?',(*changes.values(),self.e.b,t['id']))
            append_transition(self.s,self.e.b,t['id'],before_history,at=self.e.now(),source='sheet')
            self.s.audit(self.e.b,'sheet_manual_change',{'task':t['id'],'fields':list(changes)},self.e.now())

    def has_record(self,tab,record_id):
        # Reusable initial-import/read helper for reports and contacts; no writes in phase 1.
        return any(row and row[0]==record_id for row in self.all_rows(self.book.worksheet(tab))[1:])
