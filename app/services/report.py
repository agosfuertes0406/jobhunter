"""Informe diario (HTML apto para email) y envío por SMTP."""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from html import escape

from sqlalchemy.orm import Session

from .. import config
from ..connectors import CONNECTORS
from ..models import GeneratedCV, Job, Report, Run, User
from ..security import read_encrypted

DOCX_MIME = ("application", "vnd.openxmlformats-officedocument.wordprocessingml.document")


def smtp_configured() -> bool:
    return bool(config.SMTP_HOST and config.SMTP_FROM)


def _job_block(job: Job, cv: GeneratedCV | None) -> str:
    d = job.score_details or {}
    reasons = d.get("strengths") or []
    missing = [r.get("text", "") for r in (d.get("requirements_missing") or []) if r.get("type") != "deseable"][:4]
    if not missing:
        missing = [f"Herramienta: {s}" for s in (d.get("missing_skills") or [])[:4]]
    meta = " · ".join(x for x in (job.location, job.modality.capitalize() if job.modality else "",
                                   job.published_at.strftime("%d/%m/%Y") if job.published_at else "Fecha no informada") if x)
    cv_html = ""
    if cv:
        cv_html = (f'<p style="margin:6px 0 0"><a href="{config.APP_BASE_URL}/api/cvs/{cv.id}/docx">Descargar CV adaptado (DOCX)</a>'
                   f' · <a href="{config.APP_BASE_URL}/api/cvs/{cv.id}/pdf">PDF</a> · <a href="{config.APP_BASE_URL}/#/cv/{cv.id}">Editar</a></p>')
    partial = ('<p style="margin:4px 0;color:#8a5a00;font-size:12px">Análisis parcial: el aviso no incluye la descripción completa.</p>'
               if d.get("partial_analysis") else "")
    return f"""
<tr><td style="padding:14px 0;border-bottom:1px solid #e5e7eb">
  <div style="font-size:16px;font-weight:600;color:#111827">{escape(job.title)}
    <span style="float:right;background:#eef2ff;color:#3730a3;border-radius:12px;padding:2px 10px;font-size:13px">{int(job.score or 0)}/100</span></div>
  <div style="color:#374151;margin-top:2px">{escape(job.company or 'Empresa no informada')}</div>
  <div style="color:#6b7280;font-size:13px">{escape(meta)}{(' · Salario: ' + escape(job.salary_text)) if job.salary_text else ''} · Fuente: {escape(CONNECTORS[job.source].name if job.source in CONNECTORS else job.source)}</div>
  {partial}
  <p style="margin:8px 0 2px;font-size:13px"><b>Por qué encaja:</b></p>
  <ul style="margin:0;padding-left:18px;font-size:13px;color:#374151">{''.join(f'<li>{escape(r)}</li>' for r in reasons) or '<li>Sin fortalezas destacadas.</li>'}</ul>
  <p style="margin:8px 0 2px;font-size:13px"><b>Requisitos faltantes relevantes:</b></p>
  <ul style="margin:0;padding-left:18px;font-size:13px;color:#374151">{''.join(f'<li>{escape(m)}</li>' for m in missing) or '<li>No se detectaron faltantes relevantes.</li>'}</ul>
  <p style="margin:8px 0 0"><a href="{escape(job.url)}">Ver aviso original</a> · <a href="{config.APP_BASE_URL}/#/job/{job.id}">Ver en JobHunter</a></p>
  {cv_html}
</td></tr>"""


