"""Docker bootstrap: prepare SQLite permissions, drop root, exec the bot.

Never removes/moves an existing database or silently switches to temporary storage.
"""
import os
import stat
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
BOT_UID = 10001
BOT_GID = 10001


def database_path(value):
    path = Path(value or '/app/data/team.sqlite3')
    return path if path.is_absolute() else BASE / path


def prepare_database(path, root=None):
    root = os.geteuid() == 0 if root is None else root
    parent = path.parent
    if parent in (Path('/'), BASE):
        raise ValueError('DB_PATH должен указывать на файл внутри папки данных, например /app/data/team.sqlite3, а не на корень /app.')
    if path.is_symlink() or parent.is_symlink():
        raise ValueError('Файл базы и его папка не должны быть символическими ссылками.')
    if path.exists() and not path.is_file():
        raise ValueError(f'DB_PATH указывает на папку, а нужен файл: {path}. Используйте /app/data/team.sqlite3.')
    parent.mkdir(parents=True, exist_ok=True)
    if root:
        # Only the database directory and SQLite files are adjusted; never chown recursively.
        os.chown(parent, BOT_UID, BOT_GID)
        parent.chmod(stat.S_IMODE(parent.stat().st_mode) | stat.S_IRWXU)
        for item in (path, Path(str(path)+'-wal'), Path(str(path)+'-shm')):
            if item.is_symlink():
                raise ValueError(f'Недопустимая символическая ссылка SQLite: {item}')
            if item.exists():
                os.chown(item, BOT_UID, BOT_GID)
                item.chmod(stat.S_IMODE(item.stat().st_mode) | stat.S_IRUSR | stat.S_IWUSR)
    return path


def verify_write_access(path):
    # SQLite needs to create journal/WAL files in the directory, not just write the main DB.
    with tempfile.NamedTemporaryFile(prefix='.brawl-write-test-', dir=path.parent):
        pass
    if path.exists() and not os.access(path, os.R_OK | os.W_OK):
        raise PermissionError(f'Файл базы недоступен для чтения/записи: {path}')


def main():
    from dotenv import load_dotenv
    load_dotenv(BASE / '.env')
    if not os.getenv('BOT_TOKEN', '').strip() or not os.getenv('OWNER_ID', '').strip():
        raise ValueError('В панели хостинга добавьте переменные BOT_TOKEN и OWNER_ID. Команда запуска образа уже настроена.')
    os.umask(0o077)
    path = prepare_database(database_path(os.getenv('DB_PATH', '')))
    os.environ['DB_PATH'] = str(path)
    if os.geteuid() == 0:
        os.setgroups([])
        os.setgid(BOT_GID)
        os.setuid(BOT_UID)
    verify_write_access(path)
    print(f'SQLite готова: {path}; UID={os.geteuid()}. Запуск бота.', flush=True)
    command = sys.argv[1:] or [sys.executable, str(BASE / 'bot.py')]
    os.execvp(command[0], command)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as exc:
        print(f'Ошибка подготовки базы: {exc}\n'
              'В панели хостинга проверьте: DB_PATH=/app/data/team.sqlite3; '
              'постоянный диск подключён к /app/data с доступом Read/Write. '
              'Если хостинг запрещает менять владельца диска, задайте запуск с UID владельца диска '
              'или обратитесь в поддержку хостинга. Read-only том автоматически исправить нельзя.', file=sys.stderr)
        raise SystemExit(1)
