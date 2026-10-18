"""Telegram recruitment bot. Run one polling instance per database."""
import asyncio
import contextlib
import json
import logging
import os
import secrets
import hashlib
from pathlib import Path
import shutil
from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import quote
from zoneinfo import ZoneInfo

import aiohttp
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import (Message, CallbackQuery, ChatJoinRequest, ChatMemberUpdated,
                           InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup,
                           KeyboardButton, ReplyKeyboardRemove, KeyboardButtonRequestChat,
                           ChatAdministratorRights)
from helpers import FIELDS, RESPONSES, OCCUPATIONS, DIRECTIONS, validate, normalize_tag, member_label, panel_parts
from storage import Store
from playqueue import queue_text

BASE = Path(__file__).resolve().parent
ENV_FILE = BASE / '.env'
if not ENV_FILE.exists() and not os.getenv('BOT_TOKEN'):
    shutil.copyfile(BASE / '.env.example', ENV_FILE)
    ENV_FILE.chmod(0o600)
    print(f'Создан {ENV_FILE}. Заполните BOT_TOKEN и OWNER_ID, затем повторите python bot.py.')
load_dotenv(ENV_FILE)
TOKEN = os.getenv('BOT_TOKEN', '').strip()
try:
    OWNER = int(os.getenv('OWNER_ID', ''))
except ValueError:
    raise SystemExit('Заполни числовой Telegram OWNER_ID в .env')
if not TOKEN or OWNER <= 0:
    raise SystemExit('Нужны BOT_TOKEN и положительный Telegram OWNER_ID в .env')
OWNER_NAME = os.getenv('OWNER_USERNAME', 'hawkuy').lstrip('@')
START = os.getenv('PROJECT_START', '18.10.2026')
API_TOKEN = os.getenv('BRAWL_API_TOKEN', '').strip()
db_path = Path(os.getenv('DB_PATH', 'data/team.sqlite3'))
store = Store(str(db_path if db_path.is_absolute() else BASE / db_path))
if not store.setting('tag'):
    store.set('tag', normalize_tag(os.getenv('PLAYER_TAG', '#2QGJPJLJQG')))
if os.getenv('GROUP_ID') and not store.setting('group'):
    store.set('group', int(os.environ['GROUP_ID']))
bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
lock = asyncio.Lock()
panel_lock = asyncio.Lock()
queue_lock = asyncio.Lock()
campaign_lock = asyncio.Lock()
api_lock = asyncio.Lock()
MSK = ZoneInfo('Europe/Moscow')
ADMIN_HELP = ('/admin — панель\n/panel — создать пост (в группе)\n/cancel — отменить текущий ввод\n'
              '/applications — заявки\n/members — состав\n/review ID — анкета\n'
              '/refresh — обновить кубки и пост\n'
              'Все остальные действия доступны кнопками в /admin.')
USER_HELP = ('/start — анкета\n/status — статус\n/invite — новая ссылка после одобрения\n'
             '/leave — выйти с баном в группе\n/stop — отключить рассылку\n'
             '/subscribe — включить рассылку\n/delete — удалить анкету\n/cancel — отменить ввод')


def kb(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=data) for text,data in row] for row in rows])


def reply_keys(items):
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=x)] for x in items], resize_keyboard=True)


def group_id():
    return int(store.setting('group', '0'))


def stats():
    try:
        result = json.loads(store.setting('stats', '{}'))
    except json.JSONDecodeError:
        result = {}
    result['elo'] = store.setting('elo', '—')
    result['best_elo'] = store.setting('best_elo', '—')
    return result


def text_panels():
    return panel_parts(store.members(), OWNER_NAME, START, stats(), store.setting('tag'))


async def tell_owner(text, **kwargs):
    try:
        await bot.send_message(OWNER, text, **kwargs)
    except TelegramAPIError:
        logging.exception('Owner notification failed')


async def dm(uid, text, **kwargs):
    try:
        await bot.send_message(uid, text, **kwargs)
        return True
    except TelegramForbiddenError:
        store.run('UPDATE users SET reachable=0 WHERE user_id=?', (uid,))
        return False
    except TelegramAPIError:
        logging.exception('DM failed for %s', uid)
        return False


async def tg_call(fn, *args, **kwargs):
    while True:
        try:
            return await fn(*args, **kwargs)
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after+1)


async def sync_panel(create=False):
    async with panel_lock:
        gid = group_id()
        if not gid:
            return False
        ids = json.loads(store.setting('panel_ids', '[]'))
        legacy = store.setting('panel_id')
        if not ids and legacy:
            ids = [int(legacy)]
        if not ids and not create:
            return False
        hashes = json.loads(store.setting('panel_hashes', '[]'))
        parts = text_panels()
        new_hashes = []
        try:
            for index, text in enumerate(parts):
                digest = hashlib.sha256(text.encode()).hexdigest()
                new_hashes.append(digest)
                if index < len(ids):
                    if not create and index < len(hashes) and hashes[index] == digest:
                        continue
                    try:
                        await tg_call(bot.edit_message_text,text,chat_id=gid,message_id=ids[index])
                    except TelegramBadRequest as exc:
                        reason = str(exc).lower()
                        if 'message is not modified' in reason:
                            pass
                        elif 'message to edit not found' in reason:
                            msg = await tg_call(bot.send_message,gid,text)
                            ids[index] = msg.message_id
                        else:
                            raise
                else:
                    msg = await tg_call(bot.send_message,gid,text)
                    ids.append(msg.message_id)
                store.set('panel_ids',json.dumps(ids))
            extras = ids[len(parts):]
            for mid in extras:
                try:
                    await tg_call(bot.delete_message,gid,mid)
                except TelegramBadRequest:
                    # Telegram may refuse deletion of old messages: make stale continuation harmless.
                    with contextlib.suppress(TelegramBadRequest):
                        await tg_call(bot.edit_message_text,'Состав обновлён. Эта часть больше не используется.',chat_id=gid,message_id=mid)
            store.set('panel_ids',json.dumps(ids[:len(parts)]))
            store.set('panel_hashes',json.dumps(new_hashes))
            store.set('panel_id','')
            return True
        except TelegramAPIError:
            logging.exception('Panel update failed; retry next cycle')
            return False


