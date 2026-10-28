"""Owner-approved recipient boundaries for the existing closed GOLDWAY team.

These rules restrict output; they never grant source access or add recipients.
The final dispatcher check also covers messages queued before this release.
"""
import html
from html.parser import HTMLParser
import re
import unicodedata

LINK_READERS = frozenset({'test_operator', 'alen'})
PURCHASE_SELECTOR = 'alen'
_URL = re.compile(
    r'(?:\b[a-z][a-z0-9+.-]*://|\b(?:mailto|tel):)[^\s<>"\']+'
    r'|(?<![\w@])(?:[\w-]+\.)+(?:[^\W\d_]{2,63})(?::\d+)?(?:[/\?#][^\s<>"\']*)?'
    r'|(?<!\w)(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?(?:/[^\s<>"\']*)?'
    r'|(?<!\w)@[a-z0-9_]{5,}', re.I)
_BUY = r'(?:купить|купим|покупать|закупить|закупать|заказать|закажем|заказывать|докупить|дозаказать|докупать|приобрести)'
_CHOICE = re.compile(
    rf'(?:что(?:[- ]то)?|каки[еёхйуюм]+|чего)\b[^?!.\n]{{0,180}}\b{_BUY}\b'
    rf'|(?:нужно|надо|стоит|будем|хотите)\b[^?!.\n]{{0,120}}\b{_BUY}\b'
    r'|(?:выберите|подберите|предложите|определите|составьте)\b[^?!.\n]{0,120}(?:ассортимент|нов\w*\s+(?:закуп|товар)|список\s+(?:покупок|закупок|товаров))', re.I)


def selection_request(text):
    # Statements/instructions about already agreed purchases remain usable.
    for clause in re.split(r'(?<=[?!.])\s+|\n+', text):
        request = '?' in clause or re.search(
            r'\b(?:ли|подскажите|уточните|скажите|выберите|подберите|предложите|определите|составьте)\b', clause, re.I)
        if request and (_CHOICE.search(clause) or re.search(
                r'каки[ех]\s+(?:ещ[её]\s+)?(?:товар\w*|позици\w*)[^?!.]{0,60}(?:не хватает|добавить|нужны)', clause, re.I)):
            return True
    return False


def active(config):
    return 'team_intake' in config


def context_policy(config, recipient):
    if not active(config):
        return None
    return {
        'links_allowed': recipient in LINK_READERS,
        'purchase_selection_questions_allowed': recipient == PURCHASE_SELECTOR,
        'purchase_selection_owner': PURCHASE_SELECTOR,
        'procurement_execution_only': recipient == 'nastya',
        'authority': 'direct_owner_instruction_20261028',
    }


class _PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in ('br', 'p', 'div', 'li'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('p', 'div', 'li'):
            self.parts.append('\n')


def _plain(text, parse_mode):
    text = html.unescape(text)
    if parse_mode == 'HTML' or re.search(r'<a\b', text, re.I):
        parser = _PlainHTML()
        parser.feed(text)
        text = ''.join(parser.parts)
    return text


def outgoing_text(config, recipient, text, parse_mode=None):
    if not active(config):
        return text
    plain = _plain(text, parse_mode)
    normalized = unicodedata.normalize('NFKC', plain).replace('\u200b', '').replace('\u2060', '')
    # A catalog answer or incoming question cannot decide what procurement buys next.
    if recipient != PURCHASE_SELECTOR and selection_request(normalized):
        reply = 'Список новых закупок определяет Алэн.'
        if recipient == 'nastya':
            reply += ' С вами уточняю только выполнение уже согласованных закупок: оплату, сроки и отправку.'
        return reply
    if recipient in LINK_READERS:
        return text
    # Preserve evidence/ACL receipts internally. Do not send the source footer to staff.
    clean = re.split(r'\n\s*Источники:\s*\n', normalized, maxsplit=1, flags=re.I)[0]
    clean = re.sub(r'!?\[([^\]\n]*)\]\([^\n]*?\)', r'\1', clean)
    clean = _URL.sub('', clean)
    clean = '\n'.join(line.rstrip(' \t—–-:') for line in clean.splitlines()).strip()
    if not clean:
        clean = 'Информацию по этому вопросу можно уточнить у Майка или Алэна.'
    if (clean == normalized and not re.search(r'<a\b', html.unescape(text), re.I)
            and not _URL.search(html.unescape(text))):
        return text
    return html.escape(clean) if parse_mode == 'HTML' else clean


def apply_result(config, recipient, result):
    """Keep saved assistant dialogue consistent with the message being queued."""
    if not active(config) or not isinstance(result.get('reply_text'), str):
        return result
    original = result['reply_text']
    cleaned = outgoing_text(config, recipient, original)
    if cleaned == original:
        return result
    result = dict(result, reply_text=cleaned)
    state = result.get('dialogue_state')
    if isinstance(state, dict):
        result['dialogue_state'] = dict(state, pending_offer=None)
    return result
