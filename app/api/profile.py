from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import CVVersion, Profile, ProfileRevision, User
from ..security import current_user, delete_file, read_encrypted, store_encrypted
from ..services.profile import CVReadError, analyze_cv, extract_text, normalize_profile, profile_years

router = APIRouter(prefix="/api/profile", tags=["perfil"])
MAX_UPLOAD = 8 * 1024 * 1024


def _version_out(v: CVVersion, active_id: int | None) -> dict:
    return {"id": v.id, "filename": v.filename, "created_at": v.created_at.isoformat(), "parse_method": v.parse_method,
            "warnings": v.parse_warnings, "active": v.id == active_id, "chars": len(v.text or "")}


def _save_profile(db: Session, user: User, data: dict, reason: str, cv_version_id: int | None = None) -> Profile:
    data = normalize_profile(data)
    row = db.query(Profile).filter_by(user_id=user.id).first()
    if not row:
        row = Profile(user_id=user.id, data=data)
        db.add(row)
    row.data = data
    if cv_version_id is not None:
        row.source_cv_version_id = cv_version_id
    db.add(ProfileRevision(user_id=user.id, data=data, reason=reason))
    db.commit()
    return row


@router.get("")
def get_profile(user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.query(Profile).filter_by(user_id=user.id).first()
    if not row:
        return {"profile": None}
    return {"profile": normalize_profile(row.data), "updated_at": row.updated_at.isoformat(),
            "years_experience": profile_years(row.data), "source_cv_version_id": row.source_cv_version_id}


@router.put("")
def update_profile(body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = _save_profile(db, user, body.get("profile") or {}, "Edición manual")
    return {"profile": row.data, "years_experience": profile_years(row.data)}


@router.post("/cv")
async def upload_cv(file: UploadFile = File(...), replace_profile: bool = True,
                    user: User = Depends(current_user), db: Session = Depends(get_db)):
    content = await file.read()
    if len(content) > MAX_UPLOAD:
        raise HTTPException(400, "El archivo supera los 8 MB.")
    try:
        text, mime = extract_text(file.filename or "cv", content)
    except CVReadError as exc:
        raise HTTPException(400, str(exc))
    profile, method, warnings = analyze_cv(text)
    version = CVVersion(user_id=user.id, filename=file.filename or "cv", mime=mime,
                        stored_path=store_encrypted(user.id, file.filename or "cv", content), text=text,
                        parsed=profile, parse_method=method, parse_warnings=warnings)
    db.add(version)
    db.commit()
    existing = db.query(Profile).filter_by(user_id=user.id).first()
    if replace_profile or not existing:
        _save_profile(db, user, profile, f"Análisis de {version.filename}", version.id)
    return {"version": _version_out(version, version.id if (replace_profile or not existing) else None),
            "profile": profile, "method": method, "warnings": warnings}


@router.get("/versions")
def versions(user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.query(Profile).filter_by(user_id=user.id).first()
    active = row.source_cv_version_id if row else None
    vs = db.query(CVVersion).filter_by(user_id=user.id).order_by(CVVersion.id.desc()).all()
    return [_version_out(v, active) for v in vs]


@router.get("/versions/{vid}/download")
def download_version(vid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    v = db.query(CVVersion).filter_by(id=vid, user_id=user.id).first()
    if not v:
        raise HTTPException(404, "Versión no encontrada")
    return Response(read_encrypted(v.stored_path), media_type=v.mime,
                    headers={"Content-Disposition": f'attachment; filename="{v.filename}"'})


@router.post("/versions/{vid}/apply")
def apply_version(vid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    v = db.query(CVVersion).filter_by(id=vid, user_id=user.id).first()
    if not v:
        raise HTTPException(404, "Versión no encontrada")
    row = _save_profile(db, user, v.parsed, f"Restaurado desde {v.filename}", v.id)
    return {"profile": row.data}


@router.delete("/versions/{vid}")
def delete_version(vid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    v = db.query(CVVersion).filter_by(id=vid, user_id=user.id).first()
    if not v:
        raise HTTPException(404, "Versión no encontrada")
    delete_file(v.stored_path)
    db.delete(v)
    db.commit()
    return {"ok": True}


@router.get("/revisions")
def revisions(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rs = db.query(ProfileRevision).filter_by(user_id=user.id).order_by(ProfileRevision.id.desc()).limit(50).all()
    return [{"id": r.id, "reason": r.reason, "created_at": r.created_at.isoformat()} for r in rs]


@router.post("/revisions/{rid}/restore")
def restore_revision(rid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    r = db.query(ProfileRevision).filter_by(id=rid, user_id=user.id).first()
    if not r:
        raise HTTPException(404, "Revisión no encontrada")
    row = _save_profile(db, user, r.data, f"Restaurada revisión del {r.created_at:%d/%m/%Y %H:%M}")
    return {"profile": row.data}
