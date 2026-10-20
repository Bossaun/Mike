"""Grounded read-only knowledge and current-dialogue context, never action authority."""
import hashlib
import json
import re
from datetime import datetime, timezone

from .dialogue import DialogueMemory, scope_from_payload
from .knowledge import KnowledgeIndex
from .knowledge_sources import ReadOnlySources
from .work_context import (collect_work_context, is_operational_query,
                           readable_work_ids, validate_work_snapshot)


def reader_key(business, sender):
    return 'reader:' + hashlib.sha256(json.dumps([business, sender]).encode()).hexdigest()


def intelligence_enabled(config):
    return isinstance(config.get('intelligence'), dict) and config['intelligence'].get('enabled') is True


def readable_evidence_ids(store, config, sender, now):
    """Current source grants AND task-role grants; never a roster-wide task grant."""
    if sender not in set(config.get('employees', {})) | set(config.get('admins', [])):
        return set()
    docs = KnowledgeIndex(store).allowed_ids(reader_key(config['business'], sender), now=now)
    return docs | readable_work_ids(store, config, sender, docs)


def trusted_injection(config,registry,bindings):
    """Host-only preflight, deliberately before Store/index/memory/clock effects."""
    if registry is None and type(bindings) is tuple and not bindings:return ()
    try:
        if registry is None or type(bindings) is not tuple or not 1<=len(bindings)<=16 or not intelligence_enabled(config):raise ValueError()
        from .trusted_source_bridge import TrustedSourceBridge
        business=config['business'];members=set(config['employees'])|set(config['admins'])
        prefix='source:'+hashlib.sha256(business.encode()).hexdigest()+':'
        existing=tuple(prefix+spec['id'] for spec in config['intelligence'].get('sources',[]))
        return TrustedSourceBridge.validate_bindings(registry,bindings,business=business,
            members={reader_key(business,sender) for sender in members},existing_source_ids=existing)
    except Exception:raise ValueError('trusted_injection_invalid') from None


def _owned_trusted_sources(store,business):
    """Bounded ownership metadata only; validate every ID before any revoke."""
    try:
        exists=store.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trusted_source_versions' LIMIT 1").fetchone()
        if not exists:return ()
        rows=store.execute("""SELECT CASE WHEN typeof(business)='text' AND length(CAST(business AS BLOB))<=64 THEN business END AS business,
            CASE WHEN typeof(source_id)='text' AND length(CAST(source_id AS BLOB))<=202 THEN source_id END AS source_id
            FROM trusted_source_versions WHERE business=? LIMIT 65""",(business,)).fetchall()
        if len(rows)>64:raise ValueError()
        ids=[]
        for row in rows:
            source=row['source_id']
            if row['business']!=business or type(source) is not str or re.fullmatch(r'trusted:[A-Za-z0-9._-]{1,64}:[A-Za-z0-9._-]{1,64}:[A-Za-z0-9._-]{1,64}',source) is None or source.split(':')[1]!=business:raise ValueError()
            ids.append(source)
        if len(set(ids))!=len(ids):raise ValueError()
        return tuple(ids)
    except Exception:raise ValueError('trusted_initialization_unavailable') from None


