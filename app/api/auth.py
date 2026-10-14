from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db
from ..models import User
from ..security import SESSION_COOKIE, SESSION_MAX_AGE, current_user, hash_password, make_session_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    email: str
    password: str
    name: str = ""


def _set_cookie(resp: Response, user_id: int) -> None:
    resp.set_cookie(SESSION_COOKIE, make_session_token(user_id), max_age=SESSION_MAX_AGE, httponly=True,
                    samesite="lax", secure=config.COOKIE_SECURE, path="/")


def user_out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name}


@router.post("/register")
def register(body: Credentials, response: Response, db: Session = Depends(get_db)):
    if not config.ALLOW_REGISTRATION and db.query(User).count() > 0:
        raise HTTPException(403, "El registro está deshabilitado en este servidor.")
    email = body.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(400, "Email inválido.")
    if len(body.password) < 8:
        raise HTTPException(400, "La contraseña debe tener al menos 8 caracteres.")
    if db.query(User).filter_by(email=email).first():
        raise HTTPException(400, "Ya existe una cuenta con ese email.")
    user = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password),
                settings={"report_email": email})
    db.add(user)
    db.commit()
    _set_cookie(response, user.id)
    return user_out(user)


@router.post("/login")
def login(body: Credentials, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).filter_by(email=body.email.strip().lower()).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Email o contraseña incorrectos.")
    _set_cookie(response, user.id)
    return user_out(user)


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return user_out(user)
