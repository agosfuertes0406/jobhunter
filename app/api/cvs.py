from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import GeneratedCV, Job, User
from ..security import current_user, delete_file, read_encrypted
from ..services.cvs import write_files

router = APIRouter(prefix="/api/cvs", tags=["cvs"])


def _get(db: Session, user: User, cv_id: int) -> GeneratedCV:
    cv = db.query(GeneratedCV).filter_by(id=cv_id, user_id=user.id).first()
    if not cv:
        raise HTTPException(404, "CV no encontrado")
    return cv


def cv_out(cv: GeneratedCV, full: bool = False) -> dict:
    out = {"id": cv.id, "job_id": cv.job_id, "filename_base": cv.filename_base, "method": cv.method,
           "edited": cv.edited, "created_at": cv.created_at.isoformat(), "updated_at": cv.updated_at.isoformat(),
           "job_title": cv.job.title if cv.job else "", "company": cv.job.company if cv.job else "",
           "job_status": cv.job.status if cv.job else "", "score": cv.job.score if cv.job else None}
    if full:
        out.update(content=cv.content, analysis=cv.analysis, job_url=cv.job.url if cv.job else "")
    return out


@router.get("")
def list_cvs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    cvs = db.query(GeneratedCV).filter_by(user_id=user.id).order_by(GeneratedCV.id.desc()).limit(500).all()
    return [cv_out(c) for c in cvs]


@router.get("/{cv_id}")
def get_cv(cv_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return cv_out(_get(db, user, cv_id), full=True)


@router.put("/{cv_id}")
def update_cv(cv_id: int, body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Guarda la edición del usuario y regenera DOCX y PDF."""
    cv = _get(db, user, cv_id)
    content = body.get("content")
    if not isinstance(content, dict) or not isinstance(content.get("experience", []), list):
        raise HTTPException(400, "Contenido inválido")
    allowed = {"full_name", "headline", "contact", "summary", "skills", "soft_skills", "experience", "education",
               "courses", "languages"}
    cv.content = {k: v for k, v in content.items() if k in allowed}
    if body.get("filename_base"):
        cv.filename_base = "".join(c for c in body["filename_base"] if c.isalnum() or c in "_-")[:150] or cv.filename_base
    cv.edited = True
    write_files(cv)
    db.commit()
    return cv_out(cv, full=True)


def _download(cv: GeneratedCV, kind: str) -> Response:
    path = cv.docx_path if kind == "docx" else cv.pdf_path
    mime = ("application/vnd.openxmlformats-officedocument.wordprocessingml.document" if kind == "docx" else "application/pdf")
    name = f"{cv.filename_base}.{kind}"
    return Response(read_encrypted(path), media_type=mime,
                    headers={"Content-Disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{quote(name)}"})


@router.get("/{cv_id}/docx")
def download_docx(cv_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _download(_get(db, user, cv_id), "docx")


@router.get("/{cv_id}/pdf")
def download_pdf(cv_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _download(_get(db, user, cv_id), "pdf")


@router.delete("/{cv_id}")
def delete_cv(cv_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    cv = _get(db, user, cv_id)
    delete_file(cv.docx_path)
    delete_file(cv.pdf_path)
    job = db.get(Job, cv.job_id)
    db.delete(cv)
    db.flush()
    if job and job.status == "cv_generado" and not db.query(GeneratedCV).filter_by(job_id=job.id).count():
        job.status = "pendiente"
    db.commit()
    return {"ok": True}
