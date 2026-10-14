"""Persistencia de CVs generados (archivos DOCX/PDF cifrados en disco)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import GeneratedCV, Job, Profile, User
from ..security import delete_file, store_encrypted
from .cv_tailor import filename_base, tailor_cv
from .documents import render_docx, render_pdf
from .profile import normalize_profile


def job_dict(job: Job) -> dict:
    return {
        "title": job.title, "company": job.company, "location": job.location, "modality": job.modality,
        "contract_type": job.contract_type, "description": job.description, "salary_text": job.salary_text,
        "salary_max": job.salary_max, "salary_currency": job.salary_currency, "published_at": job.published_at,
        "description_complete": job.description_complete,
    }


def write_files(cv: GeneratedCV) -> None:
    delete_file(cv.docx_path)
    delete_file(cv.pdf_path)
    cv.docx_path = store_encrypted(cv.user_id, cv.filename_base + ".docx", render_docx(cv.content))
    cv.pdf_path = store_encrypted(cv.user_id, cv.filename_base + ".pdf", render_pdf(cv.content))


def generate_cv(db: Session, user: User, job: Job) -> GeneratedCV:
    profile_row = db.query(Profile).filter_by(user_id=user.id).first()
    if not profile_row or not profile_row.data.get("experience") and not profile_row.data.get("full_name"):
        raise ValueError("Primero cargá tu CV y revisá tu perfil maestro.")
    profile = normalize_profile(profile_row.data)
    content, analysis, method = tailor_cv(profile, job_dict(job), job.analysis or {})
    analysis["score_at_generation"] = job.score
    cv = GeneratedCV(user_id=user.id, job_id=job.id, filename_base=filename_base(profile, job_dict(job)),
                     content=content, analysis=analysis, method=method)
    write_files(cv)
    db.add(cv)
    if job.status in ("nueva", "pendiente"):
        job.status = "cv_generado"
    db.flush()
    return cv
