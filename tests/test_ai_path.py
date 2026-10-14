"""Verifica el camino con IA usando un cliente de Claude SIMULADO (sin llamadas reales)."""
from types import SimpleNamespace

from app.services import ai, matching, profile as profile_svc


class FakeMessages:
    def __init__(self, payload, stop="end_turn"):
        self.payload, self.stop, self.kwargs = payload, stop, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        import json
        return SimpleNamespace(stop_reason=self.stop, content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=json.dumps(self.payload))])


def _install(monkeypatch, payload, stop="end_turn"):
    fake = FakeMessages(payload, stop)
    monkeypatch.setattr(ai.config, "AI_ENABLED", True)
    monkeypatch.setattr(ai, "_client", SimpleNamespace(beta=SimpleNamespace(messages=fake)))
    return fake


def test_complete_json_request_shape(monkeypatch):
    fake = _install(monkeypatch, {"ok": True})
    assert ai.complete_json("sys", "user", {"type": "object"}, effort="low") == {"ok": True}
    k = fake.kwargs
    assert k["model"] == ai.config.ANTHROPIC_MODEL
    assert k["thinking"] == {"type": "adaptive"}
    assert k["output_config"]["format"]["type"] == "json_schema" and k["output_config"]["effort"] == "low"
    assert k["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in k["betas"]


def test_refusal_falls_back_to_heuristic(monkeypatch):
    _install(monkeypatch, {}, stop="refusal")
    prof, method, warnings = profile_svc.analyze_cv("Lucía Prueba\nAnalista\nEXPERIENCIA\nAnalista | Empresa X | 2020 - Actualidad\n• Tareas de selección")
    assert method == "heuristico" and "IA no estuvo disponible" in warnings[0]


def test_ai_job_analysis(monkeypatch):
    payload = {"requirements": [{"text": "3 años en selección", "type": "excluyente", "category": "experiencia", "status": "cumple", "evidence": "5 años en el perfil"}],
               "responsibilities": ["Selección"], "skills_required": ["Excel"], "keywords": ["selección"], "years_required": 0,
               "seniority": "semi senior", "education_required": "", "languages_required": [], "modality": "hibrido", "contract_type": ""}
    _install(monkeypatch, payload)
    a = matching.analyze_job("Analista", "x" * 200, {"experience": []})
    assert a["method"] == "claude" and a["years_required"] is None and a["requirements"][0]["status"] == "cumple"