async def fetch_stats():
    """Only documented trophy fields are consumed. Never treat Power Play as Ranked Elo."""
    async with api_lock:
        value = stats()
        tag = store.setting('tag')
        if not API_TOKEN:
            value['state'] = 'Ключ Brawl Stars API не настроен; Elo вводится вручную.'
        else:
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                    async with session.get('https://api.brawlstars.com/v1/players/' + quote(tag, safe=''),
                                           headers={'Authorization': 'Bearer ' + API_TOKEN}) as resp:
                        if resp.status != 200:
                            errors = {403: 'Нет доступа API: проверь ключ и внешний IPv4 VPS.',
                                      404: 'Аккаунт не найден: проверь тег.', 429: 'Лимит API: повтор через 5 минут.',
                                      503: 'Технические работы Brawl Stars.'}
                            raise ValueError(errors.get(resp.status, f'Ошибка API HTTP {resp.status}.'))
                        data = await resp.json()
                        trophies = data.get('trophies')
                        highest = data.get('highestTrophies')
                        if not isinstance(trophies, int) or isinstance(trophies, bool):
                            raise ValueError('API не вернул корректное число кубков.')
                        value['trophies'] = trophies
                        value['highest'] = highest if isinstance(highest, int) else '—'
                        value['updated'] = datetime.now(MSK).strftime('%d.%m.%Y %H:%M МСК')
                        value['state'] = 'Кубки: официальный API · опрос каждые 5 минут'
            except (aiohttp.ClientError, asyncio.TimeoutError):
                value['state'] = 'API недоступен; сохранены последние успешные данные.'
            except (ValueError, TypeError) as exc:
                value['state'] = str(exc)[:200] + ' Последние данные сохранены.'
        # Do not overwrite another account's data if owner changed tag while request was running.
        if store.setting('tag') == tag:
            value.pop('elo', None)
            value.pop('best_elo', None)
            store.set('stats', json.dumps(value, ensure_ascii=False))
    await sync_panel()


async def stats_worker():
    while True:
        try:
            await fetch_stats()
        except Exception:
            logging.exception('Statistics cycle failed')
        await asyncio.sleep(300)


async def check_group(gid):
    chat = await bot.get_chat(gid)
    if chat.type != 'supergroup':
        raise ValueError('Нужна приватная супергруппа Telegram, не канал и не обычная группа.')
    if chat.username:
        raise ValueError('Используй приватную группу без публичного @username — иначе приглашение можно обойти.')
    mine = await bot.get_chat_member(gid, bot.id)
    yours = await bot.get_chat_member(gid, OWNER)
    if yours.status not in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR):
        raise ValueError('Владелец бота должен быть администратором выбранной группы.')
    if mine.status != ChatMemberStatus.ADMINISTRATOR or not mine.can_invite_users or not mine.can_restrict_members:
        raise ValueError('Выдай боту права администратора: приглашать и блокировать участников.')
    return chat


async def connect_group(m, gid):
    if group_id() != gid and store.members():
        raise ValueError('Нельзя менять группу при непустом составе. Сначала исключи участников через панель.')
    chat = await check_group(gid)
    if group_id() != gid:
        store.set('panel_id', '')
        store.set('panel_ids','[]')
        store.set('panel_hashes','[]')
        store.set('queue_id','')
        store.set('queue_hash','')
    store.set('group', gid)
    store.set('queue_enabled','1')
    store.clear_flow(OWNER)
    await m.answer(f'✅ Подключена группа «{escape(chat.title or "Группа")}».\n'
                   'В группе выполни /panel. Закрепи созданный пост вручную при желании.\n'
                   'Отзови старые общие ссылки: бот управляет только созданными им ссылками.',
                   reply_markup=ReplyKeyboardRemove())


async def admin_menu(m):
    store.clear_flow(OWNER)
    await m.answer('⚙️ <b>Управление командой</b>\n'
                   f'Участников: {len(store.members())}. Лимита нет.\n'
                   'Рассылка отправляется в личные сообщения пользователям бота.', reply_markup=kb(
        [('📨 Заявки', 'apps:0'), ('👥 Состав', 'team')],
        [('🚫 Баны / пользователи', 'users:0')],
        [('📣 Рассылка в боте', 'broadcast')],
        [('⚙️ Настройки', 'settings')]))


async def settings_menu(m):
    await m.answer(f'⚙️ Настройки\nГруппа: <code>{group_id() or "не подключена"}</code>\n'
                   f'Аккаунт: <code>{store.setting("tag")}</code>\n'
                   'Статистика: каждые 5 минут. Часовой пояс: Москва.\n'
                   f'Ключ Brawl API: {"задан" if API_TOKEN else "не задан"}.\n'
                   'Владелец и секретные ключи меняются только в .env на VPS.', reply_markup=kb(
        [('🔗 Подключить группу', 'setgroup')], [('🎮 Тег аккаунта', 'settag')],
        [('⚔️ Ввести Elo и рекорд', 'setelo')], [('🔄 Обновить статистику', 'refresh')],
        [('🎮 Обновить очередь','queue_refresh')],
        [('🏠 Главное меню', 'admin')]))


def review_markup(uid):
    return kb([('✅ Принять · указать время', f'accept:{uid}')],
              [('❌ Отклонить', f'reject:{uid}'), ('🚫 Пользователь', f'user:{uid}')])


async def review(m, uid):
    row = store.app(uid)
    if not row or row['status'] != 'pending':
        return await m.answer('Эта заявка уже обработана или удалена.')
    answers = json.loads(row['answers'])
    labels = {'nick': 'Ник', 'age': 'Возраст', 'trophies': 'Кубки', 'rank': 'Ranked',
              'hours': 'Часов в день', 'preferred_time': 'Удобное время · МСК',
              'responsible': 'Ответственность', 'occupation': 'Учёба / работа', 'direction': 'Направление', 'comment': 'Комментарий'}
    text = f'📨 <b>Заявка</b> · {member_label(row)}\nID: <code>{uid}</code>\n'
    text += '\n'.join(f'<b>{label}:</b> {escape(answers.get(key,"—"))}' for key,label in labels.items())
    await m.answer(text, reply_markup=review_markup(uid))


