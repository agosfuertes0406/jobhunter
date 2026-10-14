"""Conectores con APIs oficiales o públicas documentadas."""
from __future__ import annotations

import base64
from datetime import datetime, timezone

from .. import config
from ..services.matching import detect_modality
from ..services.text import html_to_text
from .base import Connector, ConnectorError, NotConfigured, RawJob, SearchQuery, request_json


def _parse_dt(value) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            v = value / 1000 if value > 1e11 else value
            return datetime.fromtimestamp(v, tz=timezone.utc).replace(tzinfo=None)
        s = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt
    except (ValueError, OSError, OverflowError):
        for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%d"):
            try:
                return datetime.strptime(str(value), fmt).replace(tzinfo=None)
            except ValueError:
                continue
    return None


def _terms(query: SearchQuery, limit: int = 6) -> list[str]:
    return [t for t in dict.fromkeys(t.strip() for t in query.terms if t and t.strip())][:limit]


class GetOnBoardConnector(Connector):
    key = "getonbrd"
    name = "Get on Board"
    access = "api_publica"
    homepage = "https://www.getonbrd.com"
    coverage = "Empleos de Latinoamérica (fuerte en tecnología, datos, diseño, marketing digital y RR.HH. de empresas tech), incluye Argentina y remoto."
    setup = "No requiere clave: usa la API pública v0 de Get on Board (búsqueda de avisos publicados)."

    def fetch(self, query: SearchQuery) -> list[RawJob]:
        jobs: list[RawJob] = []
        for term in _terms(query):
            data = request_json("GET", "https://www.getonbrd.com/api/v0/search/jobs",
                                params={"query": term, "per_page": 50, "page": 1, "expand": '["company"]'})
            for item in data.get("data", []):
                a = item.get("attributes", {})
                countries = a.get("countries") or []
                remote_mod = a.get("remote_modality") or ""
                modality = {"fully_remote": "remoto", "remote_local": "remoto", "temporarily_remote": "remoto",
                            "hybrid": "hibrido", "no_remote": "presencial"}.get(remote_mod, "remoto" if a.get("remote") else "")
                comp = ((a.get("company") or {}).get("data") or {}).get("attributes", {}) if isinstance(a.get("company"), dict) else {}
                desc = "\n".join(html_to_text(a.get(k) or "") for k in ("description", "functions", "desirable", "benefits") if a.get(k))
                sal = ""
                if a.get("min_salary") or a.get("max_salary"):
                    sal = f"USD {a.get('min_salary') or ''} - {a.get('max_salary') or ''}".replace(" - ", " - ").strip(" -")
                location = ", ".join(map(str, countries)) or ("Remoto" if modality == "remoto" else "")
                jobs.append(RawJob(
                    source=self.key, title=a.get("title", "").strip(), url=(item.get("links") or {}).get("public_url") or f"https://www.getonbrd.com/jobs/{item.get('id')}",
                    company=comp.get("name", ""), location=location, modality=modality,
                    salary_text=sal, published_at=_parse_dt(a.get("published_at")), description=desc,
                    external_id=str(item.get("id", "")), extra={"countries": countries, "seniority": a.get("seniority")},
                ))
        return jobs


class JoobleConnector(Connector):
    key = "jooble"
    name = "Jooble"
    access = "api_oficial"
    homepage = "https://jooble.org/api/about"
    needs = ["JOOBLE_API_KEY"]
    coverage = "Agregador con API oficial y cobertura en Argentina: indexa avisos de múltiples portales y sitios de empresas. Devuelve un extracto del aviso y el link original."
    setup = ("Solicitá una clave gratuita en https://jooble.org/api/about (formulario 'Get API key'), elegí Argentina "
             "y definí JOOBLE_API_KEY en el .env. Si Jooble te asigna un endpoint regional, definí también JOOBLE_API_URL.")

    def fetch(self, query: SearchQuery) -> list[RawJob]:
        if not config.JOOBLE_API_KEY:
            raise NotConfigured("Falta JOOBLE_API_KEY")
        url = config.JOOBLE_API_URL.rstrip("/") + "/" + config.JOOBLE_API_KEY
        jobs: list[RawJob] = []
        for term in _terms(query):
            data = request_json("POST", url, json={"keywords": term, "location": query.location or "Buenos Aires",
                                                    "page": "1", "ResultOnPage": "50"})
            for j in data.get("jobs", []):
                snippet = html_to_text(j.get("snippet", ""))
                jobs.append(RawJob(
                    source=self.key, title=html_to_text(j.get("title", "")), url=j.get("link", ""),
                    company=j.get("company", ""), location=j.get("location", ""),
                    modality=detect_modality(j.get("title", ""), snippet, j.get("location", "")),
                    contract_type=j.get("type", ""), salary_text=j.get("salary", ""),
                    published_at=_parse_dt(j.get("updated")), description=snippet, description_complete=False,
                    external_id=str(j.get("id", "")), extra={"origin": j.get("source", "")},
                ))
        return jobs


