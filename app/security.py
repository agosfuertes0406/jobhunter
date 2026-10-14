"""Autenticación (contraseñas con scrypt, cookie de sesión firmada) y almacenamiento cifrado de archivos."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import shutil
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from .config import FILE_ENCRYPTION_KEY, FILES_DIR, SECRET_KEY
from .db import get_db
from .models import User

SESSION_COOKIE = "jh_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 14  # 14 días

_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="jobhunter-session")
_fernet = Fernet(FILE_ENCRYPTION_KEY.encode())


# ---------- contraseñas ----------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=64)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, digest_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
    except ValueError:
        return False
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=64)
    return hmac.compare_digest(digest, expected)


# ---------- sesiones ----------
def make_session_token(user_id: int) -> str:
    return _serializer.dumps({"uid": user_id})


def read_session_token(token: str) -> int | None:
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
        return int(data["uid"])
    except (BadSignature, SignatureExpired, KeyError, ValueError, TypeError):
        return None


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    uid = read_session_token(token) if token else None
    user = db.get(User, uid) if uid else None
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado")
    # Protección CSRF: las mutaciones deben venir del frontend propio (fetch con cabecera custom).
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-requested-with") != "JobHunter":
        raise HTTPException(status_code=403, detail="Solicitud rechazada (CSRF)")
    return user


# ---------- archivos cifrados ----------
def user_dir(user_id: int) -> Path:
    path = FILES_DIR / f"u{user_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def store_encrypted(user_id: int, name: str, content: bytes) -> str:
    safe = "".join(c for c in name if c.isalnum() or c in "._-")[:120] or "archivo"
    path = user_dir(user_id) / f"{secrets.token_hex(8)}_{safe}.enc"
    path.write_bytes(_fernet.encrypt(content))
    return str(path)


def read_encrypted(path: str) -> bytes:
    return _fernet.decrypt(Path(path).read_bytes())


def delete_file(path: str | None) -> None:
    if path:
        Path(path).unlink(missing_ok=True)


def delete_user_files(user_id: int) -> None:
    shutil.rmtree(FILES_DIR / f"u{user_id}", ignore_errors=True)
