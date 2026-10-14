from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..connectors import CONNECTORS, RawJob
from ..db import get_db
from ..models import JOB_STATUSES, GeneratedCV, Job, Profile, User, utcnow
from ..security import current_user
from ..services.ai import AIError
from ..services.cvs import generate_cv
from ..services.matching import parse_salary
from ..services.pipeline import build_job, evaluate_job, get_search_config, is_duplicate, local_now
from ..services.profile import normalize_profile

router = APIRouter(prefix="/api/jobs", tags=["ofertas"])


def job_out(j: Job, full: bool = False) -> dict:
    last_cv = max(j.cvs, key=lambda c: c.id) if j.cvs else None
    out = {
        "id": j.id, "source": j.source, "source_name": CONNECTORS[j.source].name if j.source in CONNECTORS else j.source,
        "title": j.title, "company": j.company, "location": j.location, "modality": j.modality,
        "contract_type": j.contract_type, "salary_text": j.salary_text, "url": j.url,
        "published_at": j.published_at.isoformat() if j.published_at else None,
        "created_at": j.created_at.isoformat(), "score": j.score, "status": j.status,
        "status_label": JOB_STATUSES.get(j.status, j.status), "discarded": j.discarded,
        "discard_reason": j.discard_reason, "saved": j.saved, "description_complete": j.description_complete,
        "is_test_data": j.is_test_data, "cv_id": last_cv.id if last_cv else None, "applied_at": j.applied_at,
        "analysis_method": (j.score_details or {}).get("method", ""),
        "partial_analysis": (j.score_details or {}).get("partial_analysis", False),
    }
    if full:
        out.update(description=j.description, analysis=j.analysis, score_details=j.score_details, notes=j.notes,
                   observations=j.observations,
                   cvs=[{"id": c.id, "filename_base": c.filename_base, "created_at": c.created_at.isoformat(),
                         "method": c.method, "edited": c.edited} for c in sorted(j.cvs, key=lambda c: -c.id)])
    return out


def _get_job(db: Session, user: User, job_id: int) -> Job:
    job = db.query(Job).filter_by(id=job_id, user_id=user.id).first()
    if not job:
        raise HTTPException(404, "Oferta no encontrada")
    return job


@router.get("")
def list_jobs(view: str = "activas", q: str = "", company: str = "", title: str = "", modality: str = "",
              status: str = "", source: str = "", min_score: float | None = None, date_from: str = "",
              date_to: str = "", sort: str = "score", limit: int = 200, offset: int = 0,
              user: User = Depends(current_user), db: Session = Depends(get_db)):
    qry = db.query(Job).filter(Job.user_id == user.id)
    if view == "descartadas":
        qry = qry.filter(Job.discarded.is_(True))
    elif view == "guardadas":
        qry = qry.filter(Job.saved.is_(True))
    elif view == "hoy":
        start = utcnow() - timedelta(hours=24)
        qry = qry.filter(Job.discarded.is_(False), Job.created_at >= start)
    elif view == "pendientes":
        qry = qry.filter(Job.discarded.is_(False), Job.status.in_(["nueva", "pendiente"]))
    elif view == "todas":
        pass
    else:
        qry = qry.filter(Job.discarded.is_(False))
    if q:
        like = f"%{q}%"
        qry = qry.filter(or_(Job.title.ilike(like), Job.company.ilike(like), Job.description.ilike(like)))
    if company:
        qry = qry.filter(Job.company.ilike(f"%{company}%"))
    if title:
        qry = qry.filter(Job.title.ilike(f"%{title}%"))
    if modality:
        qry = qry.filter(Job.modality == modality)
    if status:
        qry = qry.filter(Job.status == status)
    if source:
        qry = qry.filter(Job.source == source)
    if min_score is not None:
        qry = qry.filter(Job.score >= min_score)
    if date_from:
        qry = qry.filter(func.coalesce(Job.published_at, Job.created_at) >= datetime.fromisoformat(date_from))
    if date_to:
        qry = qry.filter(func.coalesce(Job.published_at, Job.created_at) < datetime.fromisoformat(date_to) + timedelta(days=1))
    total = qry.count()
    order = {"score": [Job.score.desc().nullslast(), Job.id.desc()],
             "fecha": [func.coalesce(Job.published_at, Job.created_at).desc()],
             "recientes": [Job.id.desc()]}.get(sort, [Job.id.desc()])
    jobs = qry.order_by(*order).offset(offset).limit(min(limit, 500)).all()
    return {"total": total, "items": [job_out(j) for j in jobs]}


