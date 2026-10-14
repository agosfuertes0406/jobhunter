"""Configuración de búsqueda, fuentes, ejecuciones, informes, ajustes y eliminación de datos."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from .. import config
from ..connectors import CONNECTORS
from ..db import SessionLocal, get_db
from ..models import (
    DEFAULT_SETTINGS, CVVersion, GeneratedCV, Job, Profile, ProfileRevision, Report, Run, SearchConfig, SourceStatus,
    User, utcnow,
)
from ..security import SESSION_COOKIE, current_user, delete_user_files
from ..services.ai import ai_available
from ..services.pipeline import default_search_config, get_search_config, run_for_user
from ..services.report import deliver_report, smtp_configured

router = APIRouter(prefix="/api", tags=["sistema"])


# ---------- configuración de búsqueda ----------
LIST_FIELDS = ["titles", "alt_titles", "keywords", "exclude_keywords", "locations", "modalities", "contract_types",
               "greenhouse_boards", "lever_companies"]


@router.get("/search-config")
def get_config(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return get_search_config(db, user.id)


@router.put("/search-config")
def put_config(body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    data = default_search_config()
    for k in LIST_FIELDS:
        if k in body:
            vals = body[k] if isinstance(body[k], list) else str(body[k]).split(",")
            data[k] = [str(v).strip() for v in vals if str(v).strip()]
    data["modalities"] = [m for m in data["modalities"] if m in ("remoto", "hibrido", "presencial")]
    for k in ("min_salary", "max_years_required", "max_age_days", "min_score"):
        v = body.get(k)
        data[k] = None if v in (None, "") else float(v) if k == "min_salary" else int(v)
    data["max_age_days"] = data["max_age_days"] or 3
    data["min_score"] = data["min_score"] if data["min_score"] is not None else 40
    data["salary_currency"] = (body.get("salary_currency") or "ARS").upper()[:3]
    data["sources"] = {k: bool((body.get("sources") or {}).get(k, False)) for k in CONNECTORS}
    row = db.query(SearchConfig).filter_by(user_id=user.id).first()
    if not row:
        row = SearchConfig(user_id=user.id, data=data)
        db.add(row)
    row.data = data
    db.commit()
    return data


# ---------- fuentes ----------
@router.get("/sources")
def sources(user: User = Depends(current_user), db: Session = Depends(get_db)):
    search = get_search_config(db, user.id)
    statuses = {s.source: s for s in db.query(SourceStatus).filter_by(user_id=user.id).all()}
    out = []
    for key, conn in CONNECTORS.items():
        info = conn.info(search)
        st = statuses.get(key)
        info.update(enabled=bool(search["sources"].get(key)),
                    last_run_at=st.last_run_at.isoformat() if st and st.last_run_at else None,
                    last_ok_at=st.last_ok_at.isoformat() if st and st.last_ok_at else None,
                    last_status=st.last_status if st else "sin_ejecutar", last_message=st.last_message if st else "",
                    last_count=st.last_count if st else 0)
        out.append(info)
    return out


# ---------- ejecuciones ----------
def run_out(r: Run) -> dict:
    return {"id": r.id, "trigger": r.trigger, "local_date": r.local_date, "status": r.status,
            "started_at": r.started_at.isoformat(), "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "stats": r.stats, "errors": r.errors, "source_logs": r.source_logs}


@router.get("/runs")
def list_runs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [run_out(r) for r in db.query(Run).filter_by(user_id=user.id).order_by(Run.id.desc()).limit(60).all()]


def _bg_run(user_id: int) -> None:
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user:
            run_for_user(db, user, trigger="manual")
    finally:
        db.close()


@router.post("/runs")
def start_run(user: User = Depends(current_user), db: Session = Depends(get_db)):
    running = db.query(Run).filter(Run.user_id == user.id, Run.status == "running",
                                   Run.started_at > utcnow() - timedelta(minutes=30)).first()
    if running:
        raise HTTPException(409, "Ya hay una búsqueda en curso.")
    threading.Thread(target=_bg_run, args=(user.id,), daemon=True).start()
    return {"started": True}


# ---------- informes ----------
@router.get("/reports")
def list_reports(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rs = db.query(Report).filter_by(user_id=user.id).order_by(Report.id.desc()).limit(60).all()
    return [{"id": r.id, "local_date": r.local_date, "subject": r.subject, "sent": r.sent, "sent_to": r.sent_to,
             "send_error": r.send_error, "jobs": len(r.job_ids or []), "created_at": r.created_at.isoformat()} for r in rs]


@router.get("/reports/{rid}", response_class=HTMLResponse)
def view_report(rid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    r = db.query(Report).filter_by(id=rid, user_id=user.id).first()
    if not r:
        raise HTTPException(404, "Informe no encontrado")
    return HTMLResponse(r.html, headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:"})


@router.post("/reports/{rid}/send")
def resend_report(rid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    r = db.query(Report).filter_by(id=rid, user_id=user.id).first()
    if not r:
        raise HTTPException(404, "Informe no encontrado")
    deliver_report(db, user, r)
    if not r.sent:
        raise HTTPException(502, r.send_error)
    return {"sent": True, "to": r.sent_to}


# ---------- ajustes y sistema ----------
@router.get("/settings")
def get_settings(user: User = Depends(current_user)):
    return {**user.get_settings(), "name": user.name, "email": user.email}


@router.put("/settings")
def put_settings(body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = user.get_settings()
    for k, default in DEFAULT_SETTINGS.items():
        if k in body:
            v = body[k]
            s[k] = bool(v) if isinstance(default, bool) else int(v) if isinstance(default, int) else str(v).strip()
    import re

    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", s["run_time"]):
        raise HTTPException(400, "Horario inválido (formato HH:MM).")
    user.settings = s
    if "name" in body:
        user.name = str(body["name"]).strip()[:200]
    db.commit()
    return {**s, "name": user.name, "email": user.email}


@router.get("/system")
def system_status(user: User = Depends(current_user)):
    return {
        "ai": {"configured": ai_available(), "model": config.ANTHROPIC_MODEL if ai_available() else None},
        "smtp": {"configured": smtp_configured()},
        "imap": {"configured": bool(config.IMAP_HOST and config.IMAP_USER and config.IMAP_PASSWORD), "host": config.IMAP_HOST},
        "timezone": config.TIMEZONE, "app_base_url": config.APP_BASE_URL,
        "scheduler": "en el proceso web" if config.RUN_SCHEDULER_IN_WEB else "worker separado (python -m app.worker) o cron externo",
    }


@router.delete("/account/data")
def delete_profile_data(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Elimina perfil, versiones de CV, CVs generados, ofertas e informes (conserva la cuenta)."""
    for model in (GeneratedCV, Job, Report, Run, SourceStatus, CVVersion, ProfileRevision, Profile):
        db.query(model).filter_by(user_id=user.id).delete()
    db.commit()
    delete_user_files(user.id)
    return {"ok": True}


@router.delete("/account")
def delete_account(response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)):
    delete_profile_data(user, db)
    db.query(SearchConfig).filter_by(user_id=user.id).delete()
    db.delete(user)
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}