async def ask(m, step):
    key, question = FIELDS[step]
    keys = ReplyKeyboardRemove()
    if key == 'direction':
        return await m.answer(question, reply_markup=kb([('🏆 Ладдер','direction:ladder'),('⚔️ Ranked','direction:ranked')]))
    if key == 'responsible':
        keys = reply_keys(RESPONSES)
    elif key == 'occupation':
        keys = reply_keys(OCCUPATIONS)
    elif key == 'comment':
        keys = reply_keys(['Пропустить'])
    await m.answer(question + '\n\nОтмена: /cancel', reply_markup=keys)


async def new_invite(uid):
    gid = group_id()
    if not gid:
        raise ValueError('Сначала подключи группу через настройки.')
    old = store.app(uid)['invite']
    result = await bot.create_chat_invite_link(gid, name=f'Заявка {uid}', creates_join_request=True,
                                               expire_date=datetime.now(timezone.utc)+timedelta(hours=24))
    store.edit_app(uid, invite=result.invite_link)
    if old:
        with contextlib.suppress(TelegramAPIError):
            await bot.revoke_chat_invite_link(gid, old)
    return result.invite_link


async def revoke(row):
    if row and row['invite'] and group_id():
        with contextlib.suppress(TelegramAPIError):
            await bot.revoke_chat_invite_link(group_id(), row['invite'])


async def issue_invite(uid, link):
    return await dm(uid, '✅ Заявка одобрена!\n'
                    f'Твоё время по Москве: <b>{escape(store.app(uid)["schedule"])}</b>.\n'
                    'Место зарезервировано. Открой ссылку и отправь запрос на вступление — бот примет только тебя.\n'
                    'Срок ссылки — 24 часа. Новая ссылка: /invite.\n' + escape(link))


async def remove_from_team(uid):
    """Fail closed: do not free a slot until Telegram confirms the group ban."""
    if uid == OWNER:
        raise ValueError('Нельзя заблокировать владельца бота.')
    if not group_id():
        raise ValueError('Группа не подключена.')
    await bot.ban_chat_member(group_id(), uid, revoke_messages=False)
    store.run('UPDATE users SET chat_banned=1 WHERE user_id=?', (uid,))
    row = store.app(uid)
    if row:
        store.edit_app(uid, status='removed')
        await revoke(row)
    await sync_panel()
    await sync_queue()


async def user_card(m, uid):
    user = store.user(uid)
    if not user:
        raise ValueError('Пользователь ещё не обращался к боту.')
    app = store.app(uid)
    await m.answer(f'{member_label(user)} · <code>{uid}</code>\n'
                   f'Статус: {escape(app["status"]) if app else "без заявки"}\n'
                   f'Бан в боте: {"да" if user["bot_banned"] else "нет"}\n'
                   f'Бан в группе: {"да" if user["chat_banned"] else "нет"}', reply_markup=kb(
        [('🚫 Бан в боте', f'banask:bot:{uid}'), ('✅ Разбан бота', f'unban:bot:{uid}')],
        [('⛔ Бан в группе', f'banask:chat:{uid}'), ('✅ Разбан группы', f'unban:chat:{uid}')],
        [('🕘 Изменить время', f'time:{uid}')],
        [('🏆 Ладдер', f'role:{uid}:ladder'), ('⚔️ Ranked', f'role:{uid}:ranked')],
        [('🛠 Помощник', f'role:{uid}:helper'), ('👤 Участник', f'role:{uid}:member')],
        [('🏠 Панель', 'admin')]))


async def list_apps(m, page):
    page = max(0, page)
    total = store.one("SELECT COUNT(*) AS n FROM applications WHERE status='pending'")['n']
    rows = store.rows("SELECT a.user_id,u.name FROM applications a JOIN users u USING(user_id) WHERE status='pending' ORDER BY created_at,a.user_id LIMIT 8 OFFSET ?", (page*8,))
    buttons = [[(f'📨 {r["name"][:35]} · {r["user_id"]}', f'review:{r["user_id"]}')] for r in rows]
    nav = []
    if page:
        nav.append(('⬅️',f'apps:{page-1}'))
    if total > (page+1)*8:
        nav.append(('➡️',f'apps:{page+1}'))
    if nav:
        buttons.append(nav)
    buttons.append([('🏠 Панель','admin')])
    await m.answer(f'Заявок на рассмотрении: {total}.', reply_markup=kb(*buttons))


async def begin_broadcast(m):
    store.set_flow(OWNER, 'broadcast')
    await m.answer('📣 Отправь ОДНО сообщение для рассылки: текст, фото, видео, файл и подпись. '
                   'Альбомы и служебные сообщения не поддерживаются.\n'
                   'Бот покажет предпросмотр и число получателей. Без подтверждения ничего не отправится.\n'
                   'Получатели — пользователи, начавшие диалог с ботом, без бана и с включённой подпиской.\n/cancel — отмена.')


async def prepare_broadcast(m):
    if m.media_group_id or not (m.text or m.photo or m.video or m.document or m.audio or m.voice or m.animation or m.sticker):
        raise ValueError('Отправь одно сообщение без альбома: текст, фото, видео или файл.')
    await m.answer('Предпросмотр рассылки:')
    mode = store.setting('broadcast_mode', 'telegram')
    # Store the preview's message ID, not the mutable source; all recipients get the confirmed snapshot.
    if mode == 'markdown' and m.text:
        preview = await bot.send_message(OWNER, m.text, parse_mode='MarkdownV2')
    elif mode == 'markdown' and m.caption:
        preview = await bot.copy_message(OWNER, OWNER, m.message_id, caption=m.caption, parse_mode='MarkdownV2')
    else:
        preview = await bot.copy_message(OWNER, OWNER, m.message_id)
    result = store.run('INSERT INTO campaigns(source_chat,source_message) VALUES (?,?)', (OWNER,preview.message_id))
    cid = result.lastrowid
    count = store.one('SELECT COUNT(*) AS n FROM users WHERE subscribed=1 AND bot_banned=0 AND reachable=1 AND user_id!=?', (OWNER,))['n']
    store.set_flow(OWNER, 'broadcast_confirm', campaign=cid)
    await m.answer(f'Рассылка №{cid}: сейчас доступных получателей — {count}.\nОтправить в личные сообщения бота?',
                   reply_markup=kb([('📤 Отправить',f'bcsend:{cid}'),('Отмена',f'bccancel:{cid}')]))