@router.get("/stats")
def stats(user: User = Depends(current_user), db: Session = Depends(get_db)):
    base = db.query(Job).filter(Job.user_id == user.id)
    today_local = local_now().strftime("%Y-%m-%d")
    since = utcnow() - timedelta(hours=24)
    by_status = dict(base.filter(Job.discarded.is_(False)).with_entities(Job.status, func.count()).group_by(Job.status).all())
    reasons = base.filter(Job.discarded.is_(True)).with_entities(Job.discard_reason).order_by(Job.id.desc()).limit(300).all()
    reason_groups: dict[str, int] = {}
    for (r,) in reasons:
        key = (r or "Sin motivo").split(":")[0].split("(")[0].strip()[:60]
        reason_groups[key] = reason_groups.get(key, 0) + 1
    return {
        "today": today_local,
        "new_today": base.filter(Job.discarded.is_(False), Job.created_at >= since).count(),
        "pending": base.filter(Job.discarded.is_(False), Job.status.in_(["nueva", "pendiente"])).count(),
        "saved": base.filter(Job.saved.is_(True)).count(),
        "discarded": base.filter(Job.discarded.is_(True)).count(),
        "cvs": db.query(GeneratedCV).filter_by(user_id=user.id).count(),
        "by_status": {k: by_status.get(k, 0) for k in JOB_STATUSES},
        "discard_reasons": sorted(reason_groups.items(), key=lambda x: -x[1])[:8],
        "companies": [c for (c,) in base.with_entities(Job.company).distinct().order_by(Job.company).limit(300) if c],
    }


@router.get("/{job_id}")
def get_job(job_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return job_out(_get_job(db, user, job_id), full=True)


class JobPatch(BaseModel):
    status: str | None = None
    saved: bool | None = None
    notes: str | None = None
    observations: str | None = None
    applied_at: str | None = None
    discarded: bool | None = None
    discard_reason: str | None = None


@router.patch("/{job_id}")
def patch_job(job_id: int, body: JobPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = _get_job(db, user, job_id)
    data = body.model_dump(exclude_unset=True)
    if "status" in data:
        if data["status"] not in JOB_STATUSES:
            raise HTTPException(400, "Estado inválido")
        job.status = data["status"]
        if job.status == "postulada" and not job.applied_at and "applied_at" not in data:
            job.applied_at = local_now().strftime("%Y-%m-%d")
    for k in ("saved", "notes", "observations", "applied_at"):
        if k in data:
            setattr(job, k, data[k])
    if "discarded" in data:
        job.discarded = bool(data["discarded"])
        job.discard_reason = (data.get("discard_reason") or "Descartada manualmente") if job.discarded else ""
    db.commit()
    return job_out(job, full=True)


class DescriptionIn(BaseModel):
    description: str


def _reanalyze(db: Session, user: User, job: Job) -> None:
    profile_row = db.query(Profile).filter_by(user_id=user.id).first()
    if not profile_row:
        raise HTTPException(400, "Primero cargá tu CV.")
    was_discarded, old_reason = job.discarded, job.discard_reason
    try:
        evaluate_job(job, normalize_profile(profile_row.data), get_search_config(db, user.id))
    except AIError as exc:
        raise HTTPException(502, str(exc))
    if was_discarded and old_reason.startswith("Descartada manualmente"):
        job.discarded, job.discard_reason = True, old_reason


@router.post("/{job_id}/description")
def set_description(job_id: int, body: DescriptionIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = _get_job(db, user, job_id)
    if len(body.description.strip()) < 30:
        raise HTTPException(400, "Pegá la descripción completa del aviso.")
    job.description = body.description.strip()
    job.description_complete = True
    _reanalyze(db, user, job)
    db.commit()
    return job_out(job, full=True)


@router.post("/{job_id}/reanalyze")
def reanalyze(job_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = _get_job(db, user, job_id)
    _reanalyze(db, user, job)
    db.commit()
    return job_out(job, full=True)


class ManualJob(BaseModel):
    url: str
    title: str
    company: str = ""
    location: str = ""
    modality: str = ""
    salary_text: str = ""
    published_at: str = ""
    description: str = ""


@router.post("/manual")
def add_manual(body: ManualJob, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not body.url.startswith(("http://", "https://")) or not body.title.strip():
        raise HTTPException(400, "Indicá el link original (http/https) y el título del puesto.")
    raw = RawJob(source="manual", title=body.title, url=body.url, company=body.company, location=body.location,
                 modality=body.modality, salary_text=body.salary_text,
                 published_at=datetime.fromisoformat(body.published_at) if body.published_at else None,
                 description=body.description, description_complete=len(body.description) > 200)
    job = build_job(user.id, raw, None)
    if is_duplicate(db, user.id, job.url_norm, job.company_title_key):
        raise HTTPException(409, "Esa oferta ya está cargada (misma URL o misma empresa y puesto).")
    _reanalyze(db, user, job)
    db.add(job)
    db.commit()
    return job_out(job, full=True)


@router.post("/{job_id}/cv")
def create_cv(job_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = _get_job(db, user, job_id)
    try:
        cv = generate_cv(db, user, job)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return {"cv_id": cv.id, "job": job_out(job, full=True)}


@router.delete("/{job_id}")
def delete_job(job_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..security import delete_file

    job = _get_job(db, user, job_id)
    for cv in job.cvs:
        delete_file(cv.docx_path)
        delete_file(cv.pdf_path)
    db.delete(job)
    db.commit()
    return {"ok": True}
