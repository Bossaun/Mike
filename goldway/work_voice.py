"""Warmth belongs inside an already authorized useful private follow-up."""


def daily_opening(text, local_now, sent_today):
    if sent_today or not 9 <= local_now.hour < 18 or not text.strip():
        return text
    openings = ('Хорошего дня! ', 'Пусть день пройдёт спокойно. ', 'Добрый день! ')
    opening = openings[local_now.date().toordinal() % len(openings)]
    return opening + text if len(opening + text) <= 450 else text