async def broadcast_worker():
    async with campaign_lock:
        while True:
            campaign = store.one("SELECT * FROM campaigns WHERE status='running' ORDER BY id LIMIT 1")
            if not campaign:
                return
            cid = campaign['id']
            while True:
                delivery = store.one("SELECT * FROM deliveries WHERE campaign_id=? AND status='pending' ORDER BY user_id LIMIT 1", (cid,))
                if not delivery:
                    break
                uid = delivery['user_id']
                user = store.user(uid)
                if not user or user['bot_banned'] or not user['subscribed'] or not user['reachable']:
                    store.run("UPDATE deliveries SET status='skipped' WHERE campaign_id=? AND user_id=?", (cid,uid))
                    continue
                store.run("UPDATE deliveries SET status='sending' WHERE campaign_id=? AND user_id=?", (cid,uid))
                try:
                    await bot.copy_message(uid, campaign['source_chat'], campaign['source_message'])
                    state, error = 'sent', None
                except TelegramRetryAfter as exc:
                    store.run("UPDATE deliveries SET status='pending' WHERE campaign_id=? AND user_id=?", (cid,uid))
                    await asyncio.sleep(exc.retry_after+1)
                    continue
                except TelegramForbiddenError:
                    store.run('UPDATE users SET reachable=0 WHERE user_id=?', (uid,))
                    state,error = 'failed','blocked'
                except TelegramBadRequest:
                    state,error = 'failed','bad_request'
                except (TelegramAPIError, asyncio.TimeoutError, aiohttp.ClientError):
                    # Delivery may have reached Telegram. Avoid duplicate notifications on uncertain failures.
                    state,error = 'uncertain','network_or_api'
                store.run('UPDATE deliveries SET status=?,error=? WHERE campaign_id=? AND user_id=?', (state,error,cid,uid))
                await asyncio.sleep(0.08)  # <=12.5 messages/sec, below Telegram's usual free broadcast limit.
            store.run("UPDATE campaigns SET status='done' WHERE id=?", (cid,))
            totals = store.rows('SELECT status,COUNT(*) AS n FROM deliveries WHERE campaign_id=? GROUP BY status',(cid,))
            labels = {'sent':'доставлено','failed':'ошибок','skipped':'пропущено','uncertain':'доставка не подтверждена'}
            await tell_owner(f'📣 Рассылка №{cid} завершена.\n'+'\n'.join(f'{labels.get(r["status"],r["status"])}: {r["n"]}' for r in totals))


background = set()


def launch(coro):
    task = asyncio.create_task(coro)
    background.add(task)
    def done(t):
        background.discard(t)
        if not t.cancelled() and t.exception():
            logging.error('Background job failed', exc_info=(type(t.exception()), t.exception(), t.exception().__traceback__))
    task.add_done_callback(done)


async def handle_owner_input(m, flow):
    kind = flow['kind']
    value = (m.text or '').strip()
    if kind == 'group':
        if m.chat_shared:
            return await connect_group(m, m.chat_shared.chat_id)
        if not value.lstrip('-').isdigit():
            raise ValueError('Выбери группу кнопкой или введи числовой ID вида -1001234567890.')
        return await connect_group(m, int(value))
    if kind == 'tag':
        tag = normalize_tag(value)
        store.set('tag', tag)
        store.set('stats', '{}')
        store.set('elo','—')
        store.set('best_elo','—')
        store.clear_flow(OWNER)
        await m.answer(f'Аккаунт сохранён: <code>{tag}</code>. Статистика прежнего аккаунта сброшена.')
        launch(fetch_stats())
        return
    if kind == 'elo':
        values = value.split()
        if len(values) != 2 or not all(v.isdigit() and 0 <= int(v) <= 1000000 for v in values):
            raise ValueError('Введи два целых числа: текущее Elo и рекорд. Например: 8500 9200.')
        if int(values[1]) < int(values[0]):
            raise ValueError('Рекорд не может быть меньше текущего Elo.')
        store.set('elo', int(values[0]))
        store.set('best_elo', int(values[1]))
        store.clear_flow(OWNER)
        await sync_panel()
        return await m.answer('Ranked сохранён. В посте указано, что Elo введено владельцем вручную.')
    if kind in ('accept_time', 'change_time'):
        uid = flow['payload']['uid']
        row = store.app(uid)
        expected = ('pending',) if kind == 'accept_time' else ('approved','joined')
        if not row or row['status'] not in expected:
            store.clear_flow(OWNER)
            raise ValueError('Заявка уже обработана или участник вышел. Открой /admin заново.')
        time = validate('schedule', value)
        if kind == 'change_time':
            store.edit_app(uid, schedule=time)
            store.clear_flow(OWNER)
            await sync_panel()
            await dm(uid, f'🕘 Владелец изменил твоё время: {time} МСК.')
            return await m.answer('Расписание изменено.')
        nonce = secrets.token_hex(4)
        store.set_flow(OWNER, 'accept_confirm', uid=uid, schedule=time, nonce=nonce)
        return await m.answer(f'Принять {member_label(row)}?\nНазначенное время: <b>{time} МСК</b>.\n'
                              'После подтверждения отправится приглашение.', reply_markup=kb(
            [('✅ Подтвердить приём',f'acceptconfirm:{nonce}')], [('Отмена','cancel')]))
    if kind == 'broadcast':
        return await prepare_broadcast(m)
    if kind in ('broadcast_confirm','accept_confirm'):
        return await m.answer('Нажми кнопку подтверждения в предыдущем сообщении или /cancel.')


async def cancel(m, uid=None):
    uid = uid if uid is not None else m.from_user.id
    flow = store.flow(uid)
    if flow and flow['kind'] == 'broadcast_confirm':
        store.run("UPDATE campaigns SET status='cancelled' WHERE id=? AND status='draft'",(flow['payload']['campaign'],))
    store.clear_flow(uid)
    row = store.app(uid)
    if row and row['status'] == 'draft':
        store.run('DELETE FROM applications WHERE user_id=?', (uid,))
    await m.answer('Текущий ввод отменён. Готовые заявки не изменены.', reply_markup=ReplyKeyboardRemove())