class CareerjetConnector(Connector):
    key = "careerjet"
    name = "Careerjet"
    access = "api_oficial"
    homepage = "https://www.careerjet.com.ar"
    needs = ["CAREERJET_API_KEY"]
    coverage = "Buscador de empleo con API para partners y sitio para Argentina (careerjet.com.ar). Devuelve extracto y link original."
    setup = ("Abrí una cuenta de partner/publisher en Careerjet (https://www.careerjet.com/partners/), obtené tu API key "
             "y definí CAREERJET_API_KEY. Opcional: CAREERJET_LOCALE (por defecto es_AR) y CAREERJET_USER_IP (IP pública del servidor).")

    def fetch(self, query: SearchQuery) -> list[RawJob]:
        if not config.CAREERJET_API_KEY:
            raise NotConfigured("Falta CAREERJET_API_KEY")
        auth = base64.b64encode(f"{config.CAREERJET_API_KEY}:".encode()).decode()
        jobs: list[RawJob] = []
        for term in _terms(query):
            data = request_json("GET", "https://search.api.careerjet.net/v4/query", headers={"Authorization": f"Basic {auth}"},
                                params={"locale_code": config.CAREERJET_LOCALE, "keywords": term,
                                        "location": query.location or "Buenos Aires", "sort": "date", "page": 1,
                                        "pagesize": 50, "user_ip": config.CAREERJET_USER_IP, "user_agent": config.HTTP_USER_AGENT})
            if data.get("type") and data.get("type") != "JOBS":
                continue  # p. ej. LOCATIONS (ubicación ambigua): sin resultados para este término
            for j in data.get("jobs", []):
                desc = html_to_text(j.get("description", ""))
                jobs.append(RawJob(
                    source=self.key, title=html_to_text(j.get("title", "")), url=j.get("url", ""),
                    company=j.get("company", ""), location=j.get("locations", ""),
                    modality=detect_modality(j.get("title", ""), desc), salary_text=j.get("salary", ""),
                    published_at=_parse_dt(j.get("date")), description=desc, description_complete=False,
                    extra={"site": j.get("site", "")},
                ))
        return jobs


class GreenhouseConnector(Connector):
    key = "greenhouse"
    name = "Greenhouse (empresas seleccionadas)"
    access = "api_publica"
    homepage = "https://developers.greenhouse.io/job-board.html"
    coverage = "Avisos publicados por empresas que usan Greenhouse como ATS (API pública de Job Board, texto completo del aviso)."
    setup = "No requiere clave. En Configuración de búsqueda cargá los identificadores de board (ej.: el 'xxx' de boards.greenhouse.io/xxx)."

    def configured(self, options):
        boards = options.get("greenhouse_boards") or []
        return (bool(boards), f"{len(boards)} empresa(s) configurada(s)" if boards else "Cargá al menos una empresa en Configuración de búsqueda")

    def fetch(self, query: SearchQuery) -> list[RawJob]:
        boards = query.options.get("greenhouse_boards") or []
        if not boards:
            raise NotConfigured("No hay empresas Greenhouse configuradas")
        jobs, errors = [], []
        for board in boards:
            try:
                data = request_json("GET", f"https://boards-api.greenhouse.io/v1/boards/{board.strip()}/jobs", params={"content": "true"})
            except ConnectorError as exc:
                errors.append(f"{board}: {exc}")
                continue
            for j in data.get("jobs", []):
                desc = html_to_text(_unescape(j.get("content", "")))
                loc = (j.get("location") or {}).get("name", "")
                jobs.append(RawJob(
                    source=self.key, title=j.get("title", ""), url=j.get("absolute_url", ""),
                    company=j.get("company_name") or board, location=loc, modality=detect_modality(loc, j.get("title", ""), desc[:1500]),
                    published_at=_parse_dt(j.get("first_published") or j.get("updated_at")), description=desc,
                    external_id=str(j.get("id", "")),
                ))
        if errors and not jobs:
            raise ConnectorError("; ".join(errors))
        return jobs


