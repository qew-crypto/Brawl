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
    now=now.astimezone(MSK)
    active,future=windows(rows,now)
    lines=['🎮 <b>ОЧЕРЕДЬ НА АККАУНТ</b>',
           '<i>Московское время · ежедневное расписание</i>',
           '━━━━━━━━━━━━━━━━━━━━']
    if active:
        if len(active)==1:
            item=active[0]
            lines += ['🟢 <b>СЕЙЧАС ПО РАСПИСАНИЮ</b>',
                      f'Сейчас очередь {member_label(item["row"])}',
                      f'🕹 {item["start"]:%H:%M} — <b>{item["end"]:%H:%M} МСК</b>',
                      '', 'Пожалуйста, не заходите одновременно.',
                      'Смена игрока — в конце назначенного времени.']
        else:
            lines += ['⚠️ <b>Пересечение времени в расписании</b>',
                      'Нужно согласовать очередь с владельцем:']
            for item in active[:8]:
                lines.append(f'› {member_label(item["row"])} — до <b>{item["end"]:%H:%M}</b>')
            if len(active)>8:
                lines.append(f'И ещё {len(active)-8} участников.')
    else:
        lines += ['⚪ <b>Сейчас на аккаунте нет очереди</b>',
                  '', 'Хотите поиграть? Напишите в этот чат:',
                  '<b>«Я буду играть на аккаунте до 22:00»</b>',
                  'Укажите своё время, чтобы другие не зашли одновременно.',
                  'Договорённость остаётся в чате. Бот не учитывает эти сообщения.']
    lines += ['━━━━━━━━━━━━━━━━━━━━','⏭ <b>СЛЕДУЮЩИЕ ИГРОКИ</b>']
    if future:
        for i,item in enumerate(future[:3],1):
            day='сегодня' if item['start'].date()==now.date() else 'завтра' if item['start'].date()==now.date()+timedelta(days=1) else item['start'].strftime('%d.%m')
            lines.append(f'{i}. {member_label(item["row"])} · {day}, <b>{item["start"]:%H:%M}–{item["end"]:%H:%M}</b>')
    else:
        lines.append('Назначенных слотов пока нет.')
    lines += ['━━━━━━━━━━━━━━━━━━━━',
              'ℹ️ Очередь — только по ежедневному расписанию.',
              '<i>Бот не видит фактический вход в Brawl Stars.</i>']
    return '\n'.join(lines)
