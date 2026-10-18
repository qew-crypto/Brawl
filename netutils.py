"""Network diagnostics. Only public IPv4 is sent to fixed IP-check endpoints; never API keys."""
import ipaddress
import socket
import asyncio
import aiohttp


def checked_ipv4(text):
    return str(ipaddress.IPv4Address(text.strip()))


async def external_ipv4():
    connector = aiohttp.TCPConnector(family=socket.AF_INET)
    async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=8)) as session:
        for url in ('https://api.ipify.org','https://checkip.amazonaws.com'):
            try:
                async with session.get(url) as response:
                    response.raise_for_status()
                    raw = await response.content.read(65)
                    if len(raw)>64:
                        continue
                    return checked_ipv4(raw.decode('ascii'))
            except (aiohttp.ClientError,asyncio.TimeoutError,ValueError,UnicodeError):
                continue
    raise ValueError('Не удалось определить внешний IPv4: хостинг может блокировать IP-сервисы. '
                     'Запросите у поддержки статический исходящий IPv4 приложения для Allowed IPs Brawl Stars API.')