async def handle_private(m):
    uid = m.from_user.id
    store.touch(m.from_user)
    cmd = (m.text or '').split(maxsplit=1)[0].split('@')[0] if m.text else ''
    user = store.user(uid)
    if cmd in ('/stop','/subscribe'):
        store.run('UPDATE users SET subscribed=? WHERE user_id=?', (int(cmd=='/subscribe'),uid))
        return await m.answer('Рассылка отключена.' if cmd == '/stop' else 'Рассылка включена.')
    if cmd == '/cancel':
        return await cancel(m)
    if cmd == '/delete':
        row = store.app(uid)
        if row and row['status'] in ('approved','joined'):
            return await m.answer('Сначала выйди через /leave. Затем /delete удалит анкету из базы.')
        store.run('DELETE FROM applications WHERE user_id=?',(uid,))
        store.clear_flow(uid)
        store.run('UPDATE users SET subscribed=0 WHERE user_id=?',(uid,))
        return await m.answer('Анкета удалена из базы бота, рассылка отключена. '
                              'Копии в чате владельца это не удаляет — попроси владельца удалить их отдельно. '
                              'Минимальная запись Telegram ID и банов остаётся для контроля доступа.', reply_markup=ReplyKeyboardRemove())
    if user['bot_banned'] and uid != OWNER:
        return await m.answer('Доступ к подаче заявок заблокирован владельцем. /stop — отключить рассылку, /delete — удалить анкету.')
    if uid == OWNER:
        if cmd == '/admin':
            return await admin_menu(m)
        if cmd in ('/help','/applications','/members','/review','/refresh'):
            if cmd == '/help':
                return await m.answer(ADMIN_HELP)
            if cmd == '/applications':
                return await list_apps(m,0)
            if cmd == '/members':
                return await show_team(m)
            if cmd == '/review':
                return await review(m, int((m.text or '').split()[1]))
            if cmd == '/refresh':
                launch(fetch_stats())
                return await m.answer('Обновление статистики запрошено.')
        flow = store.flow(OWNER)
        if flow and not cmd.startswith('/'):
            return await handle_owner_input(m,flow)
    if cmd == '/start':
        store.run('UPDATE users SET subscribed=1 WHERE user_id=?',(uid,))
        row = store.app(uid)
        if row and row['status'] == 'draft':
            return await ask(m,row['step'])
        return await m.answer('🎮 Набор в «10 пассажиров»\n'
                              'Анкету и возраст увидит владелец для отбора. В закрытой группе появятся только Telegram-имя, роль и назначенное время. '
                              'Не присылай пароли, коды Supercell ID и платёжные данные.\n'
                              'При отправке анкеты ты передаёшь эти сведения владельцу. '
                              'Бот также может присылать объявления команды; отключение: /stop.\n'
                              'Удаление анкеты: /delete. Помощь: /help.',
                              reply_markup=kb([('📝 Подать заявку','apply')],[('📋 Мой статус','status')],
                                              [('🚪 Выйти из команды','leave')]))
    if cmd == '/help':
        return await m.answer(USER_HELP + ('\n\n'+ADMIN_HELP if uid == OWNER else ''))
    if cmd == '/status':
        return await show_status(m,uid)
    if cmd == '/leave':
        return await leave_prompt(m,uid)
    if cmd == '/invite':
        row = store.app(uid)
        if not row or row['status'] != 'approved' or user['chat_banned']:
            raise ValueError('Новая ссылка доступна только одобренному игроку, ожидающему вступления.')
        return await issue_invite(uid, await new_invite(uid))
    if cmd.startswith('/'):
        return await m.answer('Неизвестная команда. /help')
    row = store.app(uid)
    if not row or row['status'] != 'draft':
        return await m.answer('Начать анкету: /start. Статус: /status.' + (' Панель: /admin.' if uid==OWNER else ''))
    step = row['step']
    key = FIELDS[step][0]
    answers = json.loads(row['answers'])
    value = validate(key,m.text or '')
    answers[key] = '—' if key=='comment' and value=='Пропустить' else value
    next_step = step+1
    store.edit_app(uid, step=next_step, answers=json.dumps(answers,ensure_ascii=False))
    if next_step < len(FIELDS):
        return await ask(m,next_step)
    store.edit_app(uid,status='pending',role=DIRECTIONS.get(answers.get('direction'),'member'))
    await m.answer('📨 Заявка сохранена. Дождись решения владельца. Статус: /status.',reply_markup=ReplyKeyboardRemove())
    try:
        # Use an owner-targeted message rather than replying into the candidate's chat.
        await tell_owner(f'📨 Новая заявка от {member_label(store.app(uid))}.',reply_markup=kb([('Открыть анкету',f'review:{uid}')]))
    except TelegramAPIError:
        logging.exception('Application notice failed; recover from admin applications list')
        await m.answer('Уведомление владельцу не доставлено, но заявка сохранена в его админ-панели.')


async def show_team(m, page=0):
    all_rows = store.members()
    page = max(0,page)
    rows = all_rows[page*8:(page+1)*8]
    keys = [[(f'{r["name"][:30]} · {r["schedule"]}',f'user:{r["user_id"]}')] for r in rows]
    nav = []
    if page:
        nav.append(('⬅️',f'team:{page-1}'))
    if len(all_rows) > (page+1)*8:
        nav.append(('➡️',f'team:{page+1}'))
    if nav:
        keys.append(nav)
    keys.append([('🏠 Панель','admin')])
    await m.answer(f'Состав: {len(all_rows)} участников. Лимита нет. Нажми участника для управления.',reply_markup=kb(*keys))


async def show_status(m,uid):
    row = store.app(uid)
    labels = {'draft':'Анкета не закончена — /start','pending':'На рассмотрении владельца',
              'approved':'Одобрена, ожидается вступление — /invite','joined':'В команде',
              'rejected':'Отклонена владельцем','removed':'Не в составе'}
    text = labels.get(row['status'],'Нет заявки') if row else 'Заявки нет. /start'
    if row and row['status'] in ('approved','joined'):
        text += '\nНазначенное время: '+escape(row['schedule'])+' МСК.'
    await m.answer(text)


async def leave_prompt(m,uid):
    row = store.app(uid)
    if not row or row['status'] not in ('approved','joined'):
        raise ValueError('Ты сейчас не в составе.')
    if uid == OWNER:
        raise ValueError('Владелец не может выйти с баном самого себя.')
    await m.answer('Выйти из команды? Ты исчезнешь из состава и расписания, место освободится. '
                   'В группе будет установлен бан до разбана владельцем; сам бот останется доступен.',
                   reply_markup=kb([('🚪 Да, выйти с баном в группе','leaveconfirm')],[('Остаться','stay')]))


