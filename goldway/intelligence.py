"""Grounded read-only knowledge and current-dialogue context, never action authority."""
import hashlib
import json
import re
from datetime import datetime, timezone

from .dialogue import DialogueMemory, scope_from_payload
from .knowledge import KnowledgeIndex
from .knowledge_sources import ReadOnlySources


def reader_key(business, sender):
    return 'reader:' + hashlib.sha256(json.dumps([business, sender]).encode()).hexdigest()


def intelligence_enabled(config):
    return isinstance(config.get('intelligence'), dict) and config['intelligence'].get('enabled') is True


class Intelligence:
    def __init__(self, store, config):
        self.s, self.c, self.b = store, config, config['business']
        cfg = config['intelligence']
        self.memory = DialogueMemory(store, max_turns=cfg.get('history_messages',8),
                                     max_chars=cfg.get('history_chars',6000))
        self.knowledge_chars = cfg.get('knowledge_chars',7000)
        if type(self.knowledge_chars) is not int or not 512 <= self.knowledge_chars <= 24000:
            raise ValueError('Knowledge context budget must be between 512 and 24000 characters')
        self.index = KnowledgeIndex(store)
        self.prefix = 'source:' + hashlib.sha256(self.b.encode()).hexdigest() + ':'
        members = set(config['employees']) | set(config['admins'])
        specs = []
        for original in cfg.get('sources', []):
            spec = dict(original)
            readers = spec.get('readers')
            if not isinstance(readers, list) or not readers or any(x not in members for x in readers):
                raise ValueError('Knowledge sources require explicit confirmed readers')
            spec['id'] = self.prefix + spec['id']
            spec['readers'] = [self.reader(x) for x in readers]
            if 'reader_emails' in spec:
                spec['reader_emails'] = {self.reader(k): v for k, v in spec['reader_emails'].items() if k in readers}
            specs.append(spec)
        # A restart/config change cannot retain an earlier permission observation.
        for row in store.rows('SELECT DISTINCT source_id FROM knowledge_documents'):
            if row['source_id'].startswith(self.prefix): self.index.revoke_source(row['source_id'])
        # Google credentials must be injected separately; never use ambient account auth.
        self.sources = ReadOnlySources(self.index, specs, {self.reader(x) for x in members},
                                      refresh_seconds=cfg.get('refresh_seconds', 900))
        store.execute('''CREATE TABLE IF NOT EXISTS intelligence_outbox_acl(
            outbox_id INTEGER PRIMARY KEY, business TEXT NOT NULL,
            evidence_ids TEXT NOT NULL)''')
        store.execute('''CREATE TABLE IF NOT EXISTS intelligence_task_acl(
            business TEXT NOT NULL, task_id TEXT NOT NULL, evidence_ids TEXT NOT NULL,
            PRIMARY KEY(business,task_id))''')

    def reader(self, sender):
        return reader_key(self.b, sender)

    def task_refs(self, task_id):
        row = self.s.execute('SELECT evidence_ids FROM intelligence_task_acl WHERE business=? AND task_id=?',
                             (self.b,task_id)).fetchone()
        return set(json.loads(row[0])) if row else set()

    def task_permitted(self, task_id, sender, now):
        return self.task_refs(task_id) <= self.index.allowed_ids(self.reader(sender), now=now)

    def visible_tasks(self, tasks, sender, now):
        allowed = self.index.allowed_ids(self.reader(sender), now=now)
        return [t for t in tasks if self.task_refs(t['id']) <= allowed]

    def protect_outbox(self, outbox_id, evidence_ids):
        row = self.s.execute('SELECT evidence_ids FROM intelligence_outbox_acl WHERE outbox_id=? AND business=?',
                             (outbox_id,self.b)).fetchone()
        refs = set(json.loads(row[0])) if row else set()
        refs.update(evidence_ids)
        self.s.execute('INSERT INTO intelligence_outbox_acl VALUES(?,?,?) ON CONFLICT(outbox_id) DO UPDATE SET evidence_ids=excluded.evidence_ids',
                       (outbox_id,self.b,json.dumps(sorted(refs))))

    def enrich(self, payload, context, now):
        self.sources.refresh(now=now)
        allowed = self.index.allowed_ids(self.reader(payload['sender']), now=now)
        scope = scope_from_payload(payload, self.b)
        dialogue = self.memory.get(scope, readable_ids=allowed, now=now, include_evidence=True) if scope else {
            'turns': [], 'topic': '', 'entities': [], 'pending_offer': None, 'task_id': None}
        # Internal event summaries are neither delivered conversation nor chat scoped.
        context['recent_summaries'] = []
        context['dialogue_evidence_ids'] = dialogue.pop('_evidence_ids', [])
        if len(context['dialogue_evidence_ids']) > 12:
            dialogue = {'turns': [], 'topic': '', 'entities': [], 'pending_offer': None, 'task_id': None}
            context['dialogue_evidence_ids'] = []
        context['dialogue'] = dialogue
        context['intelligence'] = True
        context['capabilities'] = {
            'task_mutations': ('reviewed_admin_actions' if context.get('can_create_tasks') is True
                               else 'proposals_and_permitted_own_task_updates' if context.get('can_submit_proposals') is True
                               else 'permitted_own_task_updates'),
            'knowledge': 'read_only_explicit_sources_and_readers',
            'telegram': 'queued_reply_and_permitted_task_notifications',
            'spreadsheet_tool': False,
            'arbitrary_browser_or_shell': False,
            'external_file_write_tool': False,
        }
        continuation = bool(re.search(r'\b(?:давай|давайте|покороче|короче|описание для клиента|подробнее|втор\w*|этот|этим|это направление|него|уточни|откуда ты|напомни)\b', payload['text'], re.I))
        subjects = list(dialogue['entities']) if continuation else []
        second = re.search(r'\b(?:втор\w*|2[- ]?(?:й|ом|го)?)\s+бренд', payload['text'].casefold())
        if second and len(subjects) >= 2: subjects = [subjects[1]]
        query = payload['text']
        if continuation and dialogue['topic']: query += '\n' + dialogue['topic']
        offer = dialogue['pending_offer']
        if continuation and offer: query += '\n' + offer['text']
        context['knowledge'] = self.index.search(query, self.reader(payload['sender']), now=now,
                                                limit=5, max_chars=self.knowledge_chars, subjects=subjects)
        # Focus helps resolve a short continuation; existing task permission/evidence
        # checks still decide every mutation. No hidden imported task is introduced.
        focus = dialogue['task_id']
        explicit_ref = payload.get('task_id') or re.search(r'(?:№|#|задач[ауеи]?\s*)\s*\d+', payload['text'], re.I)
        if focus:
            owned = payload['sender'] in self.c['admins']
            row = self.s.execute('SELECT * FROM tasks WHERE business=? AND id=?', (self.b, focus)).fetchone()
            if row and (owned or row['owner'] == payload['sender']) and (owned or row['status'] != 'proposed') and self.task_permitted(focus,payload['sender'],now):
                fields = ('id','public_number','title','owner','deadline','next_step','criterion','status','revision')
                if not context.get('tasks') and continuation and not explicit_ref: context['tasks'] = [{key: row[key] for key in fields}]
                if focus not in {t['id'] for t in context.get('tasks', [])}: dialogue['task_id'] = None
            else:
                dialogue['task_id'] = None
        task_refs = set().union(*(self.task_refs(t['id']) for t in context.get('tasks', [])))
        refs = set(self.evidence(context['knowledge'])) | set(context['dialogue_evidence_ids']) | task_refs
        if len(refs) > 16:
            # Never truncate provenance while retaining its protected content.
            context['dialogue'] = {'turns': [], 'topic': '', 'entities': [], 'pending_offer': None, 'task_id': None}
            context['dialogue_evidence_ids'] = []
            context['knowledge'] = {'evidence': [], 'facts': [], 'conflicts': [], 'status': 'no_evidence'}
            kept, task_refs = [], set()
            for task in context.get('tasks', []):
                candidate = task_refs | self.task_refs(task['id'])
                if len(candidate) <= 16:
                    kept.append(task); task_refs = candidate
            context['tasks'] = kept
        context['task_evidence_ids'] = sorted(task_refs)
        return scope

    @staticmethod
    def evidence(knowledge):
        found = {}
        for item in knowledge.get('evidence', []): found[item['id']] = item
        for fact in knowledge.get('facts', []):
            if 'evidence_id' in fact: found.setdefault(fact['evidence_id'], fact)
        for conflict in knowledge.get('conflicts', []):
            for item in conflict.get('values', []):
                if 'evidence_id' in item: found.setdefault(item['evidence_id'], item)
        return found

    def validate(self, result, context, payload, now):
        if not isinstance(result, dict): raise ValueError('Invalid intelligence result')
        state = result.get('dialogue_state')
        if not isinstance(state, dict): raise ValueError('Missing dialogue state')
        # Validate/limit it before the engine starts any transaction/action.
        state = self.memory._state(state)
        if state['task_id'] not in {None, *(t['id'] for t in context.get('tasks', []))}:
            raise ValueError('Unpermitted dialogue task focus')
        mode, refs = result.get('knowledge_mode'), result.get('evidence_ids')
        evidence = self.evidence(context['knowledge'])
        if (mode not in ('grounded','conflict','not_found','not_needed')
                or not isinstance(refs, list) or len(refs) > 12
                or any(not isinstance(v, str) or v not in evidence for v in refs)):
            raise ValueError('Invalid knowledge citations')
        all_refs = set(evidence) | set(context.get('dialogue_evidence_ids', [])) | set(context.get('task_evidence_ids', []))
        if all_refs and not all_refs <= self.index.allowed_ids(self.reader(payload['sender']), now=now):
            raise ValueError('Knowledge permission expired before reply')
        if mode in ('grounded','conflict') and not refs:
            raise ValueError('Grounded answer requires evidence')
        if mode in ('grounded','conflict') and any(not evidence[ref].get('confirmed') for ref in refs):
            raise ValueError('Assumptions cannot ground corporate facts')
        if mode in ('not_found','not_needed') and refs:
            raise ValueError('Ungrounded answer cannot cite evidence')
        pending = context['dialogue']['pending_offer']
        short = re.fullmatch(r'\s*(?:да|давай|давайте|можно|хорошо|ок(?:ей)?)\s*[.!]?\s*', payload['text'], re.I)
        if pending and pending['kind'] != 'confirm_task' and short and result.get('actions'):
            raise ValueError('Accepting an explanation is not task consent')
        if re.search(r'\b(?:задач\w*\s+создавать\s+не\s+надо|не\s+создавай\w*\s+задач|я\s+просто\s+спрашивал)', payload['text'], re.I) and result.get('actions'):
            raise ValueError('User explicitly declined task creation')
        if re.match(r'^\s*напомни\b', payload['text'], re.I) and result.get('actions'):
            raise ValueError('Reminder request cannot silently change an assignment')
        result = dict(result, dialogue_state=state)
        if mode == 'not_found' and not result.get('actions'):
            # A missing source is a concrete limitation, never permission to invent.
            result['reply_text'] = ('В доступных вам источниках нет подтверждённых сведений по этому вопросу. '
                                    'Пришлите подходящий документ или уточните, у кого можно проверить.')
        if mode == 'conflict' and any(c.get('resolution') == 'unresolved' for c in context['knowledge'].get('conflicts', [])):
            result['reply_text'] = 'Источники противоречат друг другу; единого подтверждённого ответа пока нет.\n' + result.get('reply_text', '')
        if refs:
            footers = []
            for ref in dict.fromkeys(refs):
                item = evidence[ref]
                date = item.get('updated_at') or 'дата обновления не указана'
                seen = datetime.fromtimestamp(item['observed_at'], timezone.utc).strftime('%Y-%m-%d')
                label = f"{item['title']} — {item['url']} (обновление: {date}; проверено: {seen})"
                if label not in footers: footers.append(label)
            body = result.get('reply_text', '')
            # Reserve space for authentic citations; URLs cannot be invented by model.
            footer = '\n\nИсточники:\n' + '\n'.join(footers)
            if len(footer) > 1600: raise ValueError('Citation budget exceeded')
            result['reply_text'] = body[:3000-len(footer)] + footer
        return result

    def remember(self, event_id, payload, context, result, scope, first_outbox_id, changed, now):
        rows = self.s.rows('SELECT * FROM outbox WHERE business=? AND id>? ORDER BY id', (self.b, first_outbox_id))
        evidence_ids = sorted(set(self.evidence(context['knowledge'])) | set(context.get('dialogue_evidence_ids', [])) | set(context.get('task_evidence_ids', [])))
        if len(evidence_ids) > 16: raise ValueError('Knowledge provenance budget exceeded')
        # Provenance survives later updates/reminders/summary and permission changes.
        for task_id in set(changed) - context.get('restricted_proposal_ids', set()):
            refs = self.task_refs(task_id) | set(evidence_ids)
            self.s.execute('INSERT INTO intelligence_task_acl VALUES(?,?,?) ON CONFLICT(business,task_id) DO UPDATE SET evidence_ids=excluded.evidence_ids',
                           (self.b,task_id,json.dumps(sorted(refs))))
        for row in rows:
            refs = set(evidence_ids) | (self.task_refs(row['task_id']) if row['task_id'] else set())
            self.protect_outbox(row['id'], refs)
        own = [r for r in rows if r['recipient'] == payload['sender']]
        if scope and own:
            state = dict(result['dialogue_state'])
            visible = {r['task_id'] for r in own if r['task_id']} - context.get('restricted_proposal_ids', set())
            candidates = set(changed) & visible
            if len(candidates) == 1:
                task_id = next(iter(candidates))
                task = self.s.execute('SELECT owner,status FROM tasks WHERE business=? AND id=?', (self.b,task_id)).fetchone()
                if task and (payload['sender'] in self.c['admins'] or
                             (task['owner']==payload['sender'] and task['status']!='proposed')):
                    state['task_id'] = task_id
            self.memory.record(scope, event_id, payload['text'], '\n\n'.join(r['text'] for r in own),
                               state=state, evidence_ids=evidence_ids, at=now, outbox_ids=[r['id'] for r in own])


def delivery_permitted(store, business, recipient, outbox_id, now):
    """Recheck citations immediately before transport; unrelated old output unchanged."""
    tables = {r[0] for r in store.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('intelligence_outbox_acl','intelligence_task_acl')")}
    if not tables: return True
    try:
        refs = set()
        if 'intelligence_outbox_acl' in tables:
            row = store.execute('SELECT evidence_ids FROM intelligence_outbox_acl WHERE outbox_id=? AND business=?',
                                (outbox_id,business)).fetchone()
            if row:
                values = json.loads(row[0])
                if not isinstance(values,list) or any(not isinstance(v,str) for v in values): return False
                refs.update(values)
        if 'intelligence_task_acl' in tables:
            row = store.execute('SELECT a.evidence_ids FROM intelligence_task_acl a JOIN outbox o ON o.business=a.business AND o.task_id=a.task_id WHERE o.id=? AND o.business=?',
                                (outbox_id,business)).fetchone()
            if row:
                values = json.loads(row[0])
                if not isinstance(values,list) or any(not isinstance(v,str) for v in values): return False
                refs.update(values)
        return not refs or refs <= KnowledgeIndex(store).allowed_ids(reader_key(business, recipient), now=now)
    except (ValueError, TypeError):
        return False
