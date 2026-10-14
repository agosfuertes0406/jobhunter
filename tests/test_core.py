"""Pruebas de los servicios centrales (sin red). Datos ficticios."""
from datetime import datetime, timedelta
from pathlib import Path

from app.connectors.email_alerts import (
    ComputrabajoAlertsConnector, IndeedAlertsConnector, LinkedInAlertsConnector, ZonaJobsAlertsConnector,
)
from app.services import cv_tailor
from app.services.documents import render_docx, render_pdf
from app.services.matching import analyze_job, hard_filter, parse_salary, score_job
from app.services.pipeline import company_title_key, normalize_url
from app.services.profile import extract_profile_heuristic, extract_text, profile_years
from app.models import utcnow

FIX = Path(__file__).parent / "fixtures"

JOB_DESC = """Empresa Ficticia Dos busca Analista de Recursos Humanos para su equipo en CABA.
Responsabilidades:
- Gestionar procesos de reclutamiento y selección end to end.
- Coordinar el onboarding de nuevos ingresos.
- Elaborar reportes de indicadores en Power BI.
Requisitos:
- Licenciatura en Relaciones del Trabajo, Psicología o afines.
- Experiencia mínima de 3 años en selección de personal (excluyente).
- Manejo de Excel avanzado y SAP.
- Inglés avanzado.
Deseable:
- Conocimiento de Workday.
Modalidad híbrida."""


def test_extract_and_parse_docx(cv_docx):
    text, mime = extract_text("cv.docx", cv_docx)
    assert "Lucía Prueba Ficticia" in text
    profile, warnings = extract_profile_heuristic(text)
    assert profile["email"] == "lucia.prueba@example.com"
    assert profile["full_name"] == "Lucía Prueba Ficticia"
    assert len(profile["experience"]) == 2
    e0 = profile["experience"][0]
    assert e0["title"] == "Analista de Selección" and "Empresa Ficticia Uno" in e0["company"]
    assert e0["start"] == "03/2021" and e0["end"] == "Actualidad"
    assert any("25%" in a for a in e0["achievements"])
    assert profile["languages"][0] == {"language": "Inglés", "level": "Intermedio (B2)"}
    assert "Liquidación de sueldos" in profile["technical_skills"]
    assert any("excel" in s.lower() for s in profile["tools"] + profile["technical_skills"])
    assert profile_years(profile) >= 5


def test_extract_pdf(cv_pdf):
    text, mime = extract_text("cv.pdf", cv_pdf)
    assert mime == "application/pdf" and "Analista de Selección" in text
    profile, _ = extract_profile_heuristic(text)
    assert len(profile["experience"]) == 2


def _profile(cv_docx):
    text, _ = extract_text("cv.docx", cv_docx)
    return extract_profile_heuristic(text)[0]


def test_analysis_and_score(cv_docx):
    profile = _profile(cv_docx)
    analysis = analyze_job("Analista de Recursos Humanos", JOB_DESC, profile)
    types = {r["type"] for r in analysis["requirements"]}
    assert "excluyente" in types and "deseable" in types
    assert analysis["years_required"] == 3
    assert analysis["modality"] == "hibrido"
    assert "sap" in analysis["skills_required"] and "power bi" in analysis["skills_required"]
    job = {"title": "Analista de Recursos Humanos", "company": "Empresa Ficticia Dos", "location": "CABA",
           "modality": "hibrido", "description": JOB_DESC, "description_complete": True}
    search = {"titles": ["Analista de RR.HH."], "modalities": ["remoto", "hibrido"]}
    d = score_job(job, analysis, profile, search)
    assert 0 <= d["score"] <= 100
    assert sum(c["weight"] for c in d["criteria"]) == 100
    statuses = {r["text"]: r["status"] for r in analysis["requirements"]}
    assert statuses[next(t for t in statuses if "3 años" in t)] == "cumple"
    assert "sap" in d["missing_skills"] and "power bi" in d["matched_skills"]
    assert any(r["text"].startswith("Inglés avanzado") for r in d["requirements_partial"])  # perfil: B2 < avanzado
    assert not any("Modalidad" in r["text"] for r in analysis["requirements"])
    assert "seleccion de personal" not in d["missing_skills"]
    assert "No predice" in d["disclaimer"]


def test_hard_filters(cv_docx):
    profile = _profile(cv_docx)
    a = {"years_required": 8, "modality": ""}
    base = {"title": "Analista", "location": "CABA", "modality": "presencial", "published_at": utcnow()}
    assert "Modalidad" in hard_filter(base, a, {"modalities": ["remoto"]}, profile)
    assert "fuera de Buenos Aires" in hard_filter({**base, "location": "Córdoba"}, a, {"modalities": []}, profile)
    assert hard_filter({**base, "location": "Argentina"}, {}, {"modalities": []}, profile) is None
    assert "8 años" in hard_filter(base, a, {"max_years_required": 5}, profile)
    old = {**base, "published_at": utcnow() - timedelta(days=10)}
    assert "más de 3 días" in hard_filter(old, {}, {"max_age_days": 3}, profile)
    sal = {**base, "salary_max": 500000, "salary_currency": "ARS", "salary_text": "$500.000"}
    assert "Salario" in hard_filter(sal, {}, {"min_salary": 900000, "salary_currency": "ARS"}, profile)
    assert "restringido" in hard_filter({**base, "modality": "remoto", "location": "USA Only"}, {}, {}, profile)
    assert hard_filter({**base, "modality": "remoto", "location": "LATAM"}, {}, {}, profile) is None


