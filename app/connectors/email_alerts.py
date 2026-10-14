"""Conectores para LinkedIn, Indeed, ZonaJobs y Computrabajo vía las ALERTAS DE EMPLEO que el propio
usuario recibe en su casilla.

Estos portales no ofrecen una API pública de búsqueda para particulares y sus términos prohíben
el scraping automatizado. La alternativa legítima es: el usuario crea alertas/búsquedas guardadas
en cada portal, y JobHunter lee esos emails de SU casilla vía IMAP (con contraseña de aplicación),
extrae título, empresa, ubicación y link original. No se accede a los sitios ni se evaden logins.
La descripción completa no viene en el email: el usuario puede pegarla en la ficha de la oferta."""
from __future__ import annotations

import email
import imaplib
import re
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup

from .. import config
from ..services.matching import detect_modality
from ..models import utcnow
from .base import Connector, ConnectorError, NotConfigured, RawJob, SearchQuery

IMAP_NEEDS = ["IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD"]

# Cache por proceso de los emails leídos en una ejecución (los 4 conectores comparten la lectura IMAP).
_cache: dict[str, list[tuple[str, datetime | None, str]]] = {}


def reset_cache() -> None:
    _cache.clear()


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def fetch_alert_emails(days: int) -> list[tuple[str, datetime | None, str]]:
    """Devuelve [(from, fecha, html_o_texto)] de los últimos `days` días que provienen de los portales."""
    key = f"{days}"
    if key in _cache:
        return _cache[key]
    if not (config.IMAP_HOST and config.IMAP_USER and config.IMAP_PASSWORD):
        raise NotConfigured("Falta configurar IMAP_HOST, IMAP_USER e IMAP_PASSWORD")
    since = (utcnow() - timedelta(days=max(1, days))).strftime("%d-%b-%Y")
    out = []
    try:
        conn = imaplib.IMAP4_SSL(config.IMAP_HOST, config.IMAP_PORT, timeout=config.HTTP_TIMEOUT)
        conn.login(config.IMAP_USER, config.IMAP_PASSWORD)
        conn.select(config.IMAP_FOLDER, readonly=True)  # solo lectura: no marca emails como leídos
        ids: set[bytes] = set()
        for domain in ALL_DOMAINS:
            typ, data = conn.search(None, "SINCE", since, "FROM", domain)
            if typ == "OK" and data and data[0]:
                ids.update(data[0].split())
        for msg_id in sorted(ids)[-400:]:
            typ, data = conn.fetch(msg_id, "(BODY.PEEK[])")
            if typ != "OK" or not data or not isinstance(data[0], tuple):
                continue
            msg = email.message_from_bytes(data[0][1])
            sender = _decode(msg.get("From", ""))
            try:
                date = parsedate_to_datetime(msg.get("Date")).replace(tzinfo=None) if msg.get("Date") else None
            except (TypeError, ValueError):
                date = None
            out.append((sender, date, _body(msg)))
        conn.logout()
    except (imaplib.IMAP4.error, OSError) as exc:
        raise ConnectorError(f"No se pudo leer la casilla IMAP ({config.IMAP_HOST}): {exc}") from exc
    _cache[key] = out
    return out


def _body(msg) -> str:
    html, text = None, None
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if ctype not in ("text/html", "text/plain") or part.get_filename():
            continue
        payload = part.get_payload(decode=True) or b""
        content = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if ctype == "text/html" and html is None:
            html = content
        elif ctype == "text/plain" and text is None:
            text = content
    return html or text or ""


_GENERIC_LINK_TEXT = re.compile(
    r"^(ver (el )?(empleo|aviso|oferta|todos|mas|más)|postular(me|se)?|aplicar|apply|see all|view job|ver todas|"
    r"buscar|editar|administrar|unsubscribe|darse de baja|configuraci[oó]n|ayuda|privacidad|linkedin|indeed|zonajobs|computrabajo).*",
    re.I,
)


