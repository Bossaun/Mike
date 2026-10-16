import json
import os

def load(path):
    with open(path) as f: c=json.load(f)
    if c.get('business')!='GOLDWAY': raise ValueError('This pilot only enables GOLDWAY')
    if not c.get('admins') or not c.get('employees'): raise ValueError('Configure employees and admins')
    if any(a not in c['employees'] for a in c['admins']): raise ValueError('Admins need employee identities')
    from .deadline_policy import validate_config as validate_deadline_policy
    validate_deadline_policy(c)
    if 'scheduling' in c:
        scheduling = c['scheduling']
        if (not isinstance(scheduling, dict) or set(scheduling) != {'enabled'}
                or type(scheduling['enabled']) is not bool):
            raise ValueError('Scheduling requires an explicit boolean enabled flag')
    ids=[str(v['telegram_user_id']) for v in c['employees'].values() if v.get('telegram_user_id')]
    if len(ids)!=len(set(ids)): raise ValueError('Duplicate Telegram user IDs')
    if c.get('live_send') and (not c.get('recipients_verified') or not os.environ.get('TELEGRAM_BOT_TOKEN')):
        raise ValueError('Live sends require verified recipients and TELEGRAM_BOT_TOKEN')
    if c.get('sheets_write') and not c.get('sheet_verified'): raise ValueError('Verify pilot workbook before enabling writes')
    if 'test_intake' in c:
        raise ValueError('Use controlled_intake; legacy test_intake is unsupported')
    if 'team_intake' in c:
        from .intake import validate_team
        validate_team(c)
    if 'personal_intake' in c:
        from .intake import validate_personal
        validate_personal(c)
    if 'controlled_intake' in c:
        gate=c['controlled_intake']
        if not isinstance(gate,dict) or gate.get('recipient')!='test_operator':
            raise ValueError('Invalid controlled intake gate')
        start,end=gate.get('started_at'),gate.get('ends_at')
        if type(start) is not int or type(end) is not int or not 0<end-start<=300:
            raise ValueError('Controlled intake requires a window of at most 300 seconds')
        uid=c['employees'].get('test_operator',{}).get('telegram_user_id')
        if type(uid) is not int or uid<=0 or c['admins']!=['test_operator']:
            raise ValueError('Controlled intake requires the known test operator only')
        for flag in ('live_send','sheets_write','allow_onboarding_replies','allow_registered_replies','daily_summary'):
            if c.get(flag) is not False:
                raise ValueError('Controlled intake requires explicitly disabled '+flag)
        if c.get('meeting_recipient') or c.get('allowed_group_ids') or c.get('group_destinations'):
            raise ValueError('Controlled intake cannot have group routes')
        if c.get('allowed_live_recipients',[]) not in ([],['test_operator']):
            raise ValueError('Controlled intake cannot have other outgoing recipients')
    return c
