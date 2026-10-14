import json
import os

def load(path):
    with open(path) as f: c=json.load(f)
    if c.get('business')!='GOLDWAY': raise ValueError('This pilot only enables GOLDWAY')
    if not c.get('admins') or not c.get('employees'): raise ValueError('Configure employees and admins')
    if any(a not in c['employees'] for a in c['admins']): raise ValueError('Admins need employee identities')
    ids=[str(v['telegram_user_id']) for v in c['employees'].values() if v.get('telegram_user_id')]
    if len(ids)!=len(set(ids)): raise ValueError('Duplicate Telegram user IDs')
    if c.get('live_send') and (not c.get('recipients_verified') or not os.environ.get('TELEGRAM_BOT_TOKEN')):
        raise ValueError('Live sends require verified recipients and TELEGRAM_BOT_TOKEN')
    if c.get('sheets_write') and not c.get('sheet_verified'): raise ValueError('Verify pilot workbook before enabling writes')
    return c
