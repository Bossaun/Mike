"""Optional fail-closed filter for a separately authorized five-minute intake test."""

FORBIDDEN_FIELDS = {
    'business_connection_id', 'sender_business_bot', 'sender_chat', 'edit_date',
    'forward_origin', 'forward_from', 'forward_from_chat', 'forward_date',
    'forward_sender_name', 'is_automatic_forward', 'external_reply', 'quote',
    'document', 'photo', 'video', 'animation', 'audio', 'voice', 'sticker',
    'video_note', 'contact', 'location', 'venue', 'poll', 'dice', 'caption',
    'paid_media', 'story', 'game', 'invoice', 'successful_payment',
}


def accepts(update, config, now):
    if 'test_intake' in config:
        return False
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
