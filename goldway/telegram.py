import json
import time
import httpx

class DeliveryError(Exception):
    def __init__(self, state, detail, retry_after=0):
        self.state,self.detail,self.retry_after=state,detail,retry_after
        super().__init__(detail)

class Telegram:
    def __init__(self, token, client=None):
        self.client=client or httpx.Client(timeout=40)
        self.base='https://api.telegram.org/bot'+token+'/'

    def call(self, method, **params):
        try:
            r=self.client.post(self.base+method,json=params)
            if r.status_code>=500:
                raise DeliveryError('unknown','Telegram server error; delivery may have happened')
            data=r.json()
        except (httpx.HTTPError,ValueError):
            raise DeliveryError('unknown','Network/invalid response; delivery may have happened') from None
        if not data.get('ok'):
            code=data.get('error_code',r.status_code)
            if method=='editMessageText' and code==400 and 'message is not modified' in data.get('description','').casefold():
                return {'message_id':params['message_id'],'already_applied':True}
            if code==429:
                raise DeliveryError('retry','Rate limited',data.get('parameters',{}).get('retry_after',60))
            # Never log descriptions that might include credentials or arbitrary message text.
            raise DeliveryError('failed',f'Telegram rejected request ({code})')
        return data['result']

    def document(self, file_id):
        file=self.call('getFile',file_id=file_id)
        if type(file.get('file_size')) is not int or not 0 < file['file_size'] <= 64000:
            raise ValueError('Transcript document limit: 64KB')
        try:
            with self.client.stream('GET',self.base.replace('/bot','/file/bot')+file['file_path']) as r:
                r.raise_for_status()
                body=bytearray()
                for chunk in r.iter_bytes(chunk_size=64001):
                    body.extend(chunk)
                    if len(body)>64000: raise ValueError('Transcript document too large')
            text=body.decode('utf-8-sig')
            if not text.strip(): raise ValueError('Empty transcript document')
            return text
        except httpx.HTTPError:
            raise RuntimeError('Telegram document download failed') from None