async def handle_callback(c):
    uid = c.from_user.id
    data = c.data or ''
    m = c.message
    if not m or m.chat.type != 'private':
        return
    store.touch(c.from_user)
    public = {'apply','status','leave','leaveconfirm','stay'}
    if data not in public and not data.startswith('direction:') and uid != OWNER:
        raise ValueError('Доступ только владельцу.')
    user = store.user(uid)
    if user['bot_banned'] and uid != OWNER:
        raise ValueError('Доступ заблокирован владельцем.')
    if data.startswith('direction:'):
        row = store.app(uid)
        if not row or row['status']!='draft' or FIELDS[row['step']][0]!='direction':
            raise ValueError('Этот шаг уже пройден. Продолжить анкету: /start.')
        role = data.split(':')[1]
        labels = {value:key for key,value in DIRECTIONS.items()}
        if role not in labels:
            raise ValueError('Неизвестное направление.')
        answers = json.loads(row['answers'])
        answers['direction'] = labels[role]
        store.edit_app(uid,answers=json.dumps(answers,ensure_ascii=False),role=role,step=row['step']+1)
        return await ask(m,row['step']+1)
    if data == 'status':
        return await show_status(m,uid)
    if data == 'stay':
        return await m.answer('Ты остаёшься в команде.')
    if data == 'leave':
        return await leave_prompt(m,uid)
    if data == 'leaveconfirm':
        row = store.app(uid)
        if not row or row['status'] not in ('approved','joined'):
            raise ValueError('Ты уже не в составе.')
        await remove_from_team(uid)
        await m.answer('Ты вышел из команды. Состав обновлён, место освобождено. Бан действует только в группе; бот доступен.')
        return await tell_owner(f'🚪 {member_label(row)} вышел из команды и заблокирован в группе.')
    if data == 'apply':
        if user['chat_banned']:
            raise ValueError('У тебя бан в группе. Для повторного вступления владелец должен снять его в панели.')
        if not group_id():
            raise ValueError('Владелец пока не настроил группу. Попробуй позже.')
        row = store.app(uid)
        if row and row['status'] in ('pending','approved','joined'):
            return await show_status(m,uid)
        if not row or row['status'] != 'draft':
            store.run('DELETE FROM applications WHERE user_id=?',(uid,))
            store.run('INSERT INTO applications(user_id) VALUES (?)',(uid,))
        return await ask(m,store.app(uid)['step'])
    if data == 'admin':
        return await admin_menu(m)
    if data == 'settings':
        return await settings_menu(m)
    if data == 'cancel':
        return await cancel(m,uid)
    if data.startswith('apps:'):
        return await list_apps(m,int(data.split(':')[1]))
    if data == 'team' or data.startswith('team:'):
        return await show_team(m,int(data.split(':')[1]) if ':' in data else 0)
    if data.startswith('review:'):
        return await review(m,int(data.split(':')[1]))
    if data.startswith('user:'):
        return await user_card(m,int(data.split(':')[1]))
    if data.startswith('users:'):
        page = max(0,int(data.split(':')[1]))
        rows = store.rows('SELECT * FROM users ORDER BY user_id LIMIT 8 OFFSET ?',(page*8,))
        total = store.one('SELECT COUNT(*) AS n FROM users')['n']
        keys = [[(f'{r["name"][:30]} · {r["user_id"]}',f'user:{r["user_id"]}')] for r in rows]
        nav = []
        if page:
            nav.append(('⬅️',f'users:{page-1}'))
        if total > (page+1)*8:
            nav.append(('➡️',f'users:{page+1}'))
        if nav:
            keys.append(nav)
        keys.append([('🏠 Панель','admin')])
        return await m.answer(f'Пользователей: {total}.',reply_markup=kb(*keys))
    if data == 'setgroup':
        store.set_flow(OWNER,'group')
        rights = ChatAdministratorRights(is_anonymous=False, can_manage_chat=True,
            can_delete_messages=False, can_manage_video_chats=False, can_restrict_members=True,
            can_promote_members=False, can_change_info=False, can_invite_users=True,
            can_post_stories=False, can_edit_stories=False, can_delete_stories=False, can_send_welcome_messages=False)
        request = KeyboardButtonRequestChat(request_id=74101,chat_is_channel=False,
                     chat_has_username=False,bot_is_member=True,user_administrator_rights=rights,bot_administrator_rights=rights)
        return await m.answer('Сначала добавь бота администратором в приватную супергруппу. '
                              'Выбери её кнопкой или введи ID вида -1001234567890.\n/cancel — отменить.',
                              reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='Выбрать группу',request_chat=request)]],resize_keyboard=True,one_time_keyboard=True))
    if data in ('settag','setelo'):
        store.set_flow(OWNER,'tag' if data=='settag' else 'elo')
        return await m.answer('Введи тег аккаунта, например #2QGJPJLJQG.\n/cancel — отмена.' if data=='settag'
                              else 'Введи текущее Elo и рекорд через пробел: 8500 9200. Это ручные данные, не результат API.\n/cancel — отмена.')
    if data == 'queue_refresh':
        await sync_queue(create=True)
        return await m.answer('Очередь по расписанию обновлена.')
    if data == 'refresh':
        launch(fetch_stats())
        return await m.answer('Обновление запрошено. Статус API будет показан в посте.')
    if data.startswith('accept:') or data.startswith('time:'):
        target = int(data.split(':')[1])
        row = store.app(target)
        accepting = data.startswith('accept:')
        if not row or row['status'] not in (('pending',) if accepting else ('approved','joined')):
            raise ValueError('Заявка обработана или участник не в составе.')
        store.set_flow(OWNER,'accept_time' if accepting else 'change_time',uid=target)
        preferred = json.loads(row['answers']).get('preferred_time','—')
        return await m.answer(f'Игрок: {member_label(row)}\nПожелание: {escape(preferred)}\n'
                              'Напиши согласованное время ПО МОСКВЕ: 21:00-22:00.\n/cancel — отмена.')
    if data.startswith('acceptconfirm:'):
        flow = store.flow(OWNER)
        if not flow or flow['kind'] != 'accept_confirm' or flow['payload']['nonce'] != data.split(':')[1]:
            raise ValueError('Подтверждение устарело. Открой заявку заново.')
        target = flow['payload']['uid']
        row = store.app(target)
        candidate = store.user(target)
        if not row or row['status'] != 'pending':
            raise ValueError('Заявка уже обработана или удалена.')
        if candidate['bot_banned'] or candidate['chat_banned']:
            raise ValueError('У игрока есть бан. Сначала сними его.')
        await check_group(group_id())
        link = await new_invite(target)
        store.edit_app(target,status='approved',schedule=flow['payload']['schedule'])
        store.clear_flow(OWNER)
        delivered = await issue_invite(target,link)
        await sync_panel()
        return await m.answer('✅ Игрок принят, место зарезервировано.' + ('' if delivered else '\n⚠️ Личное сообщение не доставлено. Игрок должен разблокировать бота и отправить /invite.'))
    if data.startswith('reject:'):
        target = int(data.split(':')[1])
        row = store.app(target)
        if not row or row['status'] != 'pending':
            raise ValueError('Заявка уже обработана или удалена.')
        store.edit_app(target,status='rejected')
        await dm(target,'❌ Владелец отклонил заявку. Спасибо за интерес к команде.')
        return await m.answer('Заявка отклонена.')
    if data.startswith('role:'):
        _,target,role = data.split(':')
        row = store.app(int(target))
        if role not in ('ladder','ranked','helper','member') or not row or row['status'] not in ('approved','joined'):
            raise ValueError('Не удалось назначить роль: участник отсутствует в составе.')
        store.edit_app(int(target),role=role)
        await sync_panel()
        return await m.answer('Должность изменена. У участника одна основная должность.')
    if data.startswith('banask:'):
        _,scope,target = data.split(':')
        if int(target) == OWNER:
            raise ValueError('Нельзя заблокировать владельца.')
        return await m.answer(f'Заблокировать ID {target} {"в боте" if scope=="bot" else "в группе и удалить из состава"}?',
                              reply_markup=kb([('Да, заблокировать',f'ban:{scope}:{target}')],[('Отмена','admin')]))
    if data.startswith('ban:') or data.startswith('unban:'):
        action,scope,target_s = data.split(':')
        target = int(target_s)
        if target == OWNER or scope not in ('bot','chat') or not store.user(target):
            raise ValueError('Некорректный пользователь или действие.')
        if scope == 'bot':
            store.run('UPDATE users SET bot_banned=? WHERE user_id=?',(int(action=='ban'),target))
        elif action == 'ban':
            await remove_from_team(target)
        else:
            if not group_id():
                raise ValueError('Группа не настроена.')
            await bot.unban_chat_member(group_id(),target,only_if_banned=True)
            store.run('UPDATE users SET chat_banned=0 WHERE user_id=?',(target,))
        return await user_card(m,target)
    if data == 'broadcast':
        return await m.answer('Выбери формат рассылки:',reply_markup=kb(
            [('✨ Как в Telegram · с эмодзи', 'bcmode:telegram')],
            [('📝 MarkdownV2', 'bcmode:markdown')], [('Отмена','admin')]))
    if data.startswith('bcmode:'):
        mode = data.split(':')[1]
        if mode not in ('telegram','markdown'):
            raise ValueError('Неизвестный формат.')
        store.set('broadcast_mode',mode)
        await begin_broadcast(m)
        if mode == 'markdown':
            await m.answer('Режим MarkdownV2: *жирный*, _курсив_, ~зачёркнутый~, ||спойлер||, [ссылка](https://example.com). '
                           'Спецсимволы требуют экранирования обратным слешем. Для фото разметку пиши в подписи. '
                           'Custom emoji: ![🙂](tg://emoji?id=ID). Отправляй синтаксис обычным текстом, без форматирования редактором Telegram.',parse_mode=None)
        else:
            await m.answer('Оформи сообщение средствами Telegram: жирный, курсив, ссылки, спойлеры и доступные Premium/custom emoji. '
                           'Бот копирует текст и entities без повторного разбора. Для custom emoji нужен Premium у владельца бота в BotFather либо право по условиям Fragment. Проверь отображение в предпросмотре.')
        return
    if data.startswith('bccancel:'):
        cid = int(data.split(':')[1])
        store.run("UPDATE campaigns SET status='cancelled' WHERE id=? AND status='draft'",(cid,))
        store.clear_flow(OWNER)
        return await m.answer('Рассылка отменена (уже запущенная отправка этим не прерывается).')
    if data.startswith('bcsend:'):
        cid = int(data.split(':')[1])
        campaign = store.one('SELECT * FROM campaigns WHERE id=?',(cid,))
        if not campaign or campaign['status'] != 'draft':
            raise ValueError('Рассылка уже запущена или отменена.')
        # Snapshot targets and start state in one transaction.
        with store.db:
            store.db.execute('''INSERT OR IGNORE INTO deliveries(campaign_id,user_id)
                              SELECT ?,user_id FROM users WHERE subscribed=1 AND reachable=1 AND bot_banned=0 AND user_id!=?''',(cid,OWNER))
            store.db.execute("UPDATE campaigns SET status='running' WHERE id=?",(cid,))
        store.clear_flow(OWNER)
        launch(broadcast_worker())
        return await m.answer(f'📤 Рассылка №{cid} запущена в личные сообщения пользователей бота. Итог придёт сюда.')


