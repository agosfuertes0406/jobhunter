"""Generación de CV adaptado a un aviso, con validación anti-invención.

Reglas: se mantienen empresas, cargos, fechas, estudios y logros reales. Solo se reordena,
se prioriza y se reformula. Toda salida de IA pasa por `_validate` antes de guardarse."""
from __future__ import annotations

import json
import re

from . import text as T
from .ai import AIError, ai_available, complete_json
from .profile import profile_corpus, profile_skills, profile_years


def _relevance(text: str, terms: list[str]) -> int:
    tn = T.norm(text)
    return sum(1 for t in terms if T.contains_term(tn, t))


def _job_terms(job: dict, analysis: dict) -> list[str]:
    terms = list(analysis.get("skills_required") or []) + list(analysis.get("keywords") or [])
    terms += [t for t in T.tokens(job.get("title", "")) if len(t) > 3]
    return list(dict.fromkeys(T.norm(t) for t in terms if t))


def _base_content(profile: dict) -> dict:
    return {
        "full_name": profile.get("full_name", ""),
        "headline": profile.get("headline", ""),
        "contact": {k: profile.get(k, "") for k in ("email", "phone", "location", "linkedin", "website")},
        "summary": profile.get("summary", ""),
        "skills": profile_skills(profile),
        "soft_skills": list(profile.get("soft_skills", [])),
        "experience": [
            {"ref": i, "company": e["company"], "title": e["title"], "location": e.get("location", ""),
             "start": e.get("start", ""), "end": e.get("end", ""),
             "bullets": list(dict.fromkeys(e.get("responsibilities", []) + e.get("achievements", [])))}
            for i, e in enumerate(profile.get("experience", []))
        ],
        "education": list(profile.get("education", [])),
        "courses": list(profile.get("courses", [])),
        "languages": list(profile.get("languages", [])),
    }


def tailor_heuristic(profile: dict, job: dict, analysis: dict) -> tuple[dict, dict]:
    terms = _job_terms(job, analysis)
    content = _base_content(profile)
    changes = []
    corpus_norm = T.norm(profile_corpus(profile))

    for exp in content["experience"]:
        original = list(exp["bullets"])
        exp["bullets"] = sorted(original, key=lambda b: -_relevance(b, terms))
        if exp["bullets"] != original:
            top = exp["bullets"][0]
            changes.append({"change": f"Se reordenaron las funciones en {exp['company'] or exp['title']}.",
                            "reason": f"Primero las más relacionadas con el aviso (ej.: «{top[:90]}»)."})

    skills = content["skills"]
    matched = [s for s in skills if _relevance(s, terms)]
    content["skills"] = matched + [s for s in skills if s not in matched]
    if matched:
        changes.append({"change": "Se priorizaron habilidades y herramientas: " + ", ".join(matched[:10]) + ".",
                        "reason": "Son requeridas o mencionadas en el aviso y figuran en tu perfil."})

    years = profile_years(profile)
    role = profile.get("headline") or (profile["experience"][0]["title"] if profile.get("experience") else "")
    intro = []
    if role:
        yrs = int(years)
        exp_txt = f" con {yrs} años de experiencia." if years == yrs else f" con más de {yrs} años de experiencia."
        intro.append(f"{role}" + (exp_txt if yrs >= 1 else "."))
    if matched:
        intro.append("Conocimientos relevantes para el puesto: " + ", ".join(matched[:6]) + ".")
    original_summary = profile.get("summary", "").strip()
    content["summary"] = " ".join(intro + ([original_summary] if original_summary else []))
    if intro:
        changes.append({"change": "Se agregó al inicio del resumen una síntesis orientada al puesto.",
                        "reason": "Destaca rol, años de experiencia y herramientas coincidentes, todos datos de tu perfil."})

    used = [t for t in terms if T.contains_term(corpus_norm, t)]
    omitted = [{"keyword": t, "reason": "No figura en tu perfil; no se agregó para no inventar experiencia."}
               for t in terms if t not in used][:15]
    return content, {"changes": changes, "keywords_used": used[:20], "omitted_keywords": omitted, "warnings": []}


_S = {"type": "string"}
TAILOR_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "skills", "experience", "keywords_used", "omitted_keywords", "changes"],
    "properties": {
        "summary": _S,
        "skills": {"type": "array", "items": _S},
        "experience": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["ref", "bullets"],
            "properties": {"ref": {"type": "integer"}, "bullets": {"type": "array", "items": _S}}}},
        "keywords_used": {"type": "array", "items": _S},
        "omitted_keywords": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["keyword", "reason"],
            "properties": {"keyword": _S, "reason": _S}}},
        "changes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["change", "reason"],
            "properties": {"change": _S, "reason": _S}}},
    },
}

