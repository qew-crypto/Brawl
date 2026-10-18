import re
from html import escape

FIELDS = [
    ('nick', '🎮 Твой ник в Brawl Stars?'),
    ('age', '🎂 Сколько тебе полных лет?'),
    ('trophies', '🏆 Сколько у тебя кубков? Введи число.'),
    ('rank', '⚔️ Какой у тебя текущий ранг в Ranked?'),
    ('hours', '⏱ Сколько часов в день готов играть? Например: 2 или 1.5.'),
    ('preferred_time', '🕘 В какое время тебе удобно играть ПО МОСКВЕ? Например: 21:00–22:00. Владелец согласует окончательное время отдельно.'),
    ('responsible', '🤝 Готов ответственно относиться к команде, соблюдать расписание и предупреждать об отсутствии?'),
    ('occupation', '📚 Учишься или работаешь?'),
    ('direction', '🎯 Выбери своё направление в команде: Ладдер или Ranked.'),
    ('comment', '💬 Комментарий — по желанию. Напиши текст или нажми «Пропустить».'),
]
RESPONSES = ('Да, готов', 'Нет')
OCCUPATIONS = ('Да, учусь', 'Да, работаю', 'Учусь и работаю', 'Нет')
DIRECTIONS = {'🏆 Ладдер': 'ladder', '⚔️ Ranked': 'ranked'}


def normalize_tag(value):
    value = value.strip().upper().lstrip('#')
    if not re.fullmatch('[0289PYLQGRJCUV]{3,15}', value):
        raise ValueError('Неверный тег. Пример: #2QGJPJLJQG; буква O не используется, вместо неё цифра 0.')
    return '#' + value


def validate(field, value):
    value = value.strip()
    limits = {'nick': 80, 'rank': 80, 'comment': 600, 'preferred_time': 160}
    if not value or len(value) > limits.get(field, 80):
        raise ValueError(f'Ответ должен содержать от 1 до {limits.get(field, 80)} символов.')
    if field == 'age':
        if not value.isdigit() or not 1 <= int(value) <= 100:
            raise ValueError('Введи возраст числом от 1 до 100.')
    elif field == 'trophies':
        value = value.replace(' ', '')
        if not value.isdigit() or not 0 <= int(value) <= 1000000:
            raise ValueError('Введи кубки целым числом от 0 до 1000000.')
    elif field == 'hours':
        value = value.replace(',', '.')
        try:
            number = float(value)
        except ValueError:
            raise ValueError('Введи часы числом, например 2 или 1.5.')
        if not 0 < number <= 24:
            raise ValueError('Число должно быть больше 0 и не больше 24.')
    elif field == 'schedule':
        value = value.replace('—', '-').replace('–', '-').replace(' ', '')
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d-(?:[01]\d|2[0-3]):[0-5]\d', value):
            raise ValueError('Нужен формат 21:00-22:00, по Москве. Допустим переход через полночь.')
        if value[:5] == value[-5:]:
            raise ValueError('Начало и конец не должны совпадать.')
        value = value.replace('-', '–')
    elif field == 'responsible' and value not in RESPONSES:
        raise ValueError('Выбери ответ кнопкой.')
    elif field == 'direction' and value not in DIRECTIONS:
        raise ValueError('Выбери направление кнопкой: Ладдер или Ranked.')
    elif field == 'occupation' and value not in OCCUPATIONS:
        raise ValueError('Выбери ответ кнопкой.')
    return value


def member_label(row):
    if row.get('username'):
        return '@' + escape(row['username'])
    return f'<a href="tg://user?id={int(row["user_id"])}">{escape(row["name"][:60])}</a>'


def pretty_number(value):
    if isinstance(value,int) and not isinstance(value,bool):
        return f'{value:,}'.replace(',',' ')
    return escape(str(value)) if value not in (None,'') else '—'