class EmailAlertConnector(Connector):
    access = "alertas_email"
    needs = IMAP_NEEDS
    domains: list[str] = []
    url_pattern: re.Pattern = re.compile(r"$^")

    def canonical_url(self, url: str) -> str | None:
        """Devuelve la URL canónica del aviso o None si el link no es de un aviso."""
        raise NotImplementedError

    def fetch(self, query: SearchQuery) -> list[RawJob]:
        emails = fetch_alert_emails(query.max_age_days or 3)
        jobs: dict[str, RawJob] = {}
        for sender, date, body in emails:
            if not any(d in sender.lower() for d in self.domains):
                continue
            for job in self.parse_email(body, date):
                jobs.setdefault(job.url, job)
        return list(jobs.values())

    def parse_email(self, body: str, date: datetime | None) -> list[RawJob]:
        soup = BeautifulSoup(body, "html.parser")
        anchors = soup.find_all("a", href=True)
        if not anchors:  # email en texto plano
            return [RawJob(source=self.key, title="Aviso de " + self.name, url=u, published_at=date, description_complete=False)
                    for u in {c for c in (self.canonical_url(x) for x in re.findall(r"https?://\S+", body)) if c}]
        found: dict[str, RawJob] = {}
        for a in anchors:
            url = self.canonical_url(a["href"])
            if not url:
                continue
            title = " ".join(a.get_text(" ", strip=True).split())
            if url in found and found[url].title:
                continue
            if not title or len(title) < 4 or _GENERIC_LINK_TEXT.match(title):
                found.setdefault(url, RawJob(source=self.key, title="", url=url, published_at=date, description_complete=False))
                continue
            company, location = self._context(a, title)
            found[url] = RawJob(source=self.key, title=title[:300], url=url, company=company, location=location,
                                modality=detect_modality(location, title), published_at=date, description="",
                                description_complete=False, extra={"via": "alerta por email"})
        return [j for j in found.values() if j.title]

    def _context(self, anchor, title: str) -> tuple[str, str]:
        """Empresa y ubicación: suelen ser las líneas de texto siguientes al título en el mismo bloque."""
        my_url = self.canonical_url(anchor["href"])
        block, lines = anchor, []
        for _ in range(4):
            parent = block.parent
            if parent is None:
                break
            # No subir a un bloque que contenga otros avisos: mezclaría empresa/ubicación de otra oferta.
            others = {self.canonical_url(a["href"]) for a in parent.find_all("a", href=True)} - {None, my_url}
            if others:
                break
            block = parent
            lines = [l.strip() for l in block.get_text("\n", strip=True).split("\n") if l.strip()]
            if len([l for l in lines if l != title and not _GENERIC_LINK_TEXT.match(l)]) >= 1:
                break
        lines = [l for l in lines if l != title and not _GENERIC_LINK_TEXT.match(l)]
        company, location = "", ""
        for l in lines[:4]:
            if "·" in l or " - " in l:
                parts = [p.strip() for p in re.split(r"·| - ", l) if p.strip()]
                if len(parts) >= 2:
                    return parts[0][:200], parts[1][:200]
            if not company and len(l) < 80:
                company = l
            elif not location and len(l) < 80:
                location = l
                break
        return company[:200], location[:200]


def _unwrap(url: str) -> str:
    """Desenvuelve redirecciones de tracking que llevan la URL destino como parámetro."""
    try:
        q = parse_qs(urlparse(url).query)
    except ValueError:
        return url
    for key in ("url", "u", "dest", "destination", "redirect", "target", "r"):
        if key in q and q[key][0].startswith("http"):
            return unquote(q[key][0])
    return url


