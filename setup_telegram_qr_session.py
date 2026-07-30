import asyncio
import getpass
import os
from pathlib import Path

import qrcode

from setup_telegram_session import DATA_DIR, load_env
from telegram_collector import parse_telegram_proxy


QR_PATH = DATA_DIR / "telegram_login_qr.png"


def save_qr(url: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    image = qrcode.make(url)
    image.save(QR_PATH)


async def main() -> None:
    load_env()
    try:
        from telethon import TelegramClient
    except Exception as exc:
        raise SystemExit(f"Telethon не установлен. Сначала выполните: python -m pip install -r requirements.txt\n{exc}")

    api_id = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    session_name = os.getenv("TELEGRAM_SESSION_NAME", "telegram_auto_leads").strip() or "telegram_auto_leads"
    if not api_id or not api_hash:
        raise SystemExit("Нужны TELEGRAM_API_ID и TELEGRAM_API_HASH.")

    client = TelegramClient(
        str(DATA_DIR / session_name),
        int(api_id),
        api_hash,
        proxy=parse_telegram_proxy(os.getenv("TELEGRAM_PROXY", "")),
    )
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            username = f"@{me.username}" if getattr(me, "username", None) else str(getattr(me, "id", "unknown"))
            print(f"Telegram-сессия уже готова: {username}")
            return

        while True:
            qr_login = await client.qr_login()
            save_qr(qr_login.url)
            print(f"QR-код сохранён: {QR_PATH}")
            print("Откройте Telegram на телефоне: Настройки → Устройства → Привязать устройство, затем сканируйте QR.")
            try:
                await qr_login.wait(timeout=180)
                break
            except asyncio.TimeoutError:
                print("QR истёк, создаю новый...")
                continue
            except Exception as exc:
                if "password" not in type(exc).__name__.lower():
                    raise
                password = os.getenv("TELEGRAM_2FA_PASSWORD", "").strip() or getpass.getpass("Пароль 2FA Telegram: ")
                await client.sign_in(password=password)
                break

        me = await client.get_me()
        username = f"@{me.username}" if getattr(me, "username", None) else str(getattr(me, "id", "unknown"))
        print(f"Telegram-сессия готова: {username}")
        print(f"Файл сессии: {DATA_DIR / session_name}.session")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