@dp.message()
async def on_message(m: Message):
    if not m.from_user:
        return
    try:
        async with lock:
            if m.chat.type == 'private':
                await handle_private(m)
            elif m.from_user.id == OWNER and m.chat.id == group_id() and (m.text or '').split('@')[0] == '/panel':
                await check_group(group_id())
                await sync_panel(create=True)
                store.set('queue_enabled','1')
                await sync_queue(create=True)
            elif m.chat.id == group_id():
                await group_queue_message(m)
    except (ValueError,IndexError) as exc:
        await m.answer(escape(str(exc)) if isinstance(exc,ValueError) else 'Неверный формат. /help')
    except TelegramAPIError:
        logging.exception('Message action failed')
        await m.answer('⚠️ Ошибка Telegram. Проверь права бота и параметры. Если это рассылка, проверь разметку и возможность отправки custom emoji. '
                       'Затем повтори действие; состояние заявки можно посмотреть через /status.')


@dp.callback_query()
async def on_callback(c: CallbackQuery):
    # Answer immediately to stop the client's spinner before a network operation.
    with contextlib.suppress(TelegramAPIError):
        await c.answer()
    try:
        async with lock:
            await handle_callback(c)
    except (ValueError,IndexError) as exc:
        if c.message:
            await c.message.answer(escape(str(exc)) if isinstance(exc,ValueError) else 'Кнопка устарела. /admin')
    except TelegramAPIError:
        logging.exception('Callback action failed')
        if c.message:
            await c.message.answer('⚠️ Telegram не выполнил действие. Проверь права бота и попробуй снова. '
                                   'Состояние сохранено; повторное одобрение не добавит участника повторно.')


