"""Prospective ordinal explanation from explicit numbering in delivered text.

No state.entities, general history, retrieval, classification, or task authority.
The selected block is conversation data, not a verified corporate fact.
"""
from dataclasses import dataclass
import re
from .dialogue_focus import _request, _EXPLAIN, _ORDINAL_INDEX, _ORDINAL, _ENTITY_NOUN
from .answer_rewrite import _deny, _handle_readonly_answer

_BARE=re.compile(r'(?P<ordinal>'+_ORDINAL+r')\s+'+_ENTITY_NOUN+r'(?:\s+(?:подробнее|поподробнее))?')
_NUMBERED=re.compile(r'(?P<number>[1-9][0-9]?)(?P<mark>[.)])\s+(?P<heading>\S.*)')
_NUMERIC_START=re.compile(r'^\s*(?:[0-9]+\s*[.)\-:]|[-*•])')
_NOTICE=('Не могу однозначно выбрать этот пункт в последнем доставленном ответе. '
         'Укажите название или пришлите список с явной нумерацией 1–12.')
_FOOTERS=('\n\nИсточники:\n','\n\nИсточники исходного ответа:\n')


def ordinal_index(text):
    request=_request(text)
    match=(_EXPLAIN.fullmatch(request) or _BARE.fullmatch(request)) if request else None
    if match is None:return None
    ordinal=match.group('ordinal')
    index=_ORDINAL_INDEX.get(ordinal)
    return index+1 if index is not None else int(re.match(r'\d+',ordinal).group())


def is_ordinal_request(text):return ordinal_index(text) is not None


@dataclass(frozen=True)
class SelectedItem:
    number:int
    name:str
    text:str
    count:int


def select_numbered_item(answer,number):
    """Accept one complete contiguous flat list with literal names/headings.

    Plain heading lines ``1. Name — details`` / ``1) Name: details``; optional
    shared one-line colon heading and indented continuation. Other list formats,
    nested/duplicate/skipped numbering, paragraphs/outros, ambiguous names and
    more than twelve entries require clarification. No inference from mentions.
    """
    if type(answer) is not str or not 0<len(answer)<=3000 or type(number) is not int or not 1<=number<=12:return None
    if any(ord(c)<32 and c not in '\n\t' for c in answer):return None
    # Footer is presentation text only. It never creates citation authority;
    # numeric list content inside a footer makes this projection ambiguous.
    found=[(answer.find(marker),marker) for marker in _FOOTERS if marker in answer]
    if found:
        if sum(answer.count(marker) for marker in _FOOTERS)!=1:return None
        position,marker=found[0];tail=answer[position+len(marker):]
        if any(_NUMERIC_START.match(line) for line in tail.splitlines()):return None
        answer=answer[:position]
    blocks=[];names=[];style=None;preamble=''
    for line in answer.splitlines():
        if not line.strip():continue
        match=_NUMBERED.fullmatch(line)
        if match:
            if len(blocks)>=12 or int(match['number'])!=len(blocks)+1:return None
            if style is not None and style!=match['mark']:return None
            style=match['mark'];heading=match['heading']
            name=re.split(r'\s+[—–]\s+|:\s+',heading,maxsplit=1)[0]
            if (not 0<len(name)<=128 or name!=name.strip() or
                    re.fullmatch(r"[\w .&'’()+/\-]+",name) is None):return None
            if name.casefold() in {v.casefold() for v in names}:return None
            names.append(name);blocks.append(line)
        elif _NUMERIC_START.match(line):return None
        elif not blocks:
            if preamble or len(line)>256 or not line.endswith(':') or line!=line.strip():return None
            preamble=line
        elif line.startswith('  ') and len(line.strip())<=1000:
            blocks[-1]+='\n'+line
        else:return None
    if not blocks or number>len(blocks):return None
    chosen=(preamble+'\n' if preamble else '')+blocks[number-1]
    return SelectedItem(number,names[number-1],chosen,len(blocks))


def handle_ordinal_followup(engine,event_id,payload):
    number=ordinal_index(payload.get('text'))
    if number is None:_deny()
    def prepare(target):
        selected=select_numbered_item(target['answer'],number)
        if selected is None:return None
        return dict(kind='explain_numbered_item',target_answer=selected.text,
                    selected_entity=selected.name,selected_number=selected.number,
                    numbered_count=selected.count,pending_offer=None)
    return _handle_readonly_answer(engine,event_id,payload,prepare,ordinal=True,
                                   notice=_NOTICE,prefix='ordinal-followup')
