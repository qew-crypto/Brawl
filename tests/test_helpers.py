import unittest
from helpers import validate, normalize_tag, panel_text, panel_parts, FIELDS, member_label

class HelpersTests(unittest.TestCase):
    def test_time(self):
        self.assertEqual(validate('schedule','21:00-22:00'), '21:00–22:00')
        self.assertEqual(validate('schedule','23:00 — 01:00'), '23:00–01:00')
        for value in ('24:00-25:00','21:60-22:00','21:00-21:00','tomorrow'):
            with self.assertRaises(ValueError):
                validate('schedule',value)

    def test_preference_is_free_text(self):
        self.assertEqual(validate('preferred_time','по будням 21:00-22:00'), 'по будням 21:00-22:00')

    def test_hours(self):
        self.assertEqual(validate('hours','1,5'),'1.5')
        for value in ('0','25','NaN','inf','-1','нет'):
            with self.assertRaises(ValueError):
                validate('hours',value)

    def test_tag(self):
        self.assertEqual(normalize_tag('2qgjpjljqg'),'#2QGJPJLJQG')
        with self.assertRaises(ValueError):
            normalize_tag('https://evil.example')

    def test_numbers(self):
        self.assertEqual(validate('trophies','39 420'),'39420')
        with self.assertRaises(ValueError):
            validate('age','0')

    def test_escaping(self):
        self.assertIn('&lt;script&gt;',member_label({'user_id':1,'username':None,'name':'<script>'}))

    def test_no_screenshot(self):
        self.assertNotIn('screenshot',[key for key,_ in FIELDS])
        self.assertEqual(len(FIELDS),10)

    def test_large_panel(self):
        rows=[{'user_id':i,'username':None,'name':'<X>'*20,'schedule':'21:00–22:00','role':'ranked','status':'joined'} for i in range(250)]
        chunks=panel_parts(rows,'hawkuy','18.10.2026',{},'#2QGJPJLJQG')
        self.assertGreater(len(chunks),1)
        self.assertTrue(all(len(x)<4096 for x in chunks))
        self.assertIn('250.', '\n'.join(chunks))

    def test_panel(self):
        rows=[{'user_id':i,'username':'x'*32,'name':'n'*60,'schedule':'21:00–22:00','role':'ranked','status':'approved'} for i in range(10)]
        text=panel_text(rows,'hawkuy','18.10.2026',{},'#2QGJPJLJQG')
        self.assertLess(len(text),4096)
        self.assertIn('ручной ввод',text)
        self.assertEqual(text.count(' ⏳'),10)
        self.assertNotIn('highestPowerPlayPoints',text)

if __name__=='__main__':
    unittest.main()
