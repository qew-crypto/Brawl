"""Extract only known public player fields; no guessed Ranked/Power Play mapping."""
from datetime import datetime
from zoneinfo import ZoneInfo

MSK = ZoneInfo('Europe/Moscow')


def number(value):
    return value if isinstance(value,int) and not isinstance(value,bool) and value>=0 else None


def extract_player(data, now=None):
    if not isinstance(data,dict) or number(data.get('trophies')) is None:
        raise ValueError('API не вернул корректное число кубков.')
    result={'trophies':data['trophies'], 'name':str(data.get('name',''))[:80]}
    mapping={'highestTrophies':'highest','3vs3Victories':'wins_3v3','soloVictories':'wins_solo',
             'duoVictories':'wins_duo','expLevel':'level'}
    for field,key in mapping.items():
        value=number(data.get(field))
        if value is not None:
            result[key]=value
    club=data.get('club')
    if isinstance(club,dict) and isinstance(club.get('name'),str):
        result['club']=club['name'][:80]
    # Diagnostic keys/excerpts are public player data only; never auth headers or credentials.
    result['api_keys']=sorted(str(k)[:80] for k in data.keys())
    result['ranked_excerpt']={str(k)[:80]:v for k,v in data.items()
                              if any(term in str(k).lower() for term in ('ranked','elo','league'))}
    result['updated']=(now or datetime.now(MSK)).astimezone(MSK).strftime('%d.%m.%Y %H:%M МСК')
    result['state']='Данные Brawl Stars API актуальны'
    return result