def panel_text(rows, owner_username, start, stats, tag):
    joined=sum(r['status']=='joined' for r in rows)
    waiting=sum(r['status']=='approved' for r in rows)
    name=escape(str(stats.get('name') or 'Аккаунт проекта'))
    lines = ['🎮 <b>10 ПАССАЖИРОВ</b>',
             '<i>Состав · расписание · результаты</i>',
             '━━━━━━━━━━━━━━━━━━━━',
             f'👤 <b>{name}</b> · <code>{escape(tag)}</code>',
             '', '📊 <b>СТАТИСТИКА АККАУНТА</b>',
             f'🏆 Кубки: <b>{pretty_number(stats.get("trophies"))}</b>  ┃  Рекорд: <b>{pretty_number(stats.get("highest"))}</b>',
             f'⚔️ Ranked: <b>{pretty_number(stats.get("elo", "—"))} Elo</b>  ┃  Рекорд: <b>{pretty_number(stats.get("best_elo", "—"))}</b>']
    if stats.get('elo','—')!='—' or stats.get('best_elo','—')!='—':
        lines.append('↳ Elo: введено владельцем вручную, не через API.')
    else:
        lines.append('↳ Elo: нет подключённого подтверждённого API; доступен ручной ввод.')
    if any(key in stats for key in ('wins_3v3','wins_solo','wins_duo')):
        lines += ['🎯 <b>Победы</b>',
                  f'3×3: <b>{pretty_number(stats.get("wins_3v3"))}</b>  ·  Solo: <b>{pretty_number(stats.get("wins_solo"))}</b>  ·  Duo: <b>{pretty_number(stats.get("wins_duo"))}</b>']
    if stats.get('club'):
        lines.append(f'🛡 Клуб: {escape(str(stats["club"]))}')
    lines += ['', f'🔄 {escape(str(stats.get("updated", "Ещё не обновлялось")))} · каждые 5 минут',
              f'📡 {escape(str(stats.get("state", "Ожидаем подключение API")))}',
              '━━━━━━━━━━━━━━━━━━━━', '👥 <b>КОМАНДА ПРОЕКТА</b>',
              f'В группе: <b>{joined}</b>  ·  Ожидают входа: <b>{waiting}</b>',
              f'👑 Владелец — @{escape(owner_username.lstrip("@"))}']
    for key,title in [('ladder','🏆 Ладдер'),('ranked','⚔️ Ranked'),('helper','🛠 Помощники')]:
        people=[member_label(r) for r in rows if r['role']==key]
        lines.append(f'<b>{title}</b>' + (' — Набор открыт' if not people else ''))
        for i in range(0,len(people),4):
            lines.append('› '+', '.join(people[i:i+4]))
    lines += ['━━━━━━━━━━━━━━━━━━━━', '🕘 <b>РАСПИСАНИЕ · МОСКВА</b>']
    if not rows:
        lines.append('Пока свободно. Набор в команду открыт ✨')
    for i,row in enumerate(rows,1):
        wait=' ⏳' if row['status']=='approved' else ''
        role='🏆' if row['role']=='ladder' else '⚔️' if row['role']=='ranked' else '👤'
        lines.append(f'{i}. {role} {member_label(row)} · <b>{escape(row["schedule"])}</b>{wait}')
    lines += ['', '⏳ — одобрен, ещё не вступил в группу.',
              '━━━━━━━━━━━━━━━━━━━━', '🎯 <b>Цель:</b> хороший результат, не получить бан.',
              f'📆 Старт: {escape(start)} · ранее 09.09.2026',
              '<i>Соблюдаем расписание и договариваемся в чате. Вместе — сильнее.</i>']
    return '\n'.join(lines)


def panel_parts(rows, owner_username, start, stats, tag, size=3000):
    text = panel_text(rows, owner_username, start, stats, tag)
    chunks = []
    current = ''
    for line in text.splitlines():
        if current and len(current)+len(line)+1 > size:
            chunks.append(current)
            current = '🎮 <b>10 ПАССАЖИРОВ · ПРОДОЛЖЕНИЕ</b>'
        current += ('\n' if current else '') + line
    if current:
        chunks.append(current)
    return chunks