TAILOR_SYSTEM = """Adaptás currículums para un aviso específico, en español, con formato apto para ATS.
Reglas no negociables:
- Usá solo información del perfil maestro. No inventes experiencia, responsabilidades, herramientas, estudios,
  métricas, cifras, logros ni requisitos cumplidos. No agregues números que no estén en el perfil.
- Para cada experiencia (identificada por "ref", el índice en el perfil) devolvé sus viñetas: reformulá y reordená
  las funciones y logros reales para destacar primero lo más relevante para el aviso. Podés usar la terminología
  del aviso solo cuando describa fielmente lo que el candidato ya hizo. No mezcles funciones entre experiencias.
- "summary": 3 a 5 líneas, basado en hechos del perfil, orientado al puesto.
- "skills": habilidades y herramientas que el candidato realmente tiene (del perfil), ordenadas por relevancia.
- "keywords_used": palabras clave del aviso que quedaron reflejadas con respaldo real.
- "omitted_keywords": palabras clave importantes del aviso que no se incluyeron porque el perfil no las respalda.
- "changes": lista breve de cambios realizados y por qué."""


def _validate(ai: dict, profile: dict, job: dict, analysis: dict) -> tuple[dict, list[str]]:
    """Reconstruye el CV a partir del perfil maestro y descarta cualquier contenido no respaldado."""
    warnings = []
    content = _base_content(profile)
    corpus = profile_corpus(profile)
    corpus_norm = T.norm(corpus)
    allowed_numbers = T.numbers_in(corpus) | {f"{profile_years(profile):g}", str(int(profile_years(profile))), str(round(profile_years(profile)))}

    by_ref = {e.get("ref"): e for e in ai.get("experience", []) if isinstance(e.get("ref"), int)}
    for exp in content["experience"]:
        src = profile["experience"][exp["ref"]]
        src_numbers = T.numbers_in(" ".join([src.get("start", ""), src.get("end", "")] + src.get("responsibilities", []) + src.get("achievements", [])))
        proposal = by_ref.get(exp["ref"])
        if not proposal or not proposal.get("bullets"):
            warnings.append(f"La IA no devolvió viñetas para {exp['company']}: se usaron las originales.")
            continue
        kept = []
        for b in proposal["bullets"]:
            extra = T.numbers_in(b) - src_numbers
            if extra:
                warnings.append(f"Se descartó una viñeta de {exp['company']} con cifras no presentes en tu perfil ({', '.join(sorted(extra))}).")
                continue
            kept.append(b.strip())
        exp["bullets"] = kept or exp["bullets"]

    real_skills = profile_skills(profile) + list(profile.get("technical_skills", []))
    skills = []
    for s in ai.get("skills", []):
        if T.contains_term(corpus_norm, s) or any(T.norm(s) == T.norm(r) for r in real_skills):
            skills.append(s)
        else:
            warnings.append(f"Se quitó la habilidad «{s}» porque no figura en tu perfil.")
    content["skills"] = list(dict.fromkeys(skills + [s for s in profile_skills(profile) if s not in skills]))

    summary = (ai.get("summary") or "").strip()
    extra = T.numbers_in(summary) - allowed_numbers
    if summary and not extra:
        content["summary"] = summary
    elif extra:
        warnings.append(f"El resumen propuesto contenía cifras no respaldadas ({', '.join(sorted(extra))}); se usó el resumen original.")
    return content, warnings


def tailor_cv(profile: dict, job: dict, analysis: dict) -> tuple[dict, dict, str]:
    if ai_available():
        try:
            ai = complete_json(
                TAILOR_SYSTEM,
                "<perfil_maestro>\n" + json.dumps(_base_content(profile), ensure_ascii=False) + "\n</perfil_maestro>\n\n"
                f"<aviso>\nPuesto: {job.get('title')}\nEmpresa: {job.get('company')}\n{(job.get('description') or '')[:20000]}\n</aviso>\n\n"
                "<analisis_del_aviso>\n" + json.dumps({k: analysis.get(k) for k in ("skills_required", "keywords", "requirements")}, ensure_ascii=False) + "\n</analisis_del_aviso>",
                TAILOR_SCHEMA, effort="medium",
            )
            content, warnings = _validate(ai, profile, job, analysis)
            return content, {
                "changes": ai.get("changes", []), "keywords_used": ai.get("keywords_used", []),
                "omitted_keywords": ai.get("omitted_keywords", []), "warnings": warnings,
            }, "claude"
        except AIError as exc:
            content, info = tailor_heuristic(profile, job, analysis)
            info["warnings"].append(f"IA no disponible ({exc}); se usó la adaptación local.")
            return content, info, "heuristico"
    content, info = tailor_heuristic(profile, job, analysis)
    return content, info, "heuristico"


def _slug(s: str, maxlen: int = 40) -> str:
    s = T.strip_accents(s or "")
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
    return s[:maxlen].strip("-") or "NA"


def filename_base(profile: dict, job: dict) -> str:
    name = (profile.get("full_name") or "Candidato").split()
    apellido = name[-1] if name else "Candidato"
    return f"CV_{_slug(apellido, 25)}_{_slug(job.get('company') or 'Empresa', 30)}_{_slug(job.get('title') or 'Puesto', 45)}"
