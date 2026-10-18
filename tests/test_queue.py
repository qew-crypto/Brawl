import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from playqueue import windows, queue_text

MSK=ZoneInfo('Europe/Moscow')

def player(uid,start_end):
    return {'user_id':uid,'name':f'User {uid}','username':f'user{uid}', 'schedule':start_end,'status':'joined','role':'ladder'}

class QueueTests(unittest.TestCase):
    def test_active_interval(self):
        now=datetime(2026,10,18,21,30,tzinfo=MSK)
        a,f=windows([player(1,'21:00–22:00')],now)
        self.assertEqual(a[0]['row']['user_id'],1)
        self.assertIn('Сейчас очередь @user1',queue_text([player(1,'21:00–22:00')],now))

    def test_end_exclusive_next_starts(self):
        now=datetime(2026,10,18,22,0,tzinfo=MSK)
        a,f=windows([player(1,'21:00–22:00'),player(2,'22:00–23:00')],now)
        self.assertEqual([x['row']['user_id'] for x in a],[2])

    def test_overnight(self):
        now=datetime(2026,10,19,0,30,tzinfo=MSK)
        a,f=windows([player(1,'23:00–01:00')],now)
        self.assertEqual(len(a),1)
        self.assertEqual(a[0]['end'].day,19)

    def test_empty_queue_invites_chat_coordination_only(self):
        now=datetime(2026,10,18,11,0,tzinfo=MSK)
        text=queue_text([],now)
        self.assertIn('Сейчас на аккаунте нет очереди',text)
        self.assertIn('Я буду играть на аккаунте до 22:00',text)
        self.assertIn('Бот не учитывает эти сообщения',text)

    def test_overlaps_are_not_fake_single_occupancy(self):
        now=datetime(2026,10,18,21,30,tzinfo=MSK)
        text=queue_text([player(1,'21:00–22:00'),player(2,'21:00–23:00')],now)
        self.assertIn('Пересечение',text)
        self.assertIn('@user1',text)
        self.assertIn('@user2',text)

    def test_large_overlap_is_bounded(self):
        now=datetime(2026,10,18,21,30,tzinfo=MSK)
        text=queue_text([player(i,'21:00–22:00') for i in range(500)],now)
        self.assertLess(len(text),4096)

if __name__=='__main__':
    unittest.main()
