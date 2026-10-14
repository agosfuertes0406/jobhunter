"""Flujo completo vía API: registro → CV → configuración → ejecución diaria → CV adaptado → informe.
Las respuestas de las APIs externas se simulan con DATOS FICTICIOS en el formato documentado de cada API
(este entorno de pruebas no tiene acceso de red a los portales)."""
import time
from datetime import datetime, timezone

import pytest

from app import config
from app.connectors import apis

NOW = datetime.now(timezone.utc)

GETONBRD_FAKE = {"data": [
    {"id": "analista-rrhh-ficticia", "type": "job", "links": {"public_url": "https://www.getonbrd.com/jobs/analista-rrhh-ficticia"},
     "attributes": {
         "title": "Analista de Recursos Humanos", "remote": False, "remote_modality": "hybrid", "countries": ["Argentina"],
         "description": "<p>Empresa de prueba busca Analista de RR.HH. en CABA.</p>",
         "functions": "<ul><li>Gestionar reclutamiento y selección.</li><li>Coordinar onboarding.</li><li>Reportes en Power BI y Excel avanzado.</li></ul>",
         "desirable": "<p>Deseable: Workday.</p>",
         "benefits": "", "min_salary": None, "max_salary": None, "published_at": int(NOW.timestamp()),
         "company": {"data": {"attributes": {"name": "Empresa Ficticia Tres"}}}}},
    {"id": "dev-ficticio", "type": "job", "links": {"public_url": "https://www.getonbrd.com/jobs/dev-ficticio"},
     "attributes": {"title": "Senior Backend Engineer (Go)", "remote": True, "remote_modality": "fully_remote", "countries": ["Argentina"],
                    "description": "<p>8+ years of experience with Go, Kubernetes and AWS.</p>", "functions": "", "desirable": "", "benefits": "",
                    "published_at": int(NOW.timestamp()), "company": {"data": {"attributes": {"name": "Tech Ficticia"}}}}},
]}
GREENHOUSE_FAKE = {"jobs": [
    {"id": 1, "title": "Analista de Recursos Humanos", "absolute_url": "https://boards.greenhouse.io/ficticia/jobs/1?gh_jid=1",
     "location": {"name": "Buenos Aires, Argentina"}, "updated_at": NOW.isoformat(), "company_name": "Empresa Ficticia Tres",
     "content": "&lt;p&gt;Duplicado del aviso anterior&lt;/p&gt;"},
    {"id": 2, "title": "HR Business Partner", "absolute_url": "https://boards.greenhouse.io/ficticia/jobs/2?gh_jid=2",
     "location": {"name": "Córdoba, Argentina"}, "updated_at": NOW.isoformat(), "company_name": "Empresa Ficticia Cuatro",
     "content": "&lt;p&gt;Presencial en Córdoba. Requisitos: 5 años en RR.HH.&lt;/p&gt;"},
]}


@pytest.fixture
def fake_apis(monkeypatch):
    def fake_request(method, url, **kw):
        if "getonbrd" in url:
            return GETONBRD_FAKE
        if "greenhouse" in url:
            return GREENHOUSE_FAKE
        raise AssertionError(url)

    monkeypatch.setattr(apis, "request_json", fake_request)


