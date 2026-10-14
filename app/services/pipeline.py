"""Ejecución diaria: consulta fuentes → deduplica → filtra → puntúa → genera CVs → informe."""
from __future__ import annotations

import logging
import re
import time
import traceback
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlparse
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from .. import config
from ..connectors import CONNECTORS, ConnectorError, NotConfigured, RawJob, SearchQuery
from ..connectors.email_alerts import reset_cache
from ..models import Job, Profile, Run, SearchConfig, SourceStatus, User, utcnow
from . import text as T
from .ai import AIError
from .cvs import generate_cv, job_dict
from .matching import analyze_job, detect_modality, hard_filter, parse_salary, score_job
from .profile import normalize_profile

log = logging.getLogger("jobhunter.pipeline")


def default_search_config() -> dict:
    return {
        "titles": [], "alt_titles": [], "keywords": [], "exclude_keywords": [],
        "locations": ["Buenos Aires", "CABA", "GBA"], "modalities": ["remoto", "hibrido", "presencial"],
        "contract_types": [], "min_salary": None, "salary_currency": "ARS", "max_years_required": None,
        "max_age_days": 3, "min_score": 40,
        "sources": {k: k in ("linkedin", "zonajobs", "computrabajo", "indeed", "jooble", "careerjet", "getonbrd")
                    for k in CONNECTORS},
        "greenhouse_boards": [], "lever_companies": [],
    }


def get_search_config(db: Session, user_id: int) -> dict:
    row = db.query(SearchConfig).filter_by(user_id=user_id).first()
    base = default_search_config()
    if row:
        base.update(row.data or {})
        base["sources"] = {**default_search_config()["sources"], **(row.data or {}).get("sources", {})}
    return base


def local_now(tz: str | None = None) -> datetime:
    return datetime.now(ZoneInfo(tz or config.TIMEZONE))


# ---------------- deduplicación ----------------
_KEEP_PARAMS = {"jk", "vjk", "id", "gh_jid", "currentjobid", "jobid", "job_id", "lever-via"}
_COMPANY_SUFFIX = re.compile(r"\b(s\.?a\.?u?|s\.?r\.?l|s\.?a\.?s|inc|llc|ltd|ltda|sociedad anonima|argentina)\b\.?")


def normalize_url(url: str) -> str:
    try:
        p = urlparse(url.strip())
    except ValueError:
        return url.strip().lower()
    host = (p.netloc or "").lower().removeprefix("www.")
    query = urlencode(sorted((k.lower(), v) for k, v in parse_qsl(p.query) if k.lower() in _KEEP_PARAMS))
    return f"{host}{p.path.rstrip('/')}" + (f"?{query}" if query else "")


def company_title_key(company: str, title: str) -> str:
    c = _COMPANY_SUFFIX.sub("", T.norm(company)).strip()
    t = re.sub(r"\((m/f|h/m|f/m|m/f/d)\)|\s-\s.*$", "", T.norm(title)).strip()
    return f"{c}|{t}" if c else ""


def is_duplicate(db: Session, user_id: int, url_norm: str, ct_key: str) -> bool:
    q = db.query(Job.id).filter(Job.user_id == user_id)
    if q.filter(Job.url_norm == url_norm).first():
        return True
    return bool(ct_key and q.filter(Job.company_title_key == ct_key).first())


# ---------------- procesamiento de una oferta ----------------
def build_job(user_id: int, raw: RawJob, run_id: int | None) -> Job:
    smin, smax, cur = parse_salary(raw.salary_text)
    return Job(
        user_id=user_id, run_id=run_id, source=raw.source, external_id=raw.external_id, url=raw.url,
        url_norm=normalize_url(raw.url), company_title_key=company_title_key(raw.company, raw.title),
        title=raw.title.strip()[:500], company=(raw.company or "").strip()[:300], location=(raw.location or "").strip()[:300],
        modality=raw.modality or detect_modality(raw.title, raw.location, raw.description[:2000]),
        contract_type=(raw.contract_type or "")[:80], salary_text=(raw.salary_text or "")[:200],
        salary_min=smin, salary_max=smax, salary_currency=cur, published_at=raw.published_at,
        description=raw.description or "", description_complete=raw.description_complete,
    )


def evaluate_job(job: Job, profile: dict, search: dict) -> None:
    """Analiza, aplica filtros excluyentes y puntúa. Modifica `job` in-place."""
    analysis = analyze_job(job.title, job.description, profile)
    job.analysis = analysis
    if not job.modality and analysis.get("modality"):
        job.modality = analysis["modality"]
    if not job.contract_type and analysis.get("contract_type"):
        job.contract_type = analysis["contract_type"][:80]
    jd = job_dict(job)
    details = score_job(jd, analysis, profile, search)
    job.score = details["score"]
    job.score_details = details
    reason = hard_filter(jd, analysis, search, profile)
    min_score = search.get("min_score") or 0
    if not reason and job.score < min_score:
        reason = f"Compatibilidad baja ({job.score} < mínimo configurado {min_score})."
    job.discarded = bool(reason)
    job.discard_reason = reason or ""