class LinkedInAlertsConnector(EmailAlertConnector):
    key = "linkedin"
    name = "LinkedIn"
    homepage = "https://www.linkedin.com/jobs/"
    domains = ["linkedin.com"]
    coverage = "Prioritaria. Sin API pública de búsqueda (la Jobs API es solo para partners de Talent Solutions) y su política prohíbe el scraping."
    setup = ("1) En LinkedIn Empleos, buscá cada puesto con ubicación 'Gran Buenos Aires' y activá 'Crear alerta' (frecuencia diaria, por email). "
             "2) Configurá IMAP_HOST, IMAP_USER e IMAP_PASSWORD (para Gmail: imap.gmail.com y una contraseña de aplicación). "
             "JobHunter leerá en modo solo-lectura los emails de jobalerts-noreply@linkedin.com.")

    def canonical_url(self, url):
        url = _unwrap(url)
        m = re.search(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?]*-)?(\d{6,})", url)
        if m:
            return f"https://www.linkedin.com/jobs/view/{m.group(1)}/"
        m = re.search(r"linkedin\.com/.*[?&]currentJobId=(\d{6,})", url)
        return f"https://www.linkedin.com/jobs/view/{m.group(1)}/" if m else None


class IndeedAlertsConnector(EmailAlertConnector):
    key = "indeed"
    name = "Indeed"
    homepage = "https://ar.indeed.com"
    domains = ["indeed.com"]
    coverage = "Prioritaria. La Publisher API fue discontinuada y no hay API de búsqueda para particulares; las APIs actuales son para ATS/empleadores."
    setup = ("1) En ar.indeed.com buscá el puesto con ubicación 'Buenos Aires' y activá 'Recibir nuevos empleos por email'. "
             "2) Configurá IMAP (ver LinkedIn). Se leen los emails de alert@indeed.com / indeed.com.")

    def canonical_url(self, url):
        url = _unwrap(url)
        if "indeed." not in url:
            return None
        q = parse_qs(urlparse(url).query)
        jk = (q.get("jk") or q.get("vjk") or [None])[0]
        if jk and re.fullmatch(r"[0-9a-f]{16}", jk):
            return f"https://ar.indeed.com/viewjob?jk={jk}"
        if re.search(r"indeed\.com/(rc/clk|pagead/clk|viewjob|m/viewjob)", url):
            return url.split("#")[0]
        return None


class ZonaJobsAlertsConnector(EmailAlertConnector):
    key = "zonajobs"
    name = "ZonaJobs"
    homepage = "https://www.zonajobs.com.ar"
    domains = ["zonajobs.com", "zonajobs.com.ar"]
    coverage = "Prioritaria. No ofrece API pública para búsqueda de avisos."
    setup = ("1) En zonajobs.com.ar iniciá sesión, hacé la búsqueda y activá la alerta por email (avisos similares / búsqueda guardada). "
             "2) Configurá IMAP (ver LinkedIn). Se leen los emails enviados desde zonajobs.com.ar.")

    def canonical_url(self, url):
        url = _unwrap(url)
        m = re.search(r"(https?://(?:www\.)?zonajobs\.com\.ar/empleos/[^?#\s\"']+?-\d+\.html)", url)
        return m.group(1) if m else None


class ComputrabajoAlertsConnector(EmailAlertConnector):
    key = "computrabajo"
    name = "Computrabajo"
    homepage = "https://ar.computrabajo.com"
    domains = ["computrabajo.com"]
    coverage = "Prioritaria. No ofrece API pública para búsqueda de avisos."
    setup = ("1) En ar.computrabajo.com creá una alerta de empleo con tu búsqueda (Mi área → Alertas). "
             "2) Configurá IMAP (ver LinkedIn). Se leen los emails enviados desde computrabajo.com.")

    def canonical_url(self, url):
        url = _unwrap(url)
        m = re.search(r"(https?://[a-z]{2}\.computrabajo\.com/ofertas-de-trabajo/oferta-de-trabajo-de-[^?#\s\"']+)", url)
        return m.group(1) if m else None


ALL_DOMAINS = ["linkedin.com", "indeed.com", "zonajobs.com.ar", "computrabajo.com"]
