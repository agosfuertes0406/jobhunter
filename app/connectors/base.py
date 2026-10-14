"""Interfaz común de conectores de búsqueda.

Cada conector es independiente, declara cómo accede a la fuente (API oficial, API pública,
alertas por email del propio usuario) y nunca simula resultados."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import httpx

from .. import config


class ConnectorError(Exception):
    """Error al consultar la fuente (red, credenciales, formato)."""


class NotConfigured(ConnectorError):
    """Faltan credenciales o parámetros para usar la fuente."""


@dataclass
class RawJob:
    source: str
    title: str
    url: str
    company: str = ""
    location: str = ""
    modality: str = ""
    contract_type: str = ""
    salary_text: str = ""
    published_at: datetime | None = None
    description: str = ""
    description_complete: bool = True
    external_id: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class SearchQuery:
    terms: list[str]
    location: str
    max_age_days: int
    modalities: list[str]
    options: dict  # configuración completa de búsqueda (p. ej. listas de empresas para Greenhouse/Lever)


class Connector:
    key: str = ""
    name: str = ""
    access: str = ""  # api_oficial | api_publica | alertas_email | no_automatizable
    homepage: str = ""
    needs: list[str] = []  # variables de entorno requeridas
    setup: str = ""  # instrucciones (texto plano) para habilitarla
    coverage: str = ""
    automatable: bool = True

    def configured(self, options: dict) -> tuple[bool, str]:
        missing = [n for n in self.needs if not getattr(config, n, None)]
        if missing:
            return False, "Falta configurar: " + ", ".join(missing)
        return True, "Configurada"

    def fetch(self, query: SearchQuery) -> list[RawJob]:  # pragma: no cover - interfaz
        raise NotImplementedError

    def info(self, options: dict) -> dict:
        ok, msg = self.configured(options)
        return {"key": self.key, "name": self.name, "access": self.access, "homepage": self.homepage,
                "needs": self.needs, "setup": self.setup, "coverage": self.coverage,
                "automatable": self.automatable, "configured": ok, "config_message": msg}


def http_client() -> httpx.Client:
    return httpx.Client(timeout=config.HTTP_TIMEOUT, headers={"User-Agent": config.HTTP_USER_AGENT, "Accept": "application/json"},
                        follow_redirects=True)


def request_json(method: str, url: str, **kwargs):
    try:
        with http_client() as c:
            resp = c.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise ConnectorError(f"Error de conexión con {httpx.URL(url).host}: {exc.__class__.__name__}: {exc}") from exc
    if resp.status_code in (401, 403):
        raise ConnectorError(f"Acceso denegado por {resp.url.host} (HTTP {resp.status_code}). Verificá la clave/credencial.")
    if resp.status_code == 429:
        raise ConnectorError(f"{resp.url.host} limitó la cantidad de consultas (HTTP 429). Se reintentará en la próxima ejecución.")
    if resp.status_code >= 400:
        raise ConnectorError(f"{resp.url.host} respondió HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        return resp.json()
    except ValueError as exc:
        raise ConnectorError(f"{resp.url.host} devolvió una respuesta no JSON") from exc
