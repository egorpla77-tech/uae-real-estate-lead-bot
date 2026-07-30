import asyncio
import getpass
import os
from pathlib import Path

from telegram_collector import parse_telegram_proxy


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
DATA_DIR = BASE_DIR / "data"


def load_env(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


async def main() -> None:
    load_env()
    try:
        from telethon import TelegramClient
    except Exception as exc:
        raise SystemExit(f"Telethon не установлен. Сначала выполните: python -m pip install -r requirements.txt\n{exc}")

    api_id = os.getenv("TELEGRAM_API_ID", "").strip() or input("TELEGRAM_API_ID: ").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip() or getpass.getpass("TELEGRAM_API_HASH: ").strip()
    phone = os.getenv("TELEGRAM_PHONE", "").strip() or input("Телефон Telegram в формате +79990000000: ").strip()
    session_name = os.getenv("TELEGRAM_SESSION_NAME", "telegram_auto_leads").strip() or "telegram_auto_leads"

    if not api_id or not api_hash or not phone:
        raise SystemExit("Нужны TELEGRAM_API_ID, TELEGRAM_API_HASH и телефон.")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(
        str(DATA_DIR / session_name),
        int(api_id),
        api_hash,
        proxy=parse_telegram_proxy(os.getenv("TELEGRAM_PROXY", "")),
    )
    await client.connect()
    try:
        if not await client.is_user_authorized():
            force_sms = os.getenv("TELEGRAM_FORCE_SMS", "").strip().lower() in {"1", "true", "yes", "да", "on"}
            await client.send_code_request(phone, force_sms=force_sms)
            code = os.getenv("TELEGRAM_LOGIN_CODE", "").strip() or input("Код из Telegram: ").strip()
            try:
                await client.sign_in(phone=phone, code=code)
            except Exception as exc:
                if "password" not in type(exc).__name__.lower():
                    raise
                password = os.getenv("TELEGRAM_2FA_PASSWORD", "").strip() or getpass.getpass("Пароль 2FA Telegram: ")
                await client.sign_in(password=password)
        me = await client.get_me()
        username = f"@{me.username}" if getattr(me, "username", None) else str(getattr(me, "id", "unknown"))
        print(f"Telegram-сессия готова: {username}")
        print(f"Файл сессии: {DATA_DIR / session_name}.session")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