@dp.chat_join_request()
async def on_join(req: ChatJoinRequest):
    if req.chat.id != group_id():
        return
    async with lock:
        row = store.app(req.from_user.id)
        user = store.user(req.from_user.id)
        allowed = (row and user and row['status']=='approved' and not user['chat_banned'] and not user['bot_banned']
                   and req.invite_link and req.invite_link.invite_link == row['invite'])
        try:
            if not allowed:
                await bot.decline_chat_join_request(group_id(),req.from_user.id)
                return
            await bot.approve_chat_join_request(group_id(),req.from_user.id)
            store.edit_app(req.from_user.id,status='joined')
            await revoke(row)
            await sync_panel()
            await tell_owner(f'🎉 {member_label(row)} вступил в группу.')
        except TelegramAPIError:
            logging.exception('Join approval failed')
            await tell_owner(f'⚠️ Не удалось обработать вступление ID {req.from_user.id}. Проверь права и запросы в группе.')


@dp.chat_member()
async def on_member(event: ChatMemberUpdated):
    if event.chat.id != group_id():
        return
    async with lock:
        uid = event.new_chat_member.user.id
        row = store.app(uid)
        if not row:
            return
        status = event.new_chat_member.status
        if is_present(event.new_chat_member) and row['status']=='approved':
            # Also reconcile a join that another administrator approved manually.
            store.edit_app(uid,status='joined')
            await revoke(row)
            await sync_panel()
        if status in (ChatMemberStatus.LEFT,ChatMemberStatus.KICKED) and row['status'] in ('approved','joined'):
            store.edit_app(uid,status='removed')
            if uid != OWNER:
                try:
                    await bot.ban_chat_member(group_id(),uid,revoke_messages=False)
                    store.run('UPDATE users SET chat_banned=1 WHERE user_id=?',(uid,))
                except TelegramAPIError:
                    await tell_owner(f'⚠️ ID {uid} вышел, состав очищен, но установить бан не удалось. Заблокируй вручную.')
            await revoke(row)
            await sync_panel()


def is_present(member):
    return (member.status in (ChatMemberStatus.MEMBER,ChatMemberStatus.ADMINISTRATOR,ChatMemberStatus.CREATOR)
            or (member.status == ChatMemberStatus.RESTRICTED and member.is_member))


async def reconcile_members():
    """Recover group membership changes missed during downtime, without deleting queued updates."""
    if not group_id():
        return
    for row in store.members():
        try:
            member = await bot.get_chat_member(group_id(),row['user_id'])
            if is_present(member):
                store.edit_app(row['user_id'],status='joined')
            elif row['status']=='joined':
                store.edit_app(row['user_id'],status='removed')
                await revoke(row)
                if row['user_id'] != OWNER:
                    await bot.ban_chat_member(group_id(),row['user_id'],revoke_messages=False)
                    store.run('UPDATE users SET chat_banned=1 WHERE user_id=?',(row['user_id'],))
        except TelegramAPIError:
            logging.exception('Membership reconciliation failed for %s',row['user_id'])


def queue_rows():
    return [r for r in store.members() if r['status']=='joined']


async def sync_queue(create=False):
    async with queue_lock:
        gid=group_id()
        if not gid:
            return False
        mid=store.setting('queue_id')
        if not mid and not create and store.setting('queue_enabled')!='1':
            return False
        now=datetime.now(MSK)
        rows=queue_rows()
        text=queue_text(rows,now)
        digest=hashlib.sha256(text.encode()).hexdigest()
        if not create and mid and digest==store.setting('queue_hash'):
            return True
        try:
            if mid:
                try:
                    await tg_call(bot.edit_message_text,text,chat_id=gid,message_id=int(mid))
                except TelegramBadRequest as exc:
                    reason=str(exc).lower()
                    if 'message is not modified' in reason:
                        pass
                    elif 'message to edit not found' in reason:
                        mid=None
                    else:
                        raise
            if not mid:
                result=await tg_call(bot.send_message,gid,text)
                store.set('queue_id',result.message_id)
            store.set('queue_hash',digest)
            store.set('queue_enabled','1')
            return True
        except TelegramAPIError:
            logging.exception('Queue update failed; retry in 30 seconds')
            return False


async def queue_worker():
    while True:
        try:
            await sync_queue()
        except Exception:
            logging.exception('Queue cycle failed')
        await asyncio.sleep(30)


async def group_queue_message(m):
    # Deliberately ignore natural-language occupancy messages: people coordinate in chat themselves.
    cmd=(m.text or '').strip().split('@')[0]
    if cmd=='/queue':
        row=store.app(m.from_user.id)
        if m.from_user.id==OWNER or (row and row['status']=='joined'):
            await sync_queue(create=True)


async def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    try:
        me = await bot.get_me()
        logging.info('Starting @%s',me.username)
        await bot.delete_webhook(drop_pending_updates=False)
        await reconcile_members()
        launch(stats_worker())
        launch(queue_worker())
        launch(broadcast_worker())
        await tell_owner('✅ Бот запущен. Панель управления: /admin.\n'
                         'Секреты хранятся только на сервере. Статистика кубков обновляется каждые 5 минут.')
        await dp.start_polling(bot,allowed_updates=dp.resolve_used_update_types(),close_bot_session=False)
    finally:
        tasks = list(background)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        await bot.session.close()
        store.db.close()


if __name__ == '__main__':
    asyncio.run(main())