def test_salary_and_dedup():
    assert parse_salary("$ 800.000 - 1.200.000") == (800000, 1200000, "ARS")
    assert parse_salary("USD 2000-3000")[2] == "USD"
    assert normalize_url("https://www.LinkedIn.com/jobs/view/123/?trackingId=x&utm_source=y") == "linkedin.com/jobs/view/123"
    assert normalize_url("https://ar.indeed.com/viewjob?jk=abc&from=x") == "ar.indeed.com/viewjob?jk=abc"
    assert company_title_key("Empresa Ficticia S.A.", "Analista RRHH") == company_title_key("empresa ficticia", "Analista RRHH")


def test_tailor_heuristic_and_documents(cv_docx):
    profile = _profile(cv_docx)
    job = {"title": "Analista de Recursos Humanos", "company": "Empresa Ficticia Dos", "description": JOB_DESC}
    analysis = analyze_job(job["title"], JOB_DESC, profile)
    content, info, method = cv_tailor.tailor_cv(profile, job, analysis)
    assert method == "heuristico"
    # Nunca cambia empresas ni fechas.
    assert [(e["company"], e["start"], e["end"]) for e in content["experience"]] == \
           [(e["company"], e["start"], e["end"]) for e in profile["experience"]]
    # No agrega herramientas que el candidato no tiene.
    assert not any("sap" == s.lower() for s in content["skills"])
    assert any(o["keyword"] == "sap" for o in info["omitted_keywords"])
    assert cv_tailor.filename_base(profile, job) == "CV_Ficticia_Empresa-Ficticia-Dos_Analista-de-Recursos-Humanos"
    docx_bytes, pdf_bytes = render_docx(content), render_pdf(content)
    assert docx_bytes[:2] == b"PK" and pdf_bytes[:4] == b"%PDF"
    text, _ = extract_text("x.docx", docx_bytes)
    assert "EXPERIENCIA LABORAL" in text and "Empresa Ficticia Uno SA" in text
    import docx, io
    assert len(docx.Document(io.BytesIO(docx_bytes)).tables) == 0  # sin tablas (ATS)
    ptext, _ = extract_text("x.pdf", pdf_bytes)
    assert "Lucía Prueba Ficticia" in ptext and "Reduje el tiempo promedio" in ptext


def test_ai_output_validation_blocks_invention(cv_docx):
    """Simula una respuesta de IA que inventa una cifra y una herramienta: el validador las elimina."""
    profile = _profile(cv_docx)
    fake_ai = {
        "summary": "Analista con 15 años liderando equipos de 40 personas.",  # cifras inventadas
        "skills": ["Excel avanzado", "SAP", "Power BI"],  # SAP inventado
        "experience": [
            {"ref": 0, "bullets": ["Reduje el tiempo de cobertura un 25% en 2023.", "Aumenté la retención un 90%."]},
        ],
        "keywords_used": [], "omitted_keywords": [], "changes": [],
    }
    content, warnings = cv_tailor._validate(fake_ai, profile, {}, {})
    assert "SAP" not in content["skills"]
    assert content["summary"] == profile["summary"]
    assert content["experience"][0]["bullets"] == ["Reduje el tiempo de cobertura un 25% en 2023."]
    assert content["experience"][1]["bullets"]  # la experiencia omitida por la IA conserva las originales
    assert content["experience"][0]["company"] == profile["experience"][0]["company"]
    assert len(warnings) >= 3


def test_email_alert_parsers():
    html = (FIX / "linkedin_alert.html").read_text()
    jobs = LinkedInAlertsConnector().parse_email(html, datetime(2026, 10, 14))
    assert [j.url for j in jobs] == ["https://www.linkedin.com/jobs/view/4012345678/", "https://www.linkedin.com/jobs/view/4087654321/"]
    assert jobs[0].title == "Analista de Recursos Humanos"
    assert jobs[0].company == "Empresa Ficticia Dos" and "Buenos Aires" in jobs[0].location
    assert jobs[1].modality == "remoto"
    assert all(not j.description_complete for j in jobs)
    assert IndeedAlertsConnector().canonical_url("https://ar.indeed.com/rc/clk?jk=0123456789abcdef&from=ja") == "https://ar.indeed.com/viewjob?jk=0123456789abcdef"
    assert ZonaJobsAlertsConnector().canonical_url("https://www.zonajobs.com.ar/empleos/analista-rrhh-1116123456.html?utm=x") == "https://www.zonajobs.com.ar/empleos/analista-rrhh-1116123456.html"
    assert ComputrabajoAlertsConnector().canonical_url("https://ar.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-analista-en-caba-ABC123#lc=x").endswith("ABC123")
    assert LinkedInAlertsConnector().canonical_url("https://www.linkedin.com/comm/jobs/search?keywords=x") is None