class Dispatcher:
    def __init__(self, store, config, telegram, now=time.time, weekly_guard=None, personal_list_guard=None, own_impediments_guard=None, task_brief_guard=None, selected_knowledge_guard=None):
        self.s,self.c,self.api,self.now=store,config,telegram,now
        self.b=config['business']
        self.weekly_guard=weekly_guard
        self.personal_list_guard=personal_list_guard
        self.own_impediments_guard=own_impediments_guard
        self.task_brief_guard=task_brief_guard
        self.selected_knowledge_guard=selected_knowledge_guard
        self._selected_knowledge_store,self._selected_knowledge_db=store,store.db

    def _weekly_permitted(self, row, route=None, transport=None):
        if not row['dedupe'].startswith('weekly-review:'):return True
        from .weekly_review_command import cancel
        try:
            if self.weekly_guard is not None and self.weekly_guard(row,route,transport) is True:return True
        except Exception:pass
        cancel(self.s,self.b,row['id'])
        return False

    def _personal_list_permitted(self, row, route=None, transport=None):
        if not row['dedupe'].startswith('personal-reference-list:'):return True
        from .personal_reference_discovery import cancel
        try:
            if self.personal_list_guard is not None and self.personal_list_guard(row,route,transport) is True:return True
        except Exception:pass
        cancel(self.s,self.b,row['id'])
        return False

    def _own_impediments_permitted(self, row, route=None, transport=None):
        if not row['dedupe'].startswith('own-impediments:'):return True
        from .own_impediments import cancel
        try:
            if self.own_impediments_guard is not None and self.own_impediments_guard(row,route,transport) is True:return True
        except Exception:pass
        cancel(self.s,self.b,row['id'])
        return False

    def _task_brief_permitted(self, row, route=None, transport=None):
        if not row['dedupe'].startswith('task-design-brief:'):return True
        from .design_brief_command import cancel_task_brief
        try:
            if self.task_brief_guard is not None and self.task_brief_guard(row,route,transport) is True:return True
        except Exception:pass
        cancel_task_brief(self.s,self.b,row['id'])
        return False

    def _selected_knowledge_permitted(self, row, route=None, transport=None):
        if not row['dedupe'].startswith('selected-knowledge-preview:'):return True
        if self.s is not self._selected_knowledge_store or self.s.db is not self._selected_knowledge_db:return False
        from .selected_knowledge_command import cancel
        try:
            if self.selected_knowledge_guard is not None and self.selected_knowledge_guard(row,route,transport) is True:return True
        except Exception:pass
        if self.s is self._selected_knowledge_store and self.s.db is self._selected_knowledge_db:cancel(self.s,self.b,row['id'])
        return False

    def recover(self):
        self.s.execute("UPDATE outbox SET state='unknown',error='Process stopped during send; manual reconciliation required' WHERE business=? AND state='sending'",(self.b,))

    def route(self, recipient, preferred=None):
        private_only=preferred=='private_only'
        if private_only:preferred=None
        business_only=preferred=='business_only'
        if business_only:preferred='business'
        allowed=self.c.get('allowed_live_recipients')
        if allowed is not None and recipient not in allowed and not (self.c.get('allow_onboarding_replies') and recipient.startswith('pending:')) and not (self.c.get('allow_registered_replies') and preferred is not None and recipient in self.c['employees']): raise DeliveryError('blocked','Recipient outside configured pilot allowlist')
        row=self.s.execute('SELECT * FROM chats WHERE business=? AND recipient=?',(self.b,recipient)).fetchone()
        if not row: raise DeliveryError('blocked','No verified recipient channel')
        if preferred in (None,'business') and row['connection'] and row['business_chat']:
            try:
                live=self.api.call('getBusinessConnection',business_connection_id=row['connection'])
                account=str(live.get('user',{}).get('id',''))
                permitted=account==str(self.c.get('business_account_user_id',''))
                if permitted and live.get('is_enabled') and live.get('rights',{}).get('can_reply') and row['last_inbound'] is not None and 0<=self.now()-row['last_inbound']<86400:
                    return {'chat_id':row['business_chat'],'business_connection_id':row['connection']},'business'
            except DeliveryError:
                pass  # A failed rights lookup cannot authorize a Business send.
        # Explicit Business-first fallback is resolved before the single send.
        # Private inbound (/start) persists bot_chat; it must match this employee.
        employee=self.c.get('employees',{}).get(recipient,{})
        uid=employee.get('telegram_user_id') if isinstance(employee,dict) else None
        bot_fallback=(not business_only and preferred=='business' and self.c.get('business_bot_fallback') is True
                      and type(uid) is int and uid>0 and str(row['bot_chat'])==str(uid))
        if (preferred in (None,'bot') or bot_fallback) and row['bot_chat']:
            return {'chat_id':row['bot_chat']},'bot'
        if not private_only and preferred in (None,'group') and row['group_chat']:
            try:
                info=self.api.call('getChat',chat_id=row['group_chat'])
                if info.get('type','supergroup') not in ('group','supergroup'): raise DeliveryError('blocked','Configured group is not a group chat')
                me=self.api.call('getMe')
                member=self.api.call('getChatMember',chat_id=row['group_chat'],user_id=me['id'])
                if member['status'] in ('creator','administrator') or (member['status']=='member' and info.get('permissions',{}).get('can_send_messages',True)) or (member['status']=='restricted' and member.get('is_member',True) and member.get('can_send_messages')):
                    route={'chat_id':row['group_chat']}
                    if row['group_thread_id'] is not None: route['message_thread_id']=int(row['group_thread_id'])
                    return route,'group'
            except DeliveryError:
                pass
        raise DeliveryError('blocked','Business window closed or rights missing; no permitted fallback')

    def flush(self, should_stop=lambda:False):
        if not self.c.get('live_send',False): return
        for row in self.s.rows("SELECT * FROM outbox WHERE business=? AND state IN ('pending','retry','blocked') AND next_at<=? ORDER BY id LIMIT 50",(self.b,self.now())):
            if should_stop():break
            from .delivery_notices import permitted as work_delivery_permitted
            if not work_delivery_permitted(self.s,self.b,row):continue
            try:
                if not self._weekly_permitted(row):continue
                if not self._personal_list_permitted(row):continue
                if not self._own_impediments_permitted(row):continue
                if not self._task_brief_permitted(row):continue
                if not self._selected_knowledge_permitted(row):continue
                if row['dedupe'].startswith('deadline-policy:'):
                    from .deadline_policy import permit_delivery
                    if not permit_delivery(self.s,self.c,row,self.now()):continue
                from .intelligence import delivery_permitted
                if not delivery_permitted(self.s,self.b,row['recipient'],row['id'],self.now(),self.c):
                    if row['dedupe'].startswith('selected-knowledge-preview:'):
                        from .selected_knowledge_command import cancel
                        cancel(self.s,self.b,row['id']);continue
                    if row['dedupe'].startswith('task-design-brief:'):
                        from .design_brief_command import cancel_task_brief
                        cancel_task_brief(self.s,self.b,row['id']);continue
                    if row['dedupe'].startswith('own-impediments:'):
                        from .own_impediments import cancel
                        cancel(self.s,self.b,row['id']);continue
                    if row['dedupe'].startswith('personal-reference-list:'):
                        from .personal_reference_discovery import cancel
                        cancel(self.s,self.b,row['id']);continue
                    if row['dedupe'].startswith('weekly-review:'):
                        from .weekly_review_command import cancel
                        cancel(self.s,self.b,row['id']);continue
                    raise DeliveryError('blocked','Knowledge authorization expired or changed; regenerate reply')
                try:
                    route,transport=self.route(row['recipient'],row['preferred_transport'])
                except DeliveryError:
                    if row['dedupe'].startswith('selected-knowledge-preview:'):
                        from .selected_knowledge_command import cancel
                        cancel(self.s,self.b,row['id']);continue
                    if row['dedupe'].startswith('task-design-brief:'):
                        from .design_brief_command import cancel_task_brief
                        cancel_task_brief(self.s,self.b,row['id']);continue
                    if row['dedupe'].startswith('own-impediments:'):
                        from .own_impediments import cancel
                        cancel(self.s,self.b,row['id']);continue
                    if row['dedupe'].startswith('personal-reference-list:'):
                        from .personal_reference_discovery import cancel
                        cancel(self.s,self.b,row['id']);continue
                    if row['dedupe'].startswith('weekly-review:'):
                        from .weekly_review_command import cancel
                        cancel(self.s,self.b,row['id']);continue
                    raise
                with self.s.transaction():
                    if not work_delivery_permitted(self.s,self.b,row):continue
                    if not self._weekly_permitted(row,route,transport):continue
                    if not self._personal_list_permitted(row,route,transport):continue
                    if not self._own_impediments_permitted(row,route,transport):continue
                    if not self._task_brief_permitted(row,route,transport):continue
                    if not self._selected_knowledge_permitted(row,route,transport):continue
                    if row['dedupe'].startswith('deadline-policy:') and not permit_delivery(self.s,self.c,row,self.now()):continue
                    # A policy guard may rebuild the body after route looked up rights.
                    if row['dedupe'].startswith('deadline-policy:'):
                        row=dict(self.s.execute('SELECT * FROM outbox WHERE business=? AND id=?',(self.b,row['id'])).fetchone())
                        if not delivery_permitted(self.s,self.b,row['recipient'],row['id'],self.now(),self.c):
                            raise DeliveryError('blocked','Knowledge authorization expired or changed; regenerate reply')
                    if row['dedupe'].startswith('weekly-review:') and not delivery_permitted(self.s,self.b,row['recipient'],row['id'],self.now(),self.c):
                        from .weekly_review_command import cancel
                        cancel(self.s,self.b,row['id']);continue
                    if row['dedupe'].startswith('personal-reference-list:') and not delivery_permitted(self.s,self.b,row['recipient'],row['id'],self.now(),self.c):
                        from .personal_reference_discovery import cancel
                        cancel(self.s,self.b,row['id']);continue
                    if row['dedupe'].startswith('own-impediments:') and not delivery_permitted(self.s,self.b,row['recipient'],row['id'],self.now(),self.c):
                        from .own_impediments import cancel
                        cancel(self.s,self.b,row['id']);continue
                    if row['dedupe'].startswith('task-design-brief:') and not delivery_permitted(self.s,self.b,row['recipient'],row['id'],self.now(),self.c):
                        from .design_brief_command import cancel_task_brief
                        cancel_task_brief(self.s,self.b,row['id']);continue
                    if row['dedupe'].startswith('selected-knowledge-preview:') and not delivery_permitted(self.s,self.b,row['recipient'],row['id'],self.now(),self.c):
                        from .selected_knowledge_command import cancel
                        cancel(self.s,self.b,row['id']);continue
                    # Re-read after route/permission checks: no stale queued text or
                    # model-generated link can bypass the current recipient policy.
                    current=dict(self.s.execute('SELECT * FROM outbox WHERE business=? AND id=?',(self.b,row['id'])).fetchone())
                    if current['recipient']!=row['recipient'] or current['preferred_transport']!=row['preferred_transport']:
                        raise DeliveryError('blocked','Recipient route changed before send')
                    row=current
                    from .recipient_policy import outgoing_text
                    row['text']=outgoing_text(self.c,row['recipient'],row['text'],row['parse_mode'])
                    claimed=self.s.execute("UPDATE outbox SET state='sending',text=?,attempts=attempts+1,transport=?,destination_chat=?,destination_connection=?,destination_thread=? WHERE id=? AND state IN ('pending','retry','blocked')",(row['text'],transport,str(route['chat_id']),route.get('business_connection_id'),route.get('message_thread_id'),row['id'])).rowcount
                if not claimed: continue
                params=dict(route,text=row['text'])
                if row['parse_mode']:params['parse_mode']=row['parse_mode']
                result=self.api.call('sendMessage',**params)
                state='sent'; detail=str(result['message_id'])
                self.s.execute("UPDATE outbox SET state='sent',message_id=?,error=NULL WHERE id=?",(detail,row['id']))
            except DeliveryError as exc:
                state,detail=exc.state,exc.detail
                self.s.execute('UPDATE outbox SET state=?,error=?,next_at=? WHERE id=?',
                    (state,detail,self.now()+max(exc.retry_after,300),row['id']))
            except Exception:
                # A response without a usable message_id could still mean a successful send.
                state,detail='unknown','Unexpected delivery error; reconcile manually'
                self.s.execute("UPDATE outbox SET state='unknown',error=? WHERE id=?",(detail,row['id']))
            self.s.execute('INSERT INTO deliveries(outbox_id,at,state,detail) VALUES(?,?,?,?)',(row['id'],self.now(),state,detail))

