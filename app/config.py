"""Configuración central. Todos los secretos se leen de variables de entorno (nunca del frontend)."""
from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

log = logging.getLogger("jobhunter.config")

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Carga un archivo .env simple (KEY=VALUE) si existe, sin pisar variables ya definidas."""
    env_file = Path(os.environ.get("JOBHUNTER_ENV_FILE", BASE_DIR / ".env"))
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


_load_dotenv()


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def env_bool(name: str, default: bool = False) -> bool:
    value = env(name)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "si", "sí", "on")


DATA_DIR = Path(env("DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
FILES_DIR = DATA_DIR / "files"
FILES_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = env("DATABASE_URL", f"sqlite:///{DATA_DIR / 'jobhunter.db'}")
APP_BASE_URL = env("APP_BASE_URL", "http://localhost:8000").rstrip("/")
TIMEZONE = env("TZ_DEFAULT", "America/Argentina/Buenos_Aires")
ALLOW_REGISTRATION = env_bool("ALLOW_REGISTRATION", True)
COOKIE_SECURE = env_bool("COOKIE_SECURE", False)
RUN_SCHEDULER_IN_WEB = env_bool("RUN_SCHEDULER_IN_WEB", False)


def _secret(name: str, generator) -> str:
    """Lee un secreto de entorno. En desarrollo, si falta, lo genera y lo persiste en DATA_DIR
    (con advertencia). En producción definilo siempre por variable de entorno."""
    value = env(name)
    if value:
        return value
    path = DATA_DIR / f".{name.lower()}"
    if path.exists():
        return path.read_text().strip()
    value = generator()
    path.write_text(value)
    os.chmod(path, 0o600)
    log.warning("%s no está definido: se generó uno local en %s (solo para desarrollo).", name, path)
    return value


def _fernet_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


SECRET_KEY = _secret("APP_SECRET_KEY", lambda: secrets.token_urlsafe(48))
FILE_ENCRYPTION_KEY = _secret("FILE_ENCRYPTION_KEY", _fernet_key)

# --- IA (Claude) ---
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = env("ANTHROPIC_MODEL", "claude-opus-5-5")
AI_ENABLED = env_bool("AI_ENABLED", True) and bool(ANTHROPIC_API_KEY or env("ANTHROPIC_AUTH_TOKEN"))

# --- Email saliente (informe diario) ---
SMTP_HOST = env("SMTP_HOST")
SMTP_PORT = int(env("SMTP_PORT", "587"))
SMTP_USER = env("SMTP_USER")
SMTP_PASSWORD = env("SMTP_PASSWORD")
SMTP_FROM = env("SMTP_FROM", SMTP_USER)
SMTP_STARTTLS = env_bool("SMTP_STARTTLS", True)
SMTP_SSL = env_bool("SMTP_SSL", False)

# --- Email entrante (alertas de LinkedIn / Indeed / ZonaJobs / Computrabajo) ---
IMAP_HOST = env("IMAP_HOST")
IMAP_PORT = int(env("IMAP_PORT", "993"))
IMAP_USER = env("IMAP_USER")
IMAP_PASSWORD = env("IMAP_PASSWORD")
IMAP_FOLDER = env("IMAP_FOLDER", "INBOX")

# --- APIs de empleo ---
JOOBLE_API_KEY = env("JOOBLE_API_KEY")
JOOBLE_API_URL = env("JOOBLE_API_URL", "https://jooble.org/api/")
CAREERJET_API_KEY = env("CAREERJET_API_KEY")
CAREERJET_LOCALE = env("CAREERJET_LOCALE", "es_AR")
CAREERJET_USER_IP = env("CAREERJET_USER_IP", "127.0.0.1")

HTTP_TIMEOUT = float(env("HTTP_TIMEOUT", "25"))
HTTP_USER_AGENT = env("HTTP_USER_AGENT", "JobHunterAI/1.0 (asistente personal de busqueda laboral)")
