"""Optional fail-closed gates for controlled tests and personal private intake."""

FORBIDDEN_FIELDS = {
    'business_connection_id', 'sender_business_bot', 'sender_chat', 'edit_date',
    'forward_origin', 'forward_from', 'forward_from_chat', 'forward_date',
    'forward_sender_name', 'is_automatic_forward', 'external_reply', 'quote',
    'document', 'photo', 'video', 'animation', 'audio', 'voice', 'sticker',
    'video_note', 'contact', 'location', 'venue', 'poll', 'dice', 'caption',
    'paid_media', 'story', 'game', 'invoice', 'successful_payment',
}


TEAM_RECIPIENTS = ('test_operator', 'frost', 'alen', 'nastya', 'dasha_it')
TEXT_FIELDS = {'message_id', 'date', 'chat', 'from', 'text', 'entities', 'link_preview_options'}


def validate_team(config):
    gate = config.get('team_intake')
    if (any(k in config for k in ('controlled_intake', 'test_intake', 'personal_intake'))
            or not isinstance(gate, dict) or set(gate) != {'recipients', 'started_at'}
            or gate['recipients'] != list(TEAM_RECIPIENTS)
            or type(gate['started_at']) is not int or gate['started_at'] <= 0):
        raise ValueError('Invalid or conflicting closed team intake gate')
    employees = config.get('employees')
    if (not isinstance(employees, dict) or set(employees) != set(TEAM_RECIPIENTS)
            or config.get('admins') != ['test_operator']
            or config.get('team_roster_verified') is not True):
        raise ValueError('Closed team requires the confirmed five-person roster')
    ids = [p.get('telegram_user_id') if isinstance(p, dict) else None for p in employees.values()]
    account = config.get('business_account_user_id')
    if (any(type(uid) is not int or uid <= 0 for uid in ids) or len(set(ids)) != 5
            or type(account) is not int or account <= 0 or account in ids):
        raise ValueError('Closed team requires distinct confirmed employee and account identities')
    if (config.get('allowed_live_recipients') != list(TEAM_RECIPIENTS)
            or type(config.get('live_send')) is not bool
            or config.get('recipients_verified') is not True
            or config.get('review_mode') is not True):
        raise ValueError('Closed team requires verified routes and review mode')
    for flag in ('sheets_write', 'daily_summary', 'allow_onboarding_replies', 'allow_registered_replies'):
        if config.get(flag) is not False:
            raise ValueError('Closed team requires explicitly disabled ' + flag)
    if any(config.get(k) for k in ('meeting_recipient', 'allowed_group_ids', 'group_destinations')):
        raise ValueError('Closed team cannot have meeting or group routes')
    if config.get('bot_user_id_verified') is True:
        bot = config.get('bot_user_id')
        if type(bot) is not int or bot <= 0 or bot in ids or bot == account:
            raise ValueError('Closed team bot identity must be distinct and confirmed')
    return set(ids), account, gate['started_at']


def accepts_team(update, config, now):
    try:
        ids, account, start = validate_team(config)
    except ValueError:
        return False
    if (not isinstance(update, dict) or type(update.get('update_id')) is not int
            or len(update) != 2 or now < start):
        return False
    connection = update.get('business_connection')
    if connection is not None:
        # Accept explicit revocations too; connection.date is creation time,
        # not the time of this rights update. No historical text is ingested.
        rights = connection.get('rights', {}) if isinstance(connection, dict) else None
        return (isinstance(connection, dict)
                and isinstance(connection.get('id'), str) and bool(connection['id'])
                and isinstance(connection.get('user'), dict)
                and type(connection['user'].get('id')) is int
                and connection['user']['id'] == account
                and type(connection.get('is_enabled')) is bool
                and isinstance(rights, dict)
                and ('can_reply' not in rights or type(rights['can_reply']) is bool))
    business = 'business_message' in update
    kind = 'business_message' if business else 'message'
    if set(update) != {'update_id', kind}:
        return False
    message = update[kind]
    allowed = TEXT_FIELDS | {'reply_to_message'} | ({'business_connection_id'} if business else set())
    if (not isinstance(message, dict) or not set(message) <= allowed
            or not isinstance(message.get('from'), dict)):
        return False
    sender = message['from'].get('id')
    conn = message.get('business_connection_id')
    if (type(sender) is not int or sender not in ids
            or (business and (sender == account or not isinstance(conn, str) or not conn))):
        return False

    def fresh_text(part, fields):
        return (isinstance(part, dict) and set(part) <= fields
                and type(part.get('message_id')) is int and part['message_id'] > 0
                and type(part.get('date')) is int and start <= part['date'] <= now
                and isinstance(part.get('chat'), dict) and part['chat'].get('type') == 'private'
                and type(part['chat'].get('id')) is int and part['chat']['id'] == sender
                and isinstance(part.get('from'), dict)
                and isinstance(part.get('text'), str) and bool(part['text'].strip()))

    if not fresh_text(message, allowed) or message['from'].get('is_bot', False):
        return False
    parent = message.get('reply_to_message')
    if parent is None:
        return True
    parent_fields = TEXT_FIELDS | ({'business_connection_id', 'sender_business_bot'} if business else set())
    if (not fresh_text(parent, parent_fields)
            or (business and parent.get('business_connection_id') != conn)):
        return False
    author = parent['from'].get('id')
    if type(author) is not int:
        return False
    if author == sender and not parent['from'].get('is_bot', False) and 'sender_business_bot' not in parent:
        return True
    bot = config.get('bot_user_id') if config.get('bot_user_id_verified') is True else None
    if bot is None:
        return False
    if not business:
        return author == bot and parent['from'].get('is_bot') is True
    via = parent.get('sender_business_bot')
    return (author == account and not parent['from'].get('is_bot', False)
            and isinstance(via, dict) and type(via.get('id')) is int
            and via['id'] == bot and via.get('is_bot') is True)


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
    if 'team_intake' in config:
        return accepts_team(update, config, now)
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
