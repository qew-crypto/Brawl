import tempfile
import unittest
from types import SimpleNamespace
from storage import Store

class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=self.tmp.name+'/test.sqlite3'
        self.s=Store(self.path)
        self.s.touch(SimpleNamespace(id=1,username='u',full_name='User'))
        self.s.run('INSERT INTO applications(user_id) VALUES (?)',(1,))

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def test_reservation(self):
        self.assertEqual(len(self.s.members()),0)
        self.s.edit_app(1,status='approved',schedule='21:00–22:00')
        self.assertEqual(len(self.s.members()),1)
        self.s.edit_app(1,status='joined')
        self.assertEqual(len(self.s.members()),1)
        self.s.edit_app(1,status='removed')
        self.assertEqual(len(self.s.members()),0)

    def test_persistent_flow(self):
        self.s.set_flow(1,'accept_time',uid=25)
        other=Store(self.path)
        self.assertEqual(other.flow(1)['payload']['uid'],25)
        other.db.close()

    def test_unknown_column(self):
        with self.assertRaises(ValueError):
            self.s.edit_app(1, arbitrary='bad')

    def test_delivery_recovery(self):
        self.s.run('INSERT INTO campaigns(source_chat,source_message,status) VALUES (?,?,?)',(1,2,'running'))
        self.s.run("INSERT INTO deliveries(campaign_id,user_id,status) VALUES (1,1,'sending')")
        other=Store(self.path)
        self.assertEqual(other.one('SELECT status FROM deliveries')['status'],'uncertain')
        other.db.close()

    def test_touch_preserves_ban_and_subscription(self):
        self.s.run('UPDATE users SET bot_banned=1,subscribed=0 WHERE user_id=1')
        self.s.touch(SimpleNamespace(id=1,username='updated',full_name='New'))
        self.assertEqual(self.s.user(1)['bot_banned'],1)
        self.assertEqual(self.s.user(1)['subscribed'],0)

if __name__=='__main__':
    unittest.main()
