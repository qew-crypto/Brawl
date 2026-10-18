"""Offline acceptance checks with Telegram API mocked; no real token or network."""
import importlib
import json
import os
import tempfile
import unittest
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import BanChatMember
from storage import Store

_boot = tempfile.TemporaryDirectory()
os.environ.update(BOT_TOKEN='123456789:'+'A'*35, OWNER_ID='100', DB_PATH=_boot.name+'/bootstrap.sqlite3', GROUP_ID='')
b = importlib.import_module('bot')
b.store.db.close()


def user(uid):
    return Obj(id=uid,username=f'user{uid}',full_name=f'User {uid}')


def message(uid=100,text=None):
    return Obj(from_user=user(uid),chat=Obj(id=uid,type='private'),text=text,
               answer=AsyncMock(return_value=Obj(message_id=42)),photo=None,caption=None,
               media_group_id=None,video=None,document=None,audio=None,voice=None,animation=None,sticker=None,
               message_id=10,chat_shared=None)


def callback(data,uid=100):
    return Obj(data=data,from_user=user(uid),message=message())


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        b.store=Store(self.tmp.name+'/test.sqlite3')
        b.store.set('group',-100123456)
        b.store.set('tag','#2QGJPJLJQG')
        b.bot=AsyncMock()
        b.bot.id=999
        b.bot.get_chat.return_value=Obj(type='supergroup',username=None,title='Team')
        b.bot.get_chat_member.return_value=Obj(status='administrator',can_invite_users=True,can_restrict_members=True)
        b.bot.create_chat_invite_link.return_value=Obj(invite_link='https://t.me/+TEST')
        b.bot.copy_message.return_value=Obj(message_id=123)
        b.bot.send_message.return_value=Obj(message_id=124)
        b.lock=__import__('asyncio').Lock()
        b.panel_lock=__import__('asyncio').Lock()
        b.store.touch(user(100))

    async def asyncTearDown(self):
        for task in list(b.background):
            task.cancel()
        await __import__('asyncio').gather(*list(b.background),return_exceptions=True)
        b.store.db.close()
        self.tmp.cleanup()

    def applicant(self,uid=200,status='pending'):
        b.store.touch(user(uid))
        b.store.run('INSERT INTO applications(user_id,status,answers) VALUES (?,?,?)',
                    (uid,status,json.dumps({'preferred_time':'21:00-22:00'})))

    async def test_group_picker_schema(self):
        await b.handle_callback(callback('setgroup'))
        self.assertEqual(b.store.flow(100)['kind'],'group')

    async def test_direction_application_buttons(self):
        self.applicant(200,'draft')
        index = next(i for i,(key,_) in enumerate(b.FIELDS) if key=='direction')
        b.store.edit_app(200,step=index)
        await b.handle_callback(callback('direction:ranked',200))
        self.assertEqual(b.store.app(200)['role'],'ranked')
        self.assertEqual(b.store.app(200)['step'],index+1)
        await b.handle_private(message(200,text='Пропустить'))
        self.assertEqual(b.store.app(200)['status'],'pending')
        self.assertEqual(b.store.app(200)['role'],'ranked')
        self.assertEqual(json.loads(b.store.app(200)['answers'])['direction'],'⚔️ Ranked')
        b.bot.send_photo.assert_not_awaited()

    async def test_group_occupancy_message_is_ignored(self):
        m=message(200,text='Я буду играть на аккаунте до 22:00')
        m.chat=Obj(id=b.group_id(),type='supergroup')
        await b.group_queue_message(m)
        m.answer.assert_not_awaited()
        b.bot.send_message.assert_not_awaited()
        self.assertEqual(b.store.setting('lease'),'')

    async def test_panel_grows_and_reuses_messages(self):
        for uid in range(200,300):
            self.applicant(uid,'joined')
            b.store.edit_app(uid,schedule='21:00–22:00')
        b.bot.send_message.side_effect=[Obj(message_id=i) for i in range(1000,1050)]
        await b.sync_panel(create=True)
        ids=json.loads(b.store.setting('panel_ids'))
        self.assertGreater(len(ids),1)
        count=b.bot.send_message.await_count
        await b.sync_panel()
        self.assertEqual(b.bot.send_message.await_count,count)
        b.bot.edit_message_text.assert_not_awaited()

    async def test_manual_time_then_confirm(self):
        self.applicant()
        await b.handle_callback(callback('accept:200'))
        self.assertEqual(b.store.flow(100)['kind'],'accept_time')
        await b.handle_private(message(text='22:00-23:00'))
        flow=b.store.flow(100)
        self.assertEqual(flow['kind'],'accept_confirm')
        b.bot.create_chat_invite_link.assert_not_awaited()
        await b.handle_callback(callback('acceptconfirm:'+flow['payload']['nonce']))
        self.assertEqual(b.store.app(200)['schedule'],'22:00–23:00')
        self.assertEqual(b.store.app(200)['status'],'approved')
        self.assertEqual(len(b.store.members()),1)
        with self.assertRaises(ValueError):
            await b.handle_callback(callback('acceptconfirm:'+flow['payload']['nonce']))
        self.assertEqual(b.bot.create_chat_invite_link.await_count,1)

    async def test_can_accept_beyond_ten(self):
        for uid in range(201,211):
            self.applicant(uid,'approved')
        self.applicant()
        b.store.set_flow(100,'accept_confirm',uid=200,schedule='21:00–22:00',nonce='abc')
        await b.handle_callback(callback('acceptconfirm:abc'))
        self.assertEqual(len(b.store.members()),11)
        self.assertEqual(b.store.app(200)['status'],'approved')

    async def test_other_person_cannot_use_invite(self):
        self.applicant(200,'approved')
        b.store.edit_app(200,invite='https://t.me/+TEST')
        req=Obj(chat=Obj(id=b.group_id()),from_user=user(201),invite_link=Obj(invite_link='https://t.me/+TEST'))
        await b.on_join(req)
        b.bot.decline_chat_join_request.assert_awaited_once()
        b.bot.approve_chat_join_request.assert_not_awaited()

    async def test_leave_bans_chat_not_bot(self):
        self.applicant(200,'joined')
        await b.handle_callback(callback('leaveconfirm',200))
        b.bot.ban_chat_member.assert_awaited_once()
        self.assertEqual(b.store.user(200)['bot_banned'],0)
        self.assertEqual(b.store.user(200)['chat_banned'],1)
        self.assertEqual(b.store.app(200)['status'],'removed')
        self.assertEqual(b.store.members(),[])

    async def test_failed_ban_keeps_slot(self):
        self.applicant(200,'joined')
        b.bot.ban_chat_member.side_effect=TelegramBadRequest(method=BanChatMember(chat_id=-100123456,user_id=200),message='not enough rights')
        with self.assertRaises(TelegramBadRequest):
            await b.handle_callback(callback('leaveconfirm',200))
        self.assertEqual(b.store.app(200)['status'],'joined')
        self.assertEqual(b.store.user(200)['chat_banned'],0)

    async def test_admin_callback_denied_to_other_user(self):
        with self.assertRaises(ValueError):
            await b.handle_callback(callback('settings',200))

    async def test_cancel_callback_uses_owner_not_message_sender(self):
        b.store.set_flow(100,'accept_time',uid=200)
        c=callback('cancel')
        c.message.from_user=user(999) # callback's message was sent by the bot
        await b.handle_callback(c)
        self.assertIsNone(b.store.flow(100))

    async def test_markdown_preview_snapshot(self):
        b.store.set('broadcast_mode','markdown')
        m=message(text='*Привет*')
        await b.prepare_broadcast(m)
        self.assertEqual(b.bot.send_message.call_args.kwargs['parse_mode'],'MarkdownV2')
        campaign=b.store.one('SELECT * FROM campaigns')
        self.assertEqual(campaign['source_message'],124)
        self.assertEqual(campaign['status'],'draft')
        self.assertEqual(b.store.rows('SELECT * FROM deliveries'),[])

    async def test_photo_markdown_caption(self):
        b.store.set('broadcast_mode','markdown')
        m=message()
        m.photo=['photo']
        m.caption='*Привет*'
        await b.prepare_broadcast(m)
        self.assertEqual(b.bot.copy_message.call_args.kwargs['caption'],'*Привет*')
        self.assertEqual(b.bot.copy_message.call_args.kwargs['parse_mode'],'MarkdownV2')

    async def test_native_entities_are_copied(self):
        b.store.set('broadcast_mode','telegram')
        await b.prepare_broadcast(message(text='Привет'))
        self.assertEqual(b.bot.copy_message.call_args.args,(100,100,10))
        self.assertEqual(b.bot.copy_message.call_args.kwargs,{})

    async def test_delivery_only_to_eligible_subscribers(self):
        for uid in (200,201,202,203):
            b.store.touch(user(uid))
        b.store.run('UPDATE users SET subscribed=0 WHERE user_id=201')
        b.store.run('UPDATE users SET bot_banned=1 WHERE user_id=202')
        b.store.run('UPDATE users SET reachable=0 WHERE user_id=203')
        b.store.run("INSERT INTO campaigns(source_chat,source_message,status) VALUES (100,42,'running')")
        b.store.run("INSERT INTO deliveries(campaign_id,user_id) VALUES (1,200),(1,201),(1,202),(1,203)")
        await b.broadcast_worker()
        self.assertEqual(b.bot.copy_message.await_count,1)
        self.assertEqual(b.bot.copy_message.call_args.args,(200,100,42))
        self.assertEqual(b.store.one('SELECT status FROM campaigns')['status'],'done')
        self.assertEqual(b.store.one("SELECT COUNT(*) AS n FROM deliveries WHERE status='skipped'")['n'],3)

if __name__=='__main__':
    unittest.main()