class Intelligence:
    def __init__(self, store, config, *, trusted_registry=None, trusted_sources=(), google_credentials=None):
        trusted_ids=trusted_injection(config,trusted_registry,trusted_sources)
        if google_credentials is not None:
            scopes=set(getattr(google_credentials,'scopes',()) or ())
            if (not intelligence_enabled(config)
                    or scopes != {'https://www.googleapis.com/auth/drive.readonly'}
                    or not callable(getattr(google_credentials,'before_request',None))
                    or not any(s.get('kind')=='drive' for s in config['intelligence'].get('sources',[]))):
                raise ValueError('knowledge_credentials_invalid')
        owned_ids=_owned_trusted_sources(store,config['business'])
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
        # Proved owned IDs include omitted bindings and default-None restarts.
        for source in sorted(set(owned_ids)|set(trusted_ids)):self.index.revoke_source(source)
        # A restart/config change cannot retain an earlier permission observation.
        for row in store.rows('SELECT DISTINCT source_id FROM knowledge_documents'):
            if row['source_id'].startswith(self.prefix): self.index.revoke_source(row['source_id'])
        # Google credentials must be injected separately; never use ambient account auth.
        self.sources = ReadOnlySources(self.index, specs, {self.reader(x) for x in members},
                                      google_credentials=google_credentials,
                                      refresh_seconds=cfg.get('refresh_seconds', 900),
                                      trusted_registry=trusted_registry,trusted_sources=trusted_sources,business=self.b)
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
        return self.task_refs(task_id) <= self.readable_ids(sender, now)

    def readable_ids(self, sender, now):
        return readable_evidence_ids(self.s, self.c, sender, now)

    def visible_tasks(self, tasks, sender, now):
        allowed = self.readable_ids(sender, now)
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
        context.pop('source_health', None)
        health = self._source_health(payload['sender'])
        if health is not None:context['source_health'] = health
        allowed = self.readable_ids(payload['sender'], now)
        memory_allowed = allowed
        if self._independent_task_request(payload, context):
            # An earlier multi-person overview is not evidence for an unrelated
            # new assignment. Exclude its CONTENT and state before the model,
            # rather than dropping confidentiality refs after the model saw it.
            memory_allowed = {ref for ref in allowed if not ref.startswith('work-task:')}
        scope = scope_from_payload(payload, self.b)
        dialogue = self.memory.get(scope, readable_ids=memory_allowed, now=now, include_evidence=True) if scope else {
            'turns': [], 'topic': '', 'entities': [], 'pending_offer': None, 'task_id': None}
        # Internal event summaries are neither delivered conversation nor chat scoped.
        context['recent_summaries'] = []
        context['dialogue_evidence_ids'] = dialogue.pop('_evidence_ids', [])
        last_context_kind = dialogue.pop('_last_context_kind', None)
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
        from .dialogue_focus import build_followup
        context.pop('followup', None)
        followup = build_followup(payload['text'], dialogue)
        if followup:
            context['followup'] = followup
        continuation = bool(re.search(r'\b(?:давай|давайте|покороче|короче|описание для клиента|подробнее|втор\w*|этот|этим|это направление|него|уточни|откуда ты|напомни)\b', payload['text'], re.I))
        subjects = list(dialogue['entities']) if continuation else []
        second = re.search(r'\b(?:втор\w*|2[- ]?(?:й|ом|го)?)\s+бренд', payload['text'].casefold())
        if second and len(subjects) >= 2: subjects = [subjects[1]]
        query = payload['text']
        if continuation and dialogue['topic']: query += '\n' + dialogue['topic']
        offer = dialogue['pending_offer']
        if continuation and offer: query += '\n' + offer['text']
        if followup:
            # Retrieval follows the concrete delivered answer, even when the
            # model left an older broad topic/entities in its replacement state.
            query = payload['text'] + '\n' + followup['target_answer']
            if followup['kind'] == 'explain_entity':
                subjects = [followup['selected_entity']]
                query = payload['text'] + '\n' + followup['selected_entity']
            else:
                # All last-answer follow-ups must avoid mandatory filters from
                # an older copied entity list, including bare acceptance.
                subjects = []
        context['knowledge'] = self.index.search(query, self.reader(payload['sender']), now=now,
                                                limit=5, max_chars=self.knowledge_chars, subjects=subjects)
        # Documents and current task records are different sources. A read-only
        # business question must not be limited to the public product catalog or
        # accidentally acquire mutation authority from an operational snapshot.
        context.pop('work_context', None)
        operational_question = is_operational_query(payload['text'])
        if operational_question or (followup and last_context_kind == 'work'):
            context['work_context'] = collect_work_context(
                self.s, self.c, payload['sender'], allowed, now, payload['text'])
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
        refs = set(self.evidence(context['knowledge'], context.get('work_context'))) | set(context['dialogue_evidence_ids']) | task_refs
        if len(refs) > 16:
            # Never truncate provenance while retaining its protected content.
            context.pop('followup', None)
            context['dialogue'] = {'turns': [], 'topic': '', 'entities': [], 'pending_offer': None, 'task_id': None}
            context['dialogue_evidence_ids'] = []
            context['knowledge'] = {'evidence': [], 'facts': [], 'conflicts': [], 'status': 'no_evidence'}
            # Retain the bounded current work projection for every collected
            # route, including short follow-ups: trimming older context must
            # never turn a read-only explanation into an action-capable route.
            kept, task_refs = [], set()
            current_refs = set(self.evidence(context['knowledge'], context.get('work_context')))
            for task in context.get('tasks', []):
                candidate = task_refs | self.task_refs(task['id'])
                if len(candidate | current_refs) <= 16:
                    kept.append(task); task_refs = candidate
            context['tasks'] = kept
        context['task_evidence_ids'] = sorted(task_refs)
        from .answer_citations import seal_answer_context
        seal_answer_context(self.s,self.c,payload,context,now)
        return scope

    def _source_health(self, sender):
        """Non-grounding last-status counts, only current readable registrations.

        No source identity, provider text, grant, retrieval success or clock is
        projected. An ok status is not proof of current freshness/completeness.
        """
        members = set(self.c.get('employees', {})) | set(self.c.get('admins', []))
        if sender not in members:return None
        reader = self.reader(sender)
        readable = set()
        for spec in self.sources.specs:
            source, readers = spec.get('id'), spec.get('readers')
            if (type(source) is str and source.startswith(self.prefix)
                    and type(readers) is list and reader in readers
                    and reader in self.sources.members):
                readable.add(source)
        # Optional explicitly injected I01 registrations, never source discovery.
        bridge = getattr(self.sources, 'trusted_bridge', None)
        if bridge is not None and bridge.business == self.b:
            for binding in bridge.bindings:
                if reader in binding.audience and reader in self.sources.members:
                    readable.add(bridge.source_id(binding))
        if len(readable)>16:raise ValueError('Source health registration bound exceeded')
        counts = dict(ok=0, blocked=0, unavailable=0, unknown=0)
        for source in readable:
            status = self.sources.status.get(source)
            code = status if type(status) is str and status in counts else 'unknown'
            counts[code] += 1
        return dict(non_grounding=True, registered_readable=len(readable),
                    last_refresh_counts=counts)

    @staticmethod
    def _knowledge_fallback(health):
        generic = ('В доступных вам источниках нет подтверждённых сведений по этому вопросу. '
                   'Могу опираться на подключённые документы и доступные рабочие задачи; '
                   'чтобы ответить конкретно, пришлите актуальную справку по этой теме.')
        if type(health) is not dict or health.get('non_grounding') is not True:return generic
        counts = health.get('last_refresh_counts')
        total = health.get('registered_readable')
        if (type(total) is not int or not 0<=total<=16 or type(counts) is not dict
                or set(counts)!={'ok','blocked','unavailable','unknown'}
                or any(type(value) is not int or not 0<=value<=16 for value in counts.values())
                or sum(counts.values())!=total):return generic
        if not total:
            return ('Для вас не зарегистрированы доступные источники знаний. '
                    'Поэтому подтвердить сведения по этому вопросу не могу. '
                    'Пришлите актуальную справку для разбора; доступ к источникам сам не расширяю.')
        failed = counts['blocked']+counts['unavailable']
        if failed:
            extent = 'Все зарегистрированные для вас источники' if failed==total else 'Часть зарегистрированных для вас источников'
            return (extent+' при последнем обращении были недоступны или заблокированы. '
                    'Подтверждённых сведений по этому вопросу у меня нет; '
                    'это не означает, что таких сведений нет в самих источниках. '
                    'Пришлите актуальную справку или проверьте доступность источников.')
        if counts['unknown']:
            return ('Состояние зарегистрированных для вас источников пока не подтверждено. '
                    'Подтверждённых сведений по этому вопросу у меня нет; '
                    'пришлите актуальную справку для разбора.')
        return ('В доступных вам источниках не найдено подходящих подтверждённых сведений по этому вопросу. '
                'Последнее обращение к зарегистрированным для вас источникам прошло без сообщения об ошибке; '
                'это не подтверждает полноту или актуальность их данных. '
                'Пришлите актуальную справку по этой теме.')

    @staticmethod
    def _independent_task_request(payload, context):
        text = payload['text']
        if context.get('kind') != 'operator_chat' or is_operational_query(text):
            return False
        explicit_task = (payload.get('task_id')
                         or re.search(r'(?:№|#|задач[ауеи]?\s*)\s*\d+', text, re.I)
                         or any(t.get('title') and t['title'].casefold() in text.casefold()
                                for t in context.get('tasks', [])))
        creation = re.search(
            r'\b(?:созда[йт]\w*|постав[ьи]\w*|запиш\w*|добав[ьи]\w*|зафиксир\w*)\s+'
            r'(?:(?:новую|тестовую|мне|нам|пожалуйста)\s+)*(?:задач\w*|поручени\w*|иде[юя])\b'
            r'|^\s*(?:новая\s+)?задача\s*:', text, re.I)
        return bool(explicit_task or creation)

    @staticmethod
    def evidence(knowledge, work_context=None):
        found = {}
        for item in knowledge.get('evidence', []): found[item['id']] = item
        for fact in knowledge.get('facts', []):
            if 'evidence_id' in fact: found.setdefault(fact['evidence_id'], fact)
        for conflict in knowledge.get('conflicts', []):
            for item in conflict.get('values', []):
                if 'evidence_id' in item: found.setdefault(item['evidence_id'], item)
        for item in (work_context or {}).get('evidence', []):
            found[item['id']] = item
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
        evidence = self.evidence(context['knowledge'], context.get('work_context'))
        if (mode not in ('grounded','conflict','not_found','not_needed')
                or not isinstance(refs, list) or len(refs) > 12
                or any(not isinstance(v, str) or v not in evidence for v in refs)):
            raise ValueError('Invalid knowledge citations')
        all_refs = set(evidence) | set(context.get('dialogue_evidence_ids', [])) | set(context.get('task_evidence_ids', []))
        readable = self.readable_ids(payload['sender'], now)
        if all_refs and not all_refs <= readable:
            raise ValueError('Knowledge permission expired before reply')
        for item in context.get('work_context', {}).get('evidence', []):
            if not validate_work_snapshot(self.s, self.c, payload['sender'], readable, item):
                raise ValueError('Work evidence changed before reply')
        if mode in ('grounded','conflict') and not refs:
            raise ValueError('Grounded answer requires evidence')
        if mode in ('grounded','conflict') and any(not evidence[ref].get('confirmed') for ref in refs):
            raise ValueError('Assumptions cannot ground corporate facts')
        if mode in ('not_found','not_needed') and refs:
            raise ValueError('Ungrounded answer cannot cite evidence')
        work = context.get('work_context')
        if work and result.get('actions'):
            # This is an explicitly read-only question/explanation route. A
            # misclassified model result cannot turn it into a mutation.
            refs = [item['id'] for item in work.get('evidence', [])]
            result = dict(result, actions=[], intent='conversation',
                          summary='Дан обзор записей задач без изменений.',
                          reply_text=self._work_fallback(work) if refs else '',
                          evidence_ids=refs, knowledge_mode='grounded' if refs else 'not_found')
            state = dict(state, pending_offer=None)
            mode = result['knowledge_mode']
        pending = context['dialogue']['pending_offer']
        short = re.fullmatch(r'\s*(?:да|давай|давайте|можно|хорошо|ок(?:ей)?)\s*[.!]?\s*', payload['text'], re.I)
        if pending and pending['kind'] != 'confirm_task' and short and result.get('actions'):
            raise ValueError('Accepting an explanation is not task consent')
        if re.search(r'\b(?:задач\w*\s+создавать\s+не\s+надо|не\s+создавай\w*\s+задач|я\s+просто\s+спрашивал)', payload['text'], re.I) and result.get('actions'):
            raise ValueError('User explicitly declined task creation')
        if re.match(r'^\s*напомни\b', payload['text'], re.I) and result.get('actions'):
            raise ValueError('Reminder request cannot silently change an assignment')
        # A completed public-catalog offer can be rendered from current checked
        # evidence; a model's second promise is not the promised explanation.
        from .catalog_explanation import build_catalog_explanation
        catalog_explanation = build_catalog_explanation(context, result)
        if catalog_explanation is not None:
            text, source_refs, ordered_names = catalog_explanation
            refs, names = list(source_refs), list(ordered_names)
            result = dict(result, reply_text=text, evidence_ids=refs, knowledge_mode='grounded')
            mode = 'grounded'
            state = dict(state, topic='Публичный каталог GOLDWAY', entities=names,
                         pending_offer=None, task_id=None)
        result = dict(result, dialogue_state=state)
        if mode == 'not_found' and not result.get('actions'):
            # Preserve the no-invention guard, but explain the actual source
            # boundary or show recorded work rather than a generic dead end.
            if work and work.get('evidence'):
                result['reply_text'] = self._work_fallback(work)
                refs = [item['id'] for item in work['evidence']]
                result.update(evidence_ids=refs, knowledge_mode='grounded')
            elif work and work.get('scope') == 'current_authorized_registered_task_result':
                result['reply_text'] = (
                    'В доступных мне записях нет зарегистрированного подтверждённого результата этой задачи. '
                    'Текущий статус задачи и текст переписки сами по себе не подтверждают результат. '
                    'Это не означает, что работа не выполнена; нужен явно зарегистрированный результат.')
            elif work:
                result['reply_text'] = (
                    'Подтверждённого обзора проблем бизнеса у меня пока нет. '
                    'В доступных мне рабочих задачах нет записей для такого обзора; '
                    'это не означает, что проблем нет. Публичный каталог описывает ассортимент, '
                    'а не внутреннюю работу. Пришлите свежие итоги планёрки с проблемами и решениями '
                    '— разберу их и покажу поручения на согласование.')
            else:
                result['reply_text'] = self._knowledge_fallback(context.get('source_health'))
        if mode == 'conflict' and any(c.get('resolution') == 'unresolved' for c in context['knowledge'].get('conflicts', [])):
            result['reply_text'] = 'Источники противоречат друг другу; единого подтверждённого ответа пока нет.\n' + result.get('reply_text', '')
        if refs:
            footers = []
            for ref in dict.fromkeys(refs):
                item = evidence[ref]
                seen = datetime.fromtimestamp(item['observed_at'], timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
                if item.get('source_kind') == 'task':
                    label = f"Реестр задач Mike: №{item['public_number']} (состояние на {seen})"
                else:
                    date = item.get('updated_at') or 'дата обновления не указана'
                    seen = seen.split(' ')[0]
                    label = f"{item['title']} — {item['url']} (обновление: {date}; проверено: {seen})"
                if label not in footers: footers.append(label)
            body = result.get('reply_text', '')
            # Reserve space for authentic citations; URLs cannot be invented by model.
            footer = '\n\nИсточники:\n' + '\n'.join(footers)
            if len(footer) > 1600: raise ValueError('Citation budget exceeded')
            result['reply_text'] = body[:3000-len(footer)] + footer
        return result

    @staticmethod
    def _work_fallback(work):
        labels = {'proposed': 'черновик', 'confirmed': 'согласовано',
                  'in_progress': 'в работе', 'blocked': 'есть препятствие'}
        lines = ['По доступным записям задач вижу следующие рабочие пункты:']
        for item in work['evidence']:
            line = f"• №{item['public_number']} {item['title']} — {labels.get(item['status'], item['status'])}"
            if item.get('deadline'): line += f"; срок {item['deadline']}"
            else: line += '; срок не указан'
            if not item.get('owner'): line += '; ответственный не указан'
            if item['status'] == 'blocked' and item.get('next_step'):
                line += f". Записанный следующий шаг: {item['next_step']}"
            lines.append(line)
        lines.append('Это выборка состояния задач, а не полный диагноз бизнеса; '
                     'фактические причины и результаты нужно подтверждать.')
        return '\n'.join(lines)

    def remember(self, event_id, payload, context, result, scope, first_outbox_id, changed, now):
        rows = self.s.rows('SELECT * FROM outbox WHERE business=? AND id>? ORDER BY id', (self.b, first_outbox_id))
        evidence_ids = sorted(set(self.evidence(context['knowledge'], context.get('work_context'))) | set(context.get('dialogue_evidence_ids', [])) | set(context.get('task_evidence_ids', [])))
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
            from .answer_citations import capture_answer
            capture_answer(self.s,self.c,event_id,payload,result,evidence_ids,own,now)
            state = dict(result['dialogue_state'])
            visible = {r['task_id'] for r in own if r['task_id']} - context.get('restricted_proposal_ids', set())
            candidates = set(changed) & visible
            if len(candidates) == 1:
                task_id = next(iter(candidates))
                task = self.s.execute('SELECT owner,status FROM tasks WHERE business=? AND id=?', (self.b,task_id)).fetchone()
                if task and (payload['sender'] in self.c['admins'] or
                             (task['owner']==payload['sender'] and task['status']!='proposed')):
                    state['task_id'] = task_id
            from .consent_anchor import make_memory_anchor
            anchor = make_memory_anchor(self.s, self.b, scope, event_id, state, own, now)
            self.memory.record(scope, event_id, payload['text'], '\n\n'.join(r['text'] for r in own),
                               state=state, evidence_ids=evidence_ids, at=now, outbox_ids=[r['id'] for r in own],
                               context_kind='work' if context.get('work_context') else None, anchor=anchor)


def delivery_permitted(store, business, recipient, outbox_id, now, config=None):
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
        if not refs:
            return True
        if config is not None:
            return (config.get('business') == business
                    and refs <= readable_evidence_ids(store, config, recipient, now))
        # Existing callers without a profile can validate document grants only;
        # they cannot authorize new task-role evidence without current roles.
        return refs <= KnowledgeIndex(store).allowed_ids(reader_key(business, recipient), now=now)
    except (ValueError, TypeError):
        return False
