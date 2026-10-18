import json
import sqlite3
from pathlib import Path


class Store:
    def __init__(self, path):
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(path)
        except (OSError, sqlite3.OperationalError) as exc:
            raise RuntimeError(
                f'Не удалось открыть SQLite: {path}. Папка и файл должны быть доступны '
                'на запись пользователю бота. В Docker используйте обновлённый образ '
                'и DB_PATH=/app/data/team.sqlite3; в панели подключите постоянный диск '
                'к /app/data с доступом Read/Write. Существующая база не удалялась.'
            ) from exc
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
          user_id INTEGER PRIMARY KEY, username TEXT, name TEXT NOT NULL,
          bot_banned INTEGER NOT NULL DEFAULT 0, chat_banned INTEGER NOT NULL DEFAULT 0,
          reachable INTEGER NOT NULL DEFAULT 1, subscribed INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS applications (
          user_id INTEGER PRIMARY KEY, status TEXT NOT NULL DEFAULT 'draft', step INTEGER NOT NULL DEFAULT 0,
          answers TEXT NOT NULL DEFAULT '{}', invite TEXT, schedule TEXT NOT NULL DEFAULT '',
          role TEXT NOT NULL DEFAULT 'member', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS flows (user_id INTEGER PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS campaigns (
          id INTEGER PRIMARY KEY AUTOINCREMENT, source_chat INTEGER NOT NULL, source_message INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS deliveries (
          campaign_id INTEGER NOT NULL, user_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
          error TEXT, PRIMARY KEY(campaign_id,user_id));
        ''')
        if self.db.execute('PRAGMA user_version').fetchone()[0] < 2:
            columns = {r[1] for r in self.db.execute('PRAGMA table_info(applications)')}
            if 'photo' in columns:
                self.db.execute('UPDATE applications SET photo=NULL')
            self.db.execute("UPDATE applications SET step=8 WHERE status='draft' AND step>8")
            self.db.execute('PRAGMA user_version=2')
        # An interruption during Telegram delivery may have delivered a message; do not resend uncertain items.
        self.db.execute("UPDATE deliveries SET status='uncertain' WHERE status='sending'")
        self.db.commit()

    def run(self, sql, args=()):
        result = self.db.execute(sql, args)
        self.db.commit()
        return result

    def rows(self, sql, args=()):
        return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        row = self.db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def touch(self, user):
        self.run('''INSERT INTO users(user_id,username,name) VALUES (?,?,?)
                    ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,name=excluded.name,reachable=1''',
                 (user.id, user.username, user.full_name[:80]))

    def user(self, uid):
        return self.one('SELECT * FROM users WHERE user_id=?', (uid,))

    def app(self, uid):
        return self.one('SELECT a.*,u.username,u.name,u.bot_banned,u.chat_banned FROM applications a JOIN users u USING(user_id) WHERE user_id=?', (uid,))

    def edit_app(self, uid, **values):
        allowed = {'status', 'step', 'answers', 'invite', 'schedule', 'role'}
        if not set(values) <= allowed:
            raise ValueError('Unsupported application column')
        self.run('UPDATE applications SET '+','.join(k+'=?' for k in values)+' WHERE user_id=?', (*values.values(),uid))

    def members(self):
        return self.rows("SELECT a.*,u.username,u.name FROM applications a JOIN users u USING(user_id) WHERE status IN ('approved','joined') ORDER BY created_at,a.user_id")

    def setting(self, key, default=''):
        row = self.one('SELECT value FROM settings WHERE key=?', (key,))
        return row['value'] if row else default

    def set(self, key, value):
        self.run('INSERT OR REPLACE INTO settings VALUES (?,?)', (key,str(value)))

    def flow(self, uid):
        row = self.one('SELECT * FROM flows WHERE user_id=?', (uid,))
        if row:
            row['payload'] = json.loads(row['payload'])
        return row

    def set_flow(self, actor_uid, kind, **payload):
        self.run('INSERT OR REPLACE INTO flows VALUES (?,?,?)', (actor_uid,kind,json.dumps(payload)))

    def clear_flow(self, uid):
        self.run('DELETE FROM flows WHERE user_id=?', (uid,))
