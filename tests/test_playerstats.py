import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from html.parser import HTMLParser
from playerstats import extract_player
from helpers import panel_parts, panel_text
from playqueue import queue_text

class TagValidator(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack=[]
    def handle_starttag(self,tag,attrs):
        assert tag in ('b','i','a','code','pre')
        self.stack.append(tag)
    def handle_endtag(self,tag):
        assert self.stack.pop()==tag

class PlayerStatsTests(unittest.TestCase):
    def test_official_player_fields(self):
        result=extract_player({'name':'Infinity','trophies':39420,'highestTrophies':39422,
                               '3vs3Victories':1500,'soloVictories':12,'duoVictories':55,'expLevel':100,
                               'club':{'name':'Club'}})
        self.assertEqual(result['wins_3v3'],1500)
        self.assertEqual(result['highest'],39422)
        self.assertEqual(result['club'],'Club')

    def test_powerplay_is_not_elo(self):
        result=extract_player({'trophies':1,'highestPowerPlayPoints':9000,'brawlers':[{'rank':35}]})
        self.assertNotIn('elo',result)
        self.assertNotIn('best_elo',result)
        self.assertEqual(result['ranked_excerpt'],{})

    def test_unknown_ranked_fields_are_diagnostic_not_guessed(self):
        result=extract_player({'trophies':1,'ranked':{'unknown_future_score':9500}})
        self.assertIn('ranked',result['ranked_excerpt'])
        self.assertNotIn('elo',result)

    def test_invalid_trophies(self):
        for value in (None,True,'39420',-1):
            with self.assertRaises(ValueError):
                extract_player({'trophies':value})

    def test_missing_optional_fields_do_not_show_zero(self):
        result=extract_player({'trophies':0})
        self.assertEqual(result['trophies'],0)
        self.assertNotIn('wins_3v3',result)
        self.assertNotIn('highest',result)

    def test_formatted_stats_and_escaping(self):
        stats=extract_player({'name':'<bad>&','trophies':39420,'highestTrophies':39422,'3vs3Victories':1000})
        text=panel_text([],'hawkuy','18.10.2026',stats,'#2QGJPJLJQG')
        self.assertIn('39 420',text)
        self.assertIn('&lt;bad&gt;&amp;',text)
        self.assertIn('3×3:',text)

    def test_main_message_parts_have_balanced_html(self):
        rows=[{'user_id':i,'name':'A<&','username':None,'status':'joined','schedule':'21:00–22:00','role':'ladder'} for i in range(250)]
        for text in panel_parts(rows,'hawkuy','18.10.2026',{},'#2QGJPJLJQG'):
            parser=TagValidator()
            parser.feed(text)
            self.assertFalse(parser.stack)
            self.assertLess(len(text),4096)

    def test_queue_html_and_chat_only_coordination(self):
        text=queue_text([],datetime(2026,10,18,11,0,tzinfo=ZoneInfo('Europe/Moscow')))
        parser=TagValidator()
        parser.feed(text)
        self.assertFalse(parser.stack)
        self.assertIn('Бот не учитывает эти сообщения',text)
        self.assertIn('<b>ОЧЕРЕДЬ НА АККАУНТ</b>',text)
