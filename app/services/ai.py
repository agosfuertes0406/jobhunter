"""Cliente de IA (Claude, vía SDK oficial de Anthropic) con salida JSON estructurada.

Si no hay ANTHROPIC_API_KEY, `ai_available()` devuelve False y cada servicio usa su
método heurístico local (identificado como tal en la interfaz)."""
from __future__ import annotations

import json
import logging

from .. import config

log = logging.getLogger("jobhunter.ai")

_client = None


class AIError(Exception):
    pass


def ai_available() -> bool:
    return config.AI_ENABLED


def _get_client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic(timeout=180.0, max_retries=2)
    return _client


def complete_json(system: str, user: str, schema: dict, *, effort: str = "medium", max_tokens: int = 16000) -> dict:
    """Llama a Claude pidiendo un JSON que cumpla `schema`. Lanza AIError ante cualquier falla."""
    if not ai_available():
        raise AIError("IA no configurada (falta ANTHROPIC_API_KEY)")
    import anthropic

    client = _get_client()
    try:
        response = client.beta.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            thinking={"type": "adaptive"},
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            # Si un clasificador de seguridad rechaza la solicitud, el servidor reintenta con el modelo
            # de respaldo recomendado en lugar de devolver el rechazo.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.RateLimitError as exc:
        raise AIError(f"Límite de uso de la API de Claude alcanzado: {exc}") from exc
    except anthropic.AuthenticationError as exc:
        raise AIError("ANTHROPIC_API_KEY inválida") from exc
    except anthropic.APIStatusError as exc:
        raise AIError(f"Error de la API de Claude ({exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise AIError(f"No se pudo conectar con la API de Claude: {exc}") from exc

    if response.stop_reason == "refusal":
        raise AIError("El modelo rechazó la solicitud")
    if response.stop_reason == "max_tokens":
        raise AIError("La respuesta del modelo quedó truncada")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise AIError("Respuesta vacía del modelo")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AIError(f"JSON inválido devuelto por el modelo: {exc}") from exc
