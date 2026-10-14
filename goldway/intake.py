"""Optional fail-closed gates for controlled tests and personal private intake."""

FORBIDDEN_FIELDS = {
    'business_connection_id', 'sender_business_bot', 'sender_chat', 'edit_date',
    'forward_origin', 'forward_from', 'forward_from_chat', 'forward_date',
    'forward_sender_name', 'is_automatic_forward', 'external_reply', 'quote',
    'document', 'photo', 'video', 'animation', 'audio', 'voice', 'sticker',
    'video_note', 'contact', 'location', 'venue', 'poll', 'dice', 'caption',
    'paid_media', 'story', 'game', 'invoice', 'successful_payment',
}


def validate_personal(config):
    gate = config.get('personal_intake')
    if ('controlled_intake' in config or 'test_intake' in config
            or not isinstance(gate, dict)
            or set(gate) != {'recipient', 'started_at'}
            or gate.get('recipient') != 'test_operator'):
        raise ValueError('Invalid or conflicting personal intake gate')
    start = gate['started_at']
    if type(start) is not int or start <= 0:
        raise ValueError('Personal intake requires an integer start time')
    employees = config.get('employees')
    if (not isinstance(employees, dict) or set(employees) != {'test_operator'}
            or config.get('admins') != ['test_operator']
            or not isinstance(employees['test_operator'], dict)):
        raise ValueError('Personal intake requires the test operator only')
    uid = employees['test_operator'].get('telegram_user_id')
    if type(uid) is not int or uid <= 0:
        raise ValueError('Personal intake requires the confirmed operator identity')
    if config.get('allowed_live_recipients') != ['test_operator']:
        raise ValueError('Personal intake requires the operator-only outgoing allowlist')
    if (type(config.get('live_send')) is not bool
            or (config['live_send'] and config.get('recipients_verified') is not True)):
        raise ValueError('Personal sends require explicitly verified recipients')
    for flag in ('sheets_write', 'allow_onboarding_replies', 'allow_registered_replies', 'daily_summary'):
        if config.get(flag) is not False:
            raise ValueError('Personal intake requires explicitly disabled ' + flag)
    if config.get('review_mode') is not True:
        raise ValueError('Personal intake requires review mode')
    if any(config.get(k) for k in ('meeting_recipient', 'allowed_group_ids',
                                  'group_destinations', 'business_account_user_id')):
        raise ValueError('Personal intake cannot have meeting, group or Business routes')
    return uid, start


def accepts_personal(update, config, now):
    try:
        uid, start = validate_personal(config)
    except ValueError:
        return False
    if (now < start or not isinstance(update, dict)
            or type(update.get('update_id')) is not int
            or set(update) != {'update_id', 'message'}):
        return False
    message = update['message']
    # First-stage personal chat uses standalone texts. Bot parent identity is not
    # supplied to this gate, so every Reply fails closed rather than trusting it.
    if (not isinstance(message, dict)
            or not set(message) <= {'message_id', 'date', 'chat', 'from', 'text',
                                    'entities', 'link_preview_options'}
            or not isinstance(message.get('chat'), dict)
            or not isinstance(message.get('from'), dict)):
        return False
    date = message.get('date')
    return (type(message.get('message_id')) is int and message['message_id'] > 0
            and type(date) is int and start <= date <= now
            and message['chat'].get('type') == 'private'
            and type(message['chat'].get('id')) is int
            and message['chat'].get('id') == uid
            and type(message['from'].get('id')) is int
            and message['from'].get('id') == uid
            and not message['from'].get('is_bot', False)
            and isinstance(message.get('text'), str) and bool(message['text'].strip()))


def accepts(update, config, now):
    if 'test_intake' in config:
        return False
    if 'personal_intake' in config:
        return accepts_personal(update, config, now)
    if 'controlled_intake' not in config:
        return True
    gate = config['controlled_intake']
    if not isinstance(gate, dict) or gate.get('recipient') != 'test_operator':
        return False
    start, end = gate.get('started_at'), gate.get('ends_at')
    if type(start) is not int or type(end) is not int:
        return False
    if not (0 < end - start <= 300 and start <= now < end):
        return False
    person = config.get('employees', {}).get('test_operator', {})
    uid = person.get('telegram_user_id')
    if type(uid) is not int or uid <= 0:
        return False
    if not isinstance(update,dict) or type(update.get('update_id')) is not int or set(update) != {'update_id', 'message'}:
        return False
    message = update['message']

    def fresh_private_text(m):
        if not isinstance(m, dict) or FORBIDDEN_FIELDS.intersection(m):
            return False
        if not isinstance(m.get('chat'),dict) or not isinstance(m.get('from'),dict):
            return False
        if type(m.get('message_id')) is not int or m['message_id']<=0:
            return False
        date = m.get('date')
        return (type(date) is int and start <= date <= now and date < end
                and m.get('chat', {}).get('type') == 'private'
                and m.get('chat', {}).get('id') == uid
                and m.get('from', {}).get('id') == uid
                and not m.get('from', {}).get('is_bot', False)
                and isinstance(m.get('text'), str) and bool(m['text'].strip()))

    if not fresh_private_text(message):
        return False
    parent = message.get('reply_to_message')
    if parent is not None:
        if not fresh_private_text(parent) or parent.get('reply_to_message') is not None:
            return False
    return True
