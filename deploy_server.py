import os
from pathlib import Path

import paramiko


BASE = Path(__file__).resolve().parent
REMOTE = "/opt/uae-real-estate-lead-bot"
SERVICE = "uae-real-estate-lead-bot.service"


def current_logs_command() -> str:
    return (
        f'journalctl -u {SERVICE} '
        f'--since "$(systemctl show {SERVICE} --property=ActiveEnterTimestamp --value)" '
        "--no-pager -o cat"
    )


def run(client, command: str, timeout: int = 600) -> str:
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    code = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    if code:
        raise RuntimeError(f"Remote command failed ({code}): {command}\n{err or out}")
    return out


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def server_env_text(existing_values: dict[str, str] | None = None) -> str:
    values = read_env(BASE / ".env")
    existing_values = existing_values or {}
    legacy_values: dict[str, str] = {}
    legacy_path = values.get("LEGACY_ENV_PATH", "")
    if legacy_path:
        legacy_values = read_env(Path(legacy_path))
    if not values.get("VK_TOKEN"):
        if legacy_values.get("VK_TOKEN"):
            values["VK_TOKEN"] = legacy_values["VK_TOKEN"]
    if not values.get("VK_TOKENS") and legacy_values.get("VK_TOKENS"):
        values["VK_TOKENS"] = legacy_values["VK_TOKENS"]
    values.pop("LEGACY_ENV_PATH", None)
    if not (values.get("VK_TOKEN") or values.get("VK_TOKENS")) or not values.get("TG_BOT_TOKEN"):
        raise RuntimeError("VK_TOKEN/VK_TOKENS or TG_BOT_TOKEN is unavailable for server deployment")
    bot_proxy = values.get("TELEGRAM_BOT_PROXY", "").lower()
    if "127.0.0.1" in bot_proxy or "localhost" in bot_proxy:
        values["TELEGRAM_BOT_PROXY"] = os.environ.get("SERVER_TELEGRAM_BOT_PROXY", "direct")
    user_proxy = values.get("TELEGRAM_PROXY", "").lower()
    if "127.0.0.1" in user_proxy or "localhost" in user_proxy:
        values["TELEGRAM_PROXY"] = (
            os.environ.get("SERVER_TELEGRAM_PROXY", "").strip()
            or existing_values.get("TELEGRAM_PROXY", "").strip()
        )
    return "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"


def remote_exists(sftp, path: str) -> bool:
    try:
        sftp.stat(path)
        return True
    except FileNotFoundError:
        return False