def build_report_html(db: Session, user: User, run: Run, jobs: list[Job]) -> str:
    s = run.stats or {}
    failed = [l for l in (run.source_logs or []) if l.get("status") == "error"]
    skipped = [l for l in (run.source_logs or []) if l.get("status") == "no_configurada"]
    blocks = []
    for job in jobs:
        cv = db.query(GeneratedCV).filter_by(job_id=job.id).order_by(GeneratedCV.id.desc()).first()
        blocks.append(_job_block(job, cv))
    src_html = ""
    if failed or skipped:
        items = [f"<li><b>{escape(CONNECTORS[l['source']].name)}</b>: {escape(l['message'])}</li>" for l in failed + skipped if l["source"] in CONNECTORS]
        src_html = f'<h3 style="font-size:14px;margin:18px 0 4px">Fuentes que no pudieron consultarse</h3><ul style="font-size:13px;color:#6b7280">{"".join(items)}</ul>'
    return f"""<!doctype html><html><body style="font-family:Arial,Helvetica,sans-serif;background:#f9fafb;margin:0;padding:16px">
<table role="presentation" width="100%" style="max-width:680px;margin:auto;background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:20px">
<tr><td>
  <h1 style="font-size:20px;margin:0">JobHunter AI · Informe del {run.local_date}</h1>
  <p style="color:#4b5563;font-size:14px">Hola {escape(user.name or '')}: se analizaron <b>{s.get('fetched', 0)}</b> avisos,
  <b>{s.get('new', 0)}</b> nuevos ({s.get('duplicates', 0)} duplicados omitidos), <b>{s.get('relevant', 0)}</b> compatibles con tus criterios
  y <b>{s.get('discarded', 0)}</b> descartados. Se generaron <b>{s.get('cvs_generated', 0)}</b> CV adaptados.</p>
</td></tr>
{''.join(blocks) or '<tr><td><p>No hubo ofertas nuevas compatibles hoy.</p></td></tr>'}
<tr><td>{src_html}
  <p style="color:#9ca3af;font-size:11px;margin-top:18px">La puntuación de compatibilidad es una estimación orientativa basada en criterios explícitos;
  no predice el resultado de un ATS ni de un proceso de selección. JobHunter AI no se postula ni envía tus datos a empresas.</p>
</td></tr></table></body></html>"""


def send_email(to: str, subject: str, html: str, attachments: list[tuple[str, bytes]]) -> None:
    if not smtp_configured():
        raise RuntimeError("SMTP no configurado (definí SMTP_HOST, SMTP_USER, SMTP_PASSWORD y SMTP_FROM).")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.SMTP_FROM
    msg["To"] = to
    msg.set_content("Tu cliente de correo no muestra HTML. Abrí JobHunter AI para ver el informe: " + config.APP_BASE_URL)
    msg.add_alternative(html, subtype="html")
    for name, data in attachments:
        msg.add_attachment(data, maintype=DOCX_MIME[0], subtype=DOCX_MIME[1], filename=name)
    ctx = ssl.create_default_context()
    if config.SMTP_SSL:
        server = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, context=ctx, timeout=30)
    else:
        server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30)
        if config.SMTP_STARTTLS:
            server.starttls(context=ctx)
    try:
        if config.SMTP_USER and config.SMTP_PASSWORD:
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.send_message(msg)
    finally:
        server.quit()


def deliver_report(db: Session, user: User, report: Report) -> None:
    settings = user.get_settings()
    to = settings.get("report_email") or user.email
    attachments = []
    if settings.get("attach_cvs", True):
        for jid in report.job_ids[:5]:
            cv = db.query(GeneratedCV).filter_by(job_id=jid).order_by(GeneratedCV.id.desc()).first()
            if cv and cv.docx_path:
                try:
                    attachments.append((cv.filename_base + ".docx", read_encrypted(cv.docx_path)))
                except Exception:  # noqa: BLE001 - un adjunto ilegible no debe frenar el envío
                    pass
    try:
        send_email(to, report.subject, report.html, attachments)
        report.sent, report.sent_to, report.send_error = True, to, ""
    except Exception as exc:  # noqa: BLE001
        report.sent, report.send_error = False, str(exc)
    db.commit()


def build_and_send_report(db: Session, user: User, run: Run, send: bool = True) -> Report:
    settings = user.get_settings()
    jobs = (db.query(Job).filter(Job.user_id == user.id, Job.run_id == run.id, Job.discarded.is_(False))
            .order_by(Job.score.desc()).limit(int(settings.get("report_max_jobs", 15))).all())
    html = build_report_html(db, user, run, jobs)
    subject = f"JobHunter AI · {len(jobs)} ofertas compatibles · {run.local_date}"
    report = Report(user_id=user.id, run_id=run.id, local_date=run.local_date, subject=subject, html=html,
                    job_ids=[j.id for j in jobs])
    db.add(report)
    db.commit()
    if send:
        deliver_report(db, user, report)
    else:
        report.send_error = "Envío por email desactivado en Ajustes."
        db.commit()
    return report