class Updates:
    def __init__(self, engine, telegram, bot_id):
        self.e,self.s,self.c,self.api,self.bot_id=engine,engine.s,engine.c,telegram,str(bot_id)
        self.work=None

    def person(self, user_id):
        if user_id is None:return None
        for key,p in self.c['employees'].items():
            if p.get('telegram_user_id') is not None and str(p['telegram_user_id'])==str(user_id): return key
        return None

    def handle(self, update):
        from .intake import accepts
        if not accepts(update, self.c, self.e.now()):
            return
        if self.s.db.in_transaction:
            return self._handle(update)
        with self.s.transaction():
            return self._handle(update)

    def _supersede(self, source):
        for event in self.s.rows("SELECT * FROM events WHERE business=? AND state='pending'",(self.e.b,)):
            p=json.loads(event['payload'])
            if p.get('source_id')==source:
                self.s.execute("UPDATE events SET state='superseded' WHERE business=? AND id=?",(self.e.b,event['id']))

    def _handle(self, update):
        b=self.e.b
        key=f"{b}:update:{update['update_id']}"
        if not self.s.execute('INSERT OR IGNORE INTO runtime(key,value) VALUES(?,?)',(key,'received')).rowcount: return
        if 'business_connection' in update:
            c=update['business_connection']
            if str(c['user']['id'])!=str(self.c.get('business_account_user_id','')): return
            self.s.execute('INSERT INTO connections(business,id,enabled,can_reply) VALUES(?,?,?,?) ON CONFLICT(business,id) DO UPDATE SET enabled=excluded.enabled,can_reply=excluded.can_reply',
                (b,c['id'],int(c['is_enabled']),int(c.get('rights',{}).get('can_reply',False))))
            return
        if 'deleted_business_messages' in update:
            d=update['deleted_business_messages']; conn=d['business_connection_id']
            if not self.s.execute('SELECT 1 FROM connections WHERE business=? AND id=?',(b,conn)).fetchone(): return
            for id in d['message_ids']:
                source=f"business:{conn}:{d['chat']['id']}:{id}"
                self._supersede(source)
                self.s.execute('INSERT INTO sources(business,id,body,link,deleted) VALUES(?,?,?,?,1) ON CONFLICT(business,id) DO UPDATE SET deleted=1',(b,source,'',source))
                self.s.audit(b,'source_deleted',{'source':source},self.e.now())
            for admin in self.c['admins']:
                self.s.queue(b,admin,'Business-сообщение удалено. Связанные задачи требуют сверки; автоматически не отменены.',f"deletion:{update['update_id']}:{admin}",self.e.now())
            return
        kind=next((k for k in ('business_message','edited_business_message','message','edited_message') if k in update),None)
        if not kind: return
        m=update[kind]; user=m.get('from',{})
        cutoff=self.c.get('ignore_messages_before')
        if cutoff is not None and m.get('edit_date',m.get('date',0))<cutoff:return
        if user.get('is_bot') or str(user.get('id'))==self.bot_id or m.get('sender_business_bot'): return
        sender=self.person(user.get('id'))
        if sender is None:
            if user.get('id') is not None and self.c.get('allow_onboarding_replies') and kind=='message' and m.get('chat',{}).get('type')=='private' and m.get('text','').split('@')[0]=='/start':
                uid=str(user['id']);recipient='pending:'+uid
                self.s.execute('INSERT OR IGNORE INTO pending_members(business,user_id,label,first_seen) VALUES(?,?,?,?)',(b,uid,user.get('first_name',''),self.e.now()))
                self.s.execute('INSERT INTO chats(business,recipient,bot_chat) VALUES(?,?,?) ON CONFLICT(business,recipient) DO UPDATE SET bot_chat=excluded.bot_chat',(b,recipient,str(m['chat']['id'])))
                self.s.queue(b,recipient,'Привет! Это помощник GOLDWAY. Заявка на подключение принята. Руководитель должен подтвердить вашу роль; до этого задачи и данные команды недоступны.',f'join:{uid}',self.e.now(),preferred_transport='bot')
            return
        conn=m.get('business_connection_id')
        if conn and str(user.get('id'))==str(self.c.get('business_account_user_id')): return
        if conn:
            c=self.s.execute('SELECT * FROM connections WHERE business=? AND id=?',(b,conn)).fetchone()
            if not c:  # Connection update may predate startup; explicitly read it.
                live=self.api.call('getBusinessConnection',business_connection_id=conn)
                if str(live['user']['id'])!=str(self.c.get('business_account_user_id','')): return
                self.s.execute('INSERT OR REPLACE INTO connections(business,id,enabled,can_reply) VALUES(?,?,?,?)',
                    (b,conn,int(live['is_enabled']),int(live.get('rights',{}).get('can_reply',False))))
            if m['chat']['type']!='private': return
            self.s.execute('INSERT INTO chats(business,recipient,business_chat,connection,last_inbound) VALUES(?,?,?,?,?) ON CONFLICT(business,recipient) DO UPDATE SET business_chat=excluded.business_chat,connection=excluded.connection,last_inbound=MAX(COALESCE(chats.last_inbound,0),excluded.last_inbound)',
                (b,sender,str(m['chat']['id']),conn,m['date']))
        else:
            # Ordinary private incoming proves the employee has started the bot.
            if m['chat']['type']=='private':
                self.s.execute('INSERT INTO chats(business,recipient,bot_chat) VALUES(?,?,?) ON CONFLICT(business,recipient) DO UPDATE SET bot_chat=excluded.bot_chat',(b,sender,str(m['chat']['id'])))
            else:
                group=str(m['chat']['id'])
                candidate=m.get('text',m.get('caption',''))
                username=self.c.get('bot_username','').casefold()
                parent=m.get('reply_to_message',{})
                directed=(bool(m.get('document')) or candidate.startswith(('/meeting','/summary','/start'))
                          or (username and '@'+username in candidate.casefold())
                          or bool(self.e.reference_ids(candidate))
                          or candidate.casefold().startswith(('планёрка:','планерка:'))
                          or any(candidate.startswith(label+':') for label in self.c.get('speaker_aliases',{}))
                          or str(parent.get('from',{}).get('id'))==self.bot_id
                          or candidate.casefold().strip() in ('сводка','дай сводку','покажи задачи','список задач','что по задачам','итоги планёрки'))
                if not directed:return
                if group not in [str(x) for x in self.c.get('allowed_group_ids',[])]: return
                self.s.execute('INSERT INTO chats(business,recipient,group_chat,group_thread_id) VALUES(?,?,?,?) ON CONFLICT(business,recipient) DO UPDATE SET group_chat=excluded.group_chat,group_thread_id=excluded.group_thread_id',(b,sender,group,m.get('message_thread_id') if m.get('message_thread_id')!=1 else None))
        if self.work is not None and self.work.handle_private(m,sender,conn):
            return
        text=m.get('text',m.get('caption',''))
        source=f"business:{conn}:{m['chat']['id']}:{m['message_id']}" if conn else f"telegram:{m['chat']['id']}:{m['message_id']}"
        from .answer_citations import observe_inbound
        if m.get('document') and sender in self.c['admins']:
            doc=m['document']
            if not doc.get('file_name','').lower().endswith('.txt') or doc.get('file_size',0)>64000: return
            observe_inbound(self.e,f"update:{update['update_id']}",sender,'business' if conn else 'bot',source,'accepted attachment')
            text=self.api.document(doc['file_id'])
            if 'team_intake' in self.c and len(text)>self.c.get('max_transcript_chars',32000):
                self.s.queue(b,sender,
                    'Текст планёрки слишком большой. Разделите его на отдельные части и отправьте каждую отдельным файлом .txt.',
                    f"attachment-limit:{update['update_id']}:{sender}",self.e.now(),
                    preferred_transport='business' if conn else 'bot')
                return
        if not text: return
        if not m.get('document') and text.startswith('/'):
            head,*rest=text.split(' ',1)
            if '@' in head:
                command,target=head.split('@',1)
                if self.c.get('bot_username') and target.casefold()!=self.c['bot_username'].casefold():return
                text=command+(' '+rest[0] if rest else '')
        elif not m.get('document') and self.c.get('bot_username'):
            text=text.replace('@'+self.c['bot_username'],'').strip()
        observe_inbound(self.e,f"update:{update['update_id']}",sender,'business' if conn else ('bot' if m['chat']['type']=='private' else 'group'),source,text)
        if not m.get('document') and not self.e.intelligence and text.casefold() in ('привет','здравствуйте','добрый день','hello'):
            self.s.queue(b,sender,'Привет! Пишите обычным текстом: задайте вопрос, предложите идею, пришлите планёрку или сообщите результат работы. Если нужно уточнить задачу, назовите её или ответьте на карточку. Команды не нужны.',f"hello:{update['update_id']}:{sender}",self.e.now(),preferred_transport='business' if conn else ('bot' if m['chat']['type']=='private' else 'group'))
            return
        if not m.get('document') and text=='/start':
            self.s.queue(b,sender,'Вы подключены к помощнику GOLDWAY. Можно общаться обычным текстом, прислать идею или планёрку. Например: «Как идут наши задачи?» или «Есть идея улучшить упаковку». Команды не нужны.',f"start:{update['update_id']}:{sender}",self.e.now(),preferred_transport='business' if conn else ('bot' if m['chat']['type']=='private' else 'group'))
            return
        if kind.startswith('edited'):
            self._supersede(source)
            self.s.audit(b,'source_edited',{'source':source},self.e.now())
            for admin in self.c['admins']:
                self.s.queue(b,admin,f'Сообщение {source} отредактировано. Проверьте прежние поручения.',f"edit:{update['update_id']}:{admin}",self.e.now())
        task_id=None
        parent=m.get('reply_to_message',{})
        if parent:
            # Match recipient AND channel to prevent accidental cross-chat task association.
            row=self.s.execute("SELECT task_id FROM outbox WHERE business=? AND (recipient=? OR transport='group') AND message_id=? AND transport=? AND destination_chat=? AND COALESCE(destination_connection,'')=? AND COALESCE(destination_thread,0)=? AND state='sent' ORDER BY id DESC LIMIT 1",
                (b,sender,str(parent.get('message_id')), 'business' if conn else ('bot' if m['chat']['type']=='private' else 'group'),str(m['chat']['id']),conn or '',(m.get('message_thread_id') or 0) if m.get('message_thread_id')!=1 else 0)).fetchone()
            task_id=row[0] if row else None
        personal_operator=self.e._task_dialogue_turn({'sender':sender,'channel':'business' if conn else ('bot' if m['chat']['type']=='private' else 'group')},'reply')
        if not m.get('document') and sender in self.c['admins'] and (text.strip()=='/summary' or text.casefold().strip() in ('сводка','дай сводку','покажи задачи','список задач','что по задачам','итоги планёрки')):
            self.e.queue_summary(f"request:{update['update_id']}")
        elif not ('team_intake' in self.c and m.get('document')) and sender in self.c['admins'] and (text.startswith('/meeting ') or (not personal_operator and not parent and (m.get('document') or (len(text)>400 or any(text.casefold().startswith(person['name'].casefold()+':') for person in self.c['employees'].values()) or any(text.startswith(label+':') for label in self.c.get('speaker_aliases',{}))) and not text.startswith('/') and not self.e.reference_ids(text)))):
            self.e.transcript(text.removeprefix('/meeting '),source)
        else:
            self.e.reply(f"update:{update['update_id']}",sender,text,task_id,source,'business' if conn else ('bot' if m['chat']['type']=='private' else 'group'),message_at=m.get('date'))


def bootstrap_groups(store,config):
    """Persist explicit group destinations on a fresh server; rights checked at send time."""
    allowed={str(x) for x in config.get('allowed_group_ids',[])}
    with store.transaction():
        for recipient,target in config.get('group_destinations',{}).items():
            chat_id=str(target['chat_id'])
            if recipient in config['employees'] or recipient.startswith('pending:'):
                raise ValueError('Group recipient must be separate from employee identities')
            if chat_id not in allowed or not chat_id.startswith('-') or not chat_id[1:].isdigit():
                raise ValueError('Group destination must match the explicit group allowlist')
            thread=target.get('message_thread_id')
            if thread is not None and (type(thread) is not int or thread<1):
                raise ValueError('Invalid forum topic ID')
            if thread==1:thread=None
            store.execute('INSERT INTO chats(business,recipient,group_chat,group_thread_id) VALUES(?,?,?,?) ON CONFLICT(business,recipient) DO UPDATE SET group_chat=excluded.group_chat,group_thread_id=excluded.group_thread_id',
                          (config['business'],recipient,chat_id,thread))