def _update_source_status(db: Session, user_id: int, source: str, status: str, message: str, count: int) -> None:
    row = db.query(SourceStatus).filter_by(user_id=user_id, source=source).first()
    if not row:
        row = SourceStatus(user_id=user_id, source=source)
        db.add(row)
    row.last_run_at = utcnow()
    row.last_status = status
    row.last_message = message[:2000]
    row.last_count = count
    if status == "ok":
        row.last_ok_at = row.last_run_at


def run_for_user(db: Session, user: User, trigger: str = "manual", send_email: bool = True) -> Run:
    from .report import build_and_send_report  # import tardío (evita ciclo)

    settings = user.get_settings()
    run = Run(user_id=user.id, trigger=trigger, local_date=local_now().strftime("%Y-%m-%d"), status="running",
              stats={}, errors=[], source_logs=[])
    db.add(run)
    db.commit()
    stats = {"fetched": 0, "new": 0, "duplicates": 0, "discarded": 0, "relevant": 0, "cvs_generated": 0,
             "sources_ok": 0, "sources_failed": 0, "sources_skipped": 0}
    errors: list[str] = []
    source_logs: list[dict] = []
    try:
        profile_row = db.query(Profile).filter_by(user_id=user.id).first()
        if not profile_row or not profile_row.data:
            raise RuntimeError("No hay perfil profesional cargado: subí tu CV antes de ejecutar la búsqueda.")
        profile = normalize_profile(profile_row.data)
        search = get_search_config(db, user.id)
        terms = list(dict.fromkeys(search.get("titles", []) + search.get("alt_titles", []) + search.get("keywords", [])))
        if not terms:
            raise RuntimeError("Configurá al menos un puesto o palabra clave en Configuración de búsqueda.")

        # Las ofertas 'nueva' de ejecuciones anteriores pasan a 'pendiente de revisar'.
        db.query(Job).filter(Job.user_id == user.id, Job.status == "nueva").update({"status": "pendiente"})

        query = SearchQuery(terms=terms, location="Buenos Aires", max_age_days=int(search.get("max_age_days") or 3),
                            modalities=search.get("modalities") or [], options=search)
        reset_cache()
        raw_jobs: list[RawJob] = []
        for key, conn in CONNECTORS.items():
            if not search["sources"].get(key):
                continue
            t0 = time.monotonic()
            try:
                found = [j for j in conn.fetch(query) if j.url and j.title]
                raw_jobs.extend(found)
                entry = {"source": key, "status": "ok", "count": len(found), "message": f"{len(found)} avisos obtenidos"}
                stats["sources_ok"] += 1
            except NotConfigured as exc:
                entry = {"source": key, "status": "no_configurada", "count": 0, "message": str(exc)}
                stats["sources_skipped"] += 1
            except ConnectorError as exc:
                entry = {"source": key, "status": "error", "count": 0, "message": str(exc)}
                errors.append(f"{conn.name}: {exc}")
                stats["sources_failed"] += 1
            except Exception as exc:  # noqa: BLE001 - un conector roto no debe frenar la ejecución
                log.exception("Conector %s falló", key)
                entry = {"source": key, "status": "error", "count": 0, "message": f"Error inesperado: {exc}"}
                errors.append(f"{conn.name}: error inesperado {exc}")
                stats["sources_failed"] += 1
            entry["seconds"] = round(time.monotonic() - t0, 2)
            source_logs.append(entry)
            _update_source_status(db, user.id, key, entry["status"], entry["message"], entry["count"])
        db.commit()
        stats["fetched"] = len(raw_jobs)

        seen_urls, seen_keys = set(), set()
        new_jobs: list[Job] = []
        for raw in raw_jobs:
            job = build_job(user.id, raw, run.id)
            if job.url_norm in seen_urls or (job.company_title_key and job.company_title_key in seen_keys) \
                    or is_duplicate(db, user.id, job.url_norm, job.company_title_key):
                stats["duplicates"] += 1
                continue
            seen_urls.add(job.url_norm)
            if job.company_title_key:
                seen_keys.add(job.company_title_key)
            try:
                evaluate_job(job, profile, search)
            except AIError as exc:
                errors.append(f"Análisis de «{job.title}»: {exc}")
            db.add(job)
            new_jobs.append(job)
            db.commit()
        stats["new"] = len(new_jobs)
        stats["discarded"] = sum(1 for j in new_jobs if j.discarded)
        relevant = sorted([j for j in new_jobs if not j.discarded], key=lambda j: -(j.score or 0))
        stats["relevant"] = len(relevant)

        min_cv = settings.get("auto_cv_min_score", 70)
        for job in relevant[: int(settings.get("auto_cv_max_per_run", 5))]:
            if (job.score or 0) >= min_cv:
                try:
                    generate_cv(db, user, job)
                    stats["cvs_generated"] += 1
                    db.commit()
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    errors.append(f"CV para «{job.title}»: {exc}")

        run.status = "ok" if not errors else ("parcial" if stats["sources_ok"] else "error")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        log.error("Ejecución fallida: %s\n%s", exc, traceback.format_exc())
        errors.append(str(exc))
        run.status = "error"
    run.stats = stats
    run.errors = errors
    run.source_logs = source_logs
    run.finished_at = utcnow()
    db.commit()
    try:
        build_and_send_report(db, user, run, send=send_email and settings.get("report_enabled", True))
    except Exception as exc:  # noqa: BLE001
        run.errors = errors + [f"Informe: {exc}"]
        db.commit()
    return run