def read_remote_env(sftp, path: str) -> dict[str, str]:
    if not remote_exists(sftp, path):
        return {}
    with sftp.open(path, "r") as stream:
        payload = stream.read()
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8-sig", "replace")
    values: dict[str, str] = {}
    for raw_line in str(payload).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main() -> None:
    host = os.environ["SERVER_HOST"]
    user = os.environ.get("SERVER_USER", "root")
    password = os.environ.get("SERVER_PASSWORD", "")
    key_path = os.environ.get("SERVER_SSH_KEY_PATH", "").strip()
    if not password and not key_path:
        raise RuntimeError("Set SERVER_PASSWORD or SERVER_SSH_KEY_PATH for deployment")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    connect_args = {"hostname": host, "username": user, "timeout": 30, "auth_timeout": 30, "banner_timeout": 30}
    if key_path:
        connect_args["pkey"] = paramiko.RSAKey.from_private_key_file(key_path)
    else:
        connect_args["password"] = password
    client.connect(**connect_args)
    try:
        if os.environ.get("CHECK_ONLY") == "1":
            print("ACTIVE=" + run(client, f"systemctl is-active {SERVICE}"))
            print("ENABLED=" + run(client, f"systemctl is-enabled {SERVICE}"))
            print(run(client, f"systemctl show {SERVICE} --property=MainPID,SubState,NRestarts,ActiveEnterTimestamp --no-pager"))
            print("LOGS")
            print(run(client, current_logs_command()))
            return

        run(client, f"install -d -m 755 {REMOTE} {REMOTE}/deploy {REMOTE}/data {REMOTE}/tests")
        sftp = client.open_sftp()
        try:
            files = [
                ".env.example",
                ".gitignore",
                "README.md",
                "bot.py",
                "classifier.py",
                "instagram_collector.py",
                "telegram_collector.py",
                "setup_telegram_session.py",
                "setup_telegram_qr_session.py",
                "sheets_sync.py",
                "source_discovery.py",
                "storage.py",
                "requirements.txt",
                "discovery_candidates.json",
                "tests/test_classifier.py",
                "tests/test_access.py",
                "tests/test_instagram_collector.py",
                "tests/test_source_discovery.py",
                "tests/test_telegram_collector.py",
                "deploy/resolv.conf",
                "deploy/uae-real-estate-lead-bot.service",
            ]
            for relative in files:
                local = BASE / relative
                remote = f"{REMOTE}/{relative.replace(os.sep, '/')}"
                sftp.put(str(local), remote)

            remote_env_path = f"{REMOTE}/.env"
            existing_values = read_remote_env(sftp, remote_env_path)
            with sftp.open(remote_env_path, "w") as stream:
                stream.write(server_env_text(existing_values))
            sftp.chmod(f"{REMOTE}/.env", 0o600)

            # Первая установка может забрать уже занятые локальные места доступа.
            # При последующих обновлениях серверные данные никогда не перезаписываются.
            for name in ["access.json", "leads.json", "source_cursor.json"]:
                local = BASE / "data" / name
                remote = f"{REMOTE}/data/{name}"
                if local.exists() and not remote_exists(sftp, remote):
                    sftp.put(str(local), remote)
            for local in (BASE / "data").glob("instagram_*.session"):
                remote = f"{REMOTE}/data/{local.name}"
                if not remote_exists(sftp, remote):
                    sftp.put(str(local), remote)
                    sftp.chmod(remote, 0o600)
            for local in (BASE / "data").glob("telegram_*.session"):
                remote = f"{REMOTE}/data/{local.name}"
                if not remote_exists(sftp, remote):
                    sftp.put(str(local), remote)
                    sftp.chmod(remote, 0o600)
            google_credentials = BASE / "data" / "google_service_account.json"
            if google_credentials.exists():
                remote = f"{REMOTE}/data/google_service_account.json"
                sftp.put(str(google_credentials), remote)
                sftp.chmod(remote, 0o600)

            sftp.put(
                str(BASE / "deploy" / "uae-real-estate-lead-bot.service"),
                f"/etc/systemd/system/{SERVICE}",
            )
        finally:
            sftp.close()

        try:
            run(client, f"python3 -m venv {REMOTE}/.venv")
        except RuntimeError:
            run(client, "apt-get update", timeout=600)
            run(client, "DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv", timeout=600)
            run(client, f"python3 -m venv {REMOTE}/.venv")

        try:
            run(client, f"{REMOTE}/.venv/bin/pip install --disable-pip-version-check -r {REMOTE}/requirements.txt", timeout=600)
        except RuntimeError:
            sibling_venvs = [
                "/opt/russia-auto-lead-bot/.venv",
                "/opt/omsk-realty-lead-bot/.venv",
                "/opt/spb-realty-lead-bot/.venv",
            ]
            copied = False
            for sibling_venv in sibling_venvs:
                try:
                    run(client, f"test -x {sibling_venv}/bin/python")
                    run(client, f"cp -a {sibling_venv}/. {REMOTE}/.venv/")
                    run(client, f"{REMOTE}/.venv/bin/python -c 'import requests'", timeout=120)
                    print("PIP_FALLBACK=" + sibling_venv)
                    copied = True
                    break
                except RuntimeError:
                    continue
            if not copied:
                raise

        run(client, f"cd {REMOTE} && .venv/bin/python -m py_compile bot.py classifier.py instagram_collector.py telegram_collector.py setup_telegram_session.py setup_telegram_qr_session.py sheets_sync.py source_discovery.py storage.py")
        run(client, f"cd {REMOTE} && .venv/bin/python -m unittest discover -s tests -q", timeout=120)
        run(client, "systemctl daemon-reload")
        run(client, f"systemctl enable {SERVICE}")
        run(client, f"systemctl restart {SERVICE}")
        print("ACTIVE=" + run(client, f"systemctl is-active {SERVICE}"))
        print("ENABLED=" + run(client, f"systemctl is-enabled {SERVICE}"))
        print(run(client, f"systemctl show {SERVICE} --property=MainPID,SubState,NRestarts,ActiveEnterTimestamp --no-pager"))
        print("LOGS")
        print(run(client, current_logs_command()))
    finally:
        client.close()


if __name__ == "__main__":
    main()
