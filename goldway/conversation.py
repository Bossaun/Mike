"""Small deterministic answers; general model replies are a separate field."""
import re

GENERAL_PATTERN = r'общаться|что (?:ты )?умеешь|кто ты|как тебя зовут|как дела'


def basic_reply(text):
    question=' '.join(text.casefold().split()).strip(' ?!.')
    if question in ('а с тобой можно общаться','с тобой можно общаться','можно общаться','можно'):
        return 'Да, можно общаться со мной обычным текстом. Задайте вопрос, пришлите планёрку или расскажите о результате работы. Номер задачи нужен только для уточнения конкретной задачи.'
    if question in ('что ты умеешь','что умеешь','кто ты','как тебя зовут') or re.fullmatch(r'как (?:тобой|вами|ботом|этим ботом|такой) пользоваться',question):
        return 'Я помощник GOLDWAY. Пришлите текст планёрки или файл .txt — выделю задачи и покажу черновики. По задаче можно написать «Задача №3: жду поставщика» или сообщить конкретный результат. Для списка задач напишите «сводка». Обычные вопросы тоже можно задавать без команд.'
    return None


def is_general(text):
    return basic_reply(text) is not None or bool(re.search(GENERAL_PATTERN,text.casefold()))