def _unescape(s: str) -> str:
    import html

    return html.unescape(s or "")


class LeverConnector(Connector):
    key = "lever"
    name = "Lever (empresas seleccionadas)"
    access = "api_publica"
    homepage = "https://github.com/lever/postings-api"
    coverage = "Avisos de empresas que usan Lever como ATS (Postings API pública, texto completo)."
    setup = "No requiere clave. Cargá los identificadores de empresa (el 'xxx' de jobs.lever.co/xxx) en Configuración de búsqueda."

    def configured(self, options):
        comps = options.get("lever_companies") or []
        return (bool(comps), f"{len(comps)} empresa(s) configurada(s)" if comps else "Cargá al menos una empresa en Configuración de búsqueda")

    def fetch(self, query: SearchQuery) -> list[RawJob]:
        companies = query.options.get("lever_companies") or []
        if not companies:
            raise NotConfigured("No hay empresas Lever configuradas")
        jobs, errors = [], []
        for comp in companies:
            try:
                data = request_json("GET", f"https://api.lever.co/v0/postings/{comp.strip()}", params={"mode": "json"})
            except ConnectorError as exc:
                errors.append(f"{comp}: {exc}")
                continue
            for j in data if isinstance(data, list) else []:
                cats = j.get("categories") or {}
                lists = "\n".join(f"{l.get('text','')}\n{html_to_text(l.get('content',''))}" for l in j.get("lists", []))
                desc = (j.get("descriptionPlain") or html_to_text(j.get("description", ""))) + "\n" + lists + "\n" + (j.get("additionalPlain") or "")
                wt = (j.get("workplaceType") or "").lower()
                modality = {"remote": "remoto", "hybrid": "hibrido", "onsite": "presencial", "on-site": "presencial"}.get(wt, "") or detect_modality(cats.get("location", ""))
                jobs.append(RawJob(
                    source=self.key, title=j.get("text", ""), url=j.get("hostedUrl", ""), company=comp,
                    location=cats.get("location", ""), modality=modality, contract_type=cats.get("commitment", ""),
                    published_at=_parse_dt(j.get("createdAt")), description=desc.strip(), external_id=str(j.get("id", "")),
                ))
        if errors and not jobs:
            raise ConnectorError("; ".join(errors))
        return jobs


class RemotiveConnector(Connector):
    key = "remotive"
    name = "Remotive (remoto)"
    access = "api_publica"
    homepage = "https://remotive.com/api-documentation"
    coverage = "Empleos 100% remotos internacionales (API pública). Útil solo si aceptás modalidad remota; se filtran los restringidos a otras regiones."
    setup = "No requiere clave. Remotive pide no consultar más de unas pocas veces por día y enlazar al aviso original (la app lo respeta: 1 vez al día)."

    def fetch(self, query: SearchQuery) -> list[RawJob]:
        if query.modalities and "remoto" not in query.modalities:
            return []
        jobs = []
        for term in _terms(query, limit=4):
            data = request_json("GET", "https://remotive.com/api/remote-jobs", params={"search": term, "limit": 50})
            for j in data.get("jobs", []):
                jobs.append(RawJob(
                    source=self.key, title=j.get("title", ""), url=j.get("url", ""), company=j.get("company_name", ""),
                    location=j.get("candidate_required_location", "") or "Remoto", modality="remoto",
                    contract_type=j.get("job_type", ""), salary_text=j.get("salary", ""),
                    published_at=_parse_dt(j.get("publication_date")), description=html_to_text(j.get("description", "")),
                    external_id=str(j.get("id", "")),
                ))
        return jobs