@pytest.fixture
def fake_smtp(monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def starttls(self, **k): pass
        def login(self, *a): pass
        def send_message(self, msg): sent.append(msg)
        def quit(self): pass

    import smtplib
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(config, "SMTP_FROM", "jobhunter@example.com")
    return sent


def test_full_flow(client, cv_docx, fake_apis, fake_smtp):
    # Sin sesión y sin cabecera anti-CSRF
    assert client.get("/api/jobs/stats").status_code in (401, 200)
    r = client.post("/api/auth/register", json={"email": "prueba@example.com", "password": "clave-segura-1", "name": "Lucía"})
    assert r.status_code == 200, r.text
    assert client.post("/api/search-config", json={}).status_code in (404, 405)
    bad = client.put("/api/settings", json={"run_time": "07:30"}, headers={"X-Requested-With": ""})
    assert bad.status_code == 403  # protección CSRF

    # Carga de CV
    r = client.post("/api/profile/cv", files={"file": ("CV_Lucia.docx", cv_docx, "application/octet-stream")})
    assert r.status_code == 200, r.text
    assert r.json()["method"] == "heuristico" and len(r.json()["profile"]["experience"]) == 2
    prof = client.get("/api/profile").json()
    assert prof["years_experience"] >= 5
    # Edición del perfil + historial
    p = prof["profile"]
    p["headline"] = "Analista de RR.HH. y Selección"
    assert client.put("/api/profile", json={"profile": p}).status_code == 200
    assert len(client.get("/api/profile/revisions").json()) == 2
    vs = client.get("/api/profile/versions").json()
    assert vs[0]["active"] and client.get(f"/api/profile/versions/{vs[0]['id']}/download").content == cv_docx
    assert client.post(f"/api/profile/versions/{vs[0]['id']}/apply").json()["profile"]["headline"] != "Analista de RR.HH. y Selección"
    rev = client.get("/api/profile/revisions").json()[1]  # la edición manual
    assert client.post(f"/api/profile/revisions/{rev['id']}/restore").json()["profile"]["headline"] == "Analista de RR.HH. y Selección"

    # Configuración: solo fuentes con respuesta simulada
    r = client.put("/api/search-config", json={
        "titles": ["Analista de Recursos Humanos"], "keywords": ["selección"], "modalities": ["remoto", "hibrido"],
        "max_age_days": 3, "min_score": 30, "max_years_required": 6, "greenhouse_boards": ["ficticia"],
        "sources": {"getonbrd": True, "greenhouse": True, "jooble": True, "linkedin": True}})
    assert r.status_code == 200, r.text
    assert client.put("/api/settings", json={"run_time": "07:30", "auto_cv_min_score": 30}).json()["run_time"] == "07:30"

    # Ejecución (sincrónica para la prueba)
    from app.db import SessionLocal
    from app.models import User
    from app.services.pipeline import run_for_user

    db = SessionLocal()
    user = db.query(User).filter_by(email="prueba@example.com").first()
    run = run_for_user(db, user, trigger="manual")
    s = run.stats
    logs = {l["source"]: l["status"] for l in run.source_logs}
    assert logs == {"getonbrd": "ok", "greenhouse": "ok", "jooble": "no_configurada", "linkedin": "no_configurada"}
    assert s["fetched"] == 6  # 2 términos x 2 avisos de Get on Board + 2 de Greenhouse
    assert s["duplicates"] == 3  # mismo aviso por 2 términos + mismo empresa/puesto en Greenhouse
    assert s["new"] == 3
    jobs = client.get("/api/jobs?view=todas").json()["items"]
    by_title = {j["title"]: j for j in jobs}
    assert not by_title["Analista de Recursos Humanos"]["discarded"]
    assert "Modalidad" in by_title["HR Business Partner"]["discard_reason"] or "Córdoba" in by_title["HR Business Partner"]["discard_reason"]
    assert by_title["Senior Backend Engineer (Go)"]["discarded"]
    assert s["cvs_generated"] == 1
    db.close()

    # Segunda ejecución: nada nuevo (no se reprocesan ofertas)
    db = SessionLocal()
    run2 = run_for_user(db, db.query(User).filter_by(email="prueba@example.com").first(), trigger="manual")
    assert run2.stats["new"] == 0 and run2.stats["duplicates"] == 6
    db.close()

    # Informe enviado por email (SMTP simulado) con CV adjunto
    reps = client.get("/api/reports").json()
    assert reps[-1]["sent"] and reps[-1]["sent_to"] == "prueba@example.com"
    msg = fake_smtp[0]
    html = msg.get_body(("html",)).get_content()
    assert "Analista de Recursos Humanos" in html and "Ver aviso original" in html and "Requisitos faltantes" in html
    assert [a.get_filename() for a in msg.iter_attachments()] == ["CV_Ficticia_Empresa-Ficticia-Tres_Analista-de-Recursos-Humanos.docx"]
    assert "text/html" in client.get(f"/api/reports/{reps[-1]['id']}").headers["content-type"]

    # Detalle, seguimiento, CV
    job = by_title["Analista de Recursos Humanos"]
    d = client.get(f"/api/jobs/{job['id']}").json()
    assert d["status"] == "cv_generado" and d["score_details"]["criteria"]
    r = client.patch(f"/api/jobs/{job['id']}", json={"status": "postulada", "notes": "Enviado por la web", "saved": True})
    assert r.json()["applied_at"] and r.json()["saved"]
    cv_id = d["cv_id"]
    cv = client.get(f"/api/cvs/{cv_id}").json()
    assert cv["content"]["experience"][0]["company"].startswith("Empresa Ficticia Uno")
    docx_resp = client.get(f"/api/cvs/{cv_id}/docx")
    assert docx_resp.status_code == 200 and docx_resp.content[:2] == b"PK"
    assert "CV_Ficticia_Empresa-Ficticia-Tres" in docx_resp.headers["content-disposition"]
    assert client.get(f"/api/cvs/{cv_id}/pdf").content[:4] == b"%PDF"
    content = cv["content"]
    content["summary"] = "Resumen editado por la usuaria."
    r = client.put(f"/api/cvs/{cv_id}", json={"content": content})
    assert r.json()["edited"]
    from app.services.profile import extract_text
    assert "Resumen editado por la usuaria." in extract_text("a.docx", client.get(f"/api/cvs/{cv_id}/docx").content)[0]

    # Oferta manual + descripción pegada + duplicado
    r = client.post("/api/jobs/manual", json={"url": "https://www.linkedin.com/jobs/view/4000000001/", "title": "Recruiter",
                                               "company": "Empresa Ficticia Cinco", "location": "CABA"})
    assert r.status_code == 200 and r.json()["partial_analysis"]
    mid = r.json()["id"]
    assert client.post("/api/jobs/manual", json={"url": "https://linkedin.com/jobs/view/4000000001", "title": "X"}).status_code == 409
    r = client.post(f"/api/jobs/{mid}/description", json={"description": "Requisitos:\n- 2 años en selección de personal\n- Manejo de LinkedIn Recruiter\nModalidad híbrida"})
    assert r.status_code == 200 and r.json()["score_details"]["partial_analysis"] is False
    assert client.post(f"/api/jobs/{mid}/cv").status_code == 200

    # Panel
    st = client.get("/api/jobs/stats").json()
    assert st["saved"] == 1 and st["cvs"] == 2 and st["discarded"] == 2
    assert client.get("/api/jobs?min_score=0&modality=hibrido").json()["total"] >= 1
    assert len(client.get("/api/sources").json()) == 10
    assert len(client.get("/api/runs").json()) == 2

    # Scheduler: el usuario está pendiente si ya pasó su horario y no hubo ejecución automática hoy
    from app import scheduler
    from app.services.pipeline import local_now
    client.put("/api/settings", json={"run_time": "00:00"})
    assert user.id in scheduler.due_users()
    client.put("/api/settings", json={"run_time": "23:59"})
    if local_now().strftime("%H:%M") < "23:59":
        assert user.id not in scheduler.due_users()

    # Eliminación de datos y cuenta
    import os
    from app.security import user_dir
    assert any(user_dir(user.id).iterdir())
    assert client.delete("/api/account/data").status_code == 200
    assert client.get("/api/profile").json()["profile"] is None
    assert not (config.FILES_DIR / f"u{user.id}").exists()
    assert client.delete("/api/account").status_code == 200
    assert client.get("/api/auth/me").status_code == 401
