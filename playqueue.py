"""Daily Moscow schedule and schedule-only status; chat declarations are not tracked; no game session tracking."""
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from helpers import member_label

MSK = ZoneInfo('Europe/Moscow')
def windows(rows, now):
    """A schedule repeats daily; start inclusive, end exclusive, overnight slots supported."""
    now = now.astimezone(MSK)
    active, future = [], []
    for row in rows:
        try:
            start_s, end_s = row['schedule'].replace('–','-').replace('—','-').split('-')
            start_t, end_t = time.fromisoformat(start_s), time.fromisoformat(end_s)
        except (ValueError, KeyError):
            continue
        for offset in (-1,0,1):
            day = now.date() + timedelta(days=offset)
            start = datetime.combine(day,start_t,MSK)
            end = datetime.combine(day,end_t,MSK)
            if end <= start:
                end += timedelta(days=1)
            item = {'row':row,'start':start,'end':end}
            if start <= now < end:
                active.append(item)
            elif start > now:
                future.append(item)
    future.sort(key=lambda x:(x['start'],x['row']['user_id']))
    active.sort(key=lambda x:(x['start'],x['row']['user_id']))
    return active,future


def queue_text(rows, now):
    active, future = windows(rows,now)
    lines = ['🎮 ОЧЕРЕДЬ НА АККАУНТ · МСК']
    if active:
        if len(active) == 1:
            item = active[0]
            lines.append(f'🟢 Сейчас очередь {member_label(item["row"])} — до {item["end"]:%H:%M}.')
        else:
            lines.append('⚠️ Пересечение времени в расписании. Согласуйте очередь с владельцем:')
            for item in active[:8]:
                lines.append(f'• {member_label(item["row"])} — до {item["end"]:%H:%M}')
            if len(active)>8:
                lines.append(f'И ещё {len(active)-8} участников. Владелец должен исправить расписание.')
    else:
        lines += ['⚪ Сейчас на аккаунте нет очереди.',
                  'Если хотите играть, напишите в этот чат:',
                  '«Я буду играть на аккаунте до 22:00»',
                  'Договоритесь в чате, чтобы другие не заходили одновременно. Бот не учитывает эти сообщения.']
    if future:
        lines.append('⏭ Далее по расписанию:')
        for item in future[:3]:
            when=item['start'].strftime('%d.%m %H:%M')
            lines.append(f'• {member_label(item["row"])} — {when}–{item["end"]:%H:%M}')
    lines += ['ℹ️ Очередь — только по ежедневному расписанию.',
              'Бот не видит фактический вход в Brawl Stars.']
    return '\n'.join(lines)
