"""Análisis de avisos, filtros excluyentes y puntuación de compatibilidad (0-100) explicable.

La puntuación es una estimación orientativa calculada con criterios explícitos. No es una
predicción del resultado de un ATS ni de un proceso de selección."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from . import text as T
from .ai import AIError, ai_available, complete_json
from .profile import profile_corpus, profile_skills, profile_years

DISCLAIMER = (
    "Puntuación orientativa calculada por JobHunter AI según los criterios detallados. "
    "No predice el resultado de un ATS ni de un proceso de selección."
)

AMBA_TERMS = [
    "buenos aires", "caba", "c.a.b.a", "capital federal", "ciudad autonoma", "gba", "amba", "gran buenos aires",
    "zona norte", "zona sur", "zona oeste", "vicente lopez", "san isidro", "tigre", "pilar", "escobar", "san martin",
    "tres de febrero", "moron", "ituzaingo", "hurlingham", "la matanza", "san justo", "ramos mejia", "lanus",
    "avellaneda", "quilmes", "lomas de zamora", "banfield", "almirante brown", "esteban echeverria", "ezeiza",
    "berazategui", "florencio varela", "la plata", "san fernando", "malvinas argentinas", "jose c. paz",
    "san miguel", "merlo", "moreno", "olivos", "martinez", "munro", "florida", "palermo", "belgrano", "microcentro",
    "puerto madero", "nunez", "colegiales", "caballito", "retiro", "recoleta", "parque patricios", "saavedra",
]
REMOTE_OK_TERMS = ["argentina", "latam", "latin america", "latinoamerica", "america latina", "south america",
                   "americas", "worldwide", "anywhere", "global", "remote", "remoto", "mundo"]
SENIORITY_ORDER = ["trainee", "junior", "semi senior", "senior", "lead"]


# ---------------- detección de campos ----------------
def detect_modality(*texts: str) -> str:
    t = T.norm(" ".join(t or "" for t in texts))
    if re.search(r"hibrid|hybrid|modalidad mixta", t):
        return "hibrido"
    if re.search(r"100 ?% remoto|full ?remote|fully remote|remoto|remote|home ?office|teletrabajo|trabajo a distancia", t):
        return "remoto"
    if re.search(r"presencial|on.?site|in.?office|en oficina", t):
        return "presencial"
    return ""


def detect_seniority(*texts: str) -> str:
    t = T.norm(" ".join(t or "" for t in texts))
    if re.search(r"\b(trainee|pasante|pasantia|internship|intern)\b", t):
        return "trainee"
    if re.search(r"\b(semi ?senior|ssr|semisenior|mid ?level|mid)\b", t):
        return "semi senior"
    if re.search(r"\b(junior|jr|entry level)\b", t):
        return "junior"
    if re.search(r"\b(lead|lider|head|jefe|jefa|manager|gerente|director|directora|principal|staff)\b", t):
        return "lead"
    if re.search(r"\b(senior|sr)\b", t):
        return "senior"
    return ""


def candidate_seniority(years: float) -> str:
    if years < 1:
        return "trainee"
    if years < 2.5:
        return "junior"
    if years < 5:
        return "semi senior"
    if years < 9:
        return "senior"
    return "lead"


def detect_years_required(text: str) -> int | None:
    t = T.norm(text)
    vals = [int(m) for m in re.findall(r"(\d{1,2})\s*\+?\s*(?:anos|years|yrs)", t) if 0 < int(m) < 30]
    m = re.findall(r"(?:minimo|al menos|mas de|at least)\s*(\d{1,2})", t)
    vals += [int(x) for x in m if 0 < int(x) < 30]
    return min(vals) if vals else None


def parse_salary(text: str) -> tuple[float | None, float | None, str]:
    if not text:
        return None, None, ""
    t = text.replace("\xa0", " ")
    cur = "USD" if re.search(r"usd|u\$s|us\$|dolar|dollar", t, re.I) else ("EUR" if re.search(r"eur|€", t, re.I) else "ARS")
    nums = []
    for raw, k in re.findall(r"(\d[\d.,]*)\s*(k|mil)?\b", t, re.I):
        clean = raw.rstrip(".,")
        if re.fullmatch(r"\d{1,3}([.,]\d{3})+", clean):
            val = float(re.sub(r"[.,]", "", clean))
        else:
            try:
                val = float(clean.replace(",", "."))
            except ValueError:
                continue
        if k:
            val *= 1000
        if val >= 100:
            nums.append(val)
    if not nums:
        return None, None, ""
    return min(nums), max(nums), cur


def is_amba(location: str) -> bool:
    loc = T.norm(location)
    return any(T.contains_term(loc, term) for term in AMBA_TERMS)


# ---------------- extracción heurística de requisitos ----------------
REQ_HEADERS = r"(requisitos|requerimientos|que buscamos|buscamos|perfil buscado|tu perfil|que necesitas|requirements|qualifications|what you.?ll need|what we.?re looking for|must have|te sumaras si)"
RESP_HEADERS = r"(responsabilidades|tareas|funciones|que vas a hacer|tus desafios|desafios|responsibilities|what you.?ll do|the role|principales tareas)"
DESIRED_HEADERS = r"(deseable|deseables|valorable|valoraremos|plus|nice to have|bonus)"
OFFER_HEADERS = r"(ofrecemos|beneficios|benefits|que ofrecemos|we offer|perks)"


def analyze_job_heuristic(title: str, description: str) -> dict:
    lines = [l.strip(" \t•-–*·▪●") for l in (description or "").splitlines()]
    lines = [l for l in lines if len(l) > 2]
    reqs, resps = [], []
    section = None
    for l in lines:
        n = T.norm(l)
        head = n.rstrip(":").strip()
        if len(head) < 45:
            if re.search(DESIRED_HEADERS, head) and len(head.split()) <= 4:
                section = "deseable"; continue
            if re.search(REQ_HEADERS, head):
                section = "requisito"; continue
            if re.search(RESP_HEADERS, head):
                section = "resp"; continue
            if re.search(OFFER_HEADERS, head):
                section = "oferta"; continue
        if re.fullmatch(r"(modalidad|lugar de trabajo|ubicacion|zona|horario|jornada)\b.{0,60}", n):
            continue  # condiciones del puesto, no requisitos del candidato
        if section == "resp":
            resps.append(l)
        elif section in ("requisito", "deseable"):
            typ = section
            if re.search(r"excluyente|indispensable|imprescindible|obligatorio|must", n):
                typ = "excluyente"
            elif re.search(DESIRED_HEADERS, n):
                typ = "deseable"
            reqs.append({"text": l, "type": typ})
        elif re.search(r"excluyente|indispensable|imprescindible", n):
            reqs.append({"text": l, "type": "excluyente"})
    if not reqs:
        # Sin secciones claras: oraciones que mencionan experiencia/conocimientos.
        for sent in re.split(r"(?<=[.;])\s+|\n", description or ""):
            s = sent.strip(" •-")
            if 10 < len(s) < 260 and re.search(r"experiencia|conocimiento|manejo|dominio|titulo|graduad|estudiante|ingles|años|anos", T.norm(s)):
                typ = "excluyente" if re.search(r"excluyente|indispensable", T.norm(s)) else ("deseable" if re.search(DESIRED_HEADERS, T.norm(s)) else "requisito")
                reqs.append({"text": s, "type": typ})
    for r in reqs:
        r["category"] = _req_category(r["text"])
    full = f"{title}\n{description}"
    edu = ""
    m = re.search(r"([^.\n]*(titulo|graduad|licenciad|estudiante|carrera|universitari|terciari)[^.\n]*)", full, re.I)
    if m:
        edu = m.group(1).strip()[:200]
    langs = [T.LANG_NAMES[k] for k in T.LANG_NAMES if T.contains_term(T.norm(full), k)]
    return {
        "method": "heuristico",
        "requirements": reqs[:30],
        "responsibilities": resps[:25],
        "skills_required": T.find_vocab(full, T.SKILL_VOCAB),
        "keywords": top_keywords(full),
        "years_required": detect_years_required(full),
        "seniority": detect_seniority(title) or detect_seniority(description[:600] if description else ""),
        "education_required": edu,
        "languages_required": sorted(set(langs) - {"Español"}),
        "modality": detect_modality(title, description),
    }


def _req_category(text: str) -> str:
    n = T.norm(text)
    if re.search(r"titulo|graduad|licenciad|estudiante|carrera|universitari|terciari|degree", n):
        return "formacion"
    if any(T.contains_term(n, k) for k in T.LANG_NAMES):
        return "idioma"
    if re.search(r"\d+\s*\+?\s*(anos|years)|experiencia", n):
        return "experiencia"
    if T.find_vocab(text, T.SKILL_VOCAB):
        return "habilidad"
    return "otro"


def top_keywords(text: str, n: int = 20) -> list[str]:
    freq: dict[str, int] = {}
    for tok in T.tokens(text):
        if len(tok) > 3:
            freq[tok] = freq.get(tok, 0) + 1
    vocab = T.find_vocab(text, T.SKILL_VOCAB)
    ranked = vocab + [w for w, _ in sorted(freq.items(), key=lambda x: (-x[1], x[0])) if w not in vocab]
    return ranked[:n]


LANG_LEVELS = [(r"\b(nativo|native|bilingue|bilingual|c2)\b", 5), (r"\b(fluido|fluent|c1|proficient)\b", 4),
               (r"\b(avanzado|advanced|upper)\b", 4), (r"\b(b2)\b", 3), (r"\b(intermedio|intermediate|conversacional|b1)\b", 2),
               (r"\b(basico|basic|elemental|a1|a2)\b", 1)]


def language_rank(text: str) -> int:
    n = T.norm(text)
    ranks = [r for pat, r in LANG_LEVELS if re.search(pat, n)]
    return max(ranks) if ranks else 0


def term_in(corpus_norm: str, term: str) -> bool:
    """Coincidencia de término; para frases, alcanza con que estén todas sus palabras significativas."""
    if T.contains_term(corpus_norm, term):
        return True
    words = [w for w in T.norm(term).split() if len(w) > 3 and w not in T.STOPWORDS]
    return len(words) > 1 and all(T.contains_term(corpus_norm, w) for w in words)


def _eval_requirement_heuristic(req: dict, profile: dict, corpus_norm: str, cand_years: float) -> dict:
    text = req["text"]
    n = T.norm(text)
    out = {**req}
    m = re.search(r"(\d{1,2})\s*\+?\s*(anos|years)", n)
    if m:
        need = int(m.group(1))
        if cand_years >= need:
            out.update(status="cumple", evidence=f"{cand_years} años de experiencia total en el perfil (se piden {need}).")
        elif cand_years >= need * 0.6:
            out.update(status="parcial", evidence=f"{cand_years} años de experiencia total; se piden {need}.")
        else:
            out.update(status="falta", evidence=f"{cand_years} años de experiencia total; se piden {need}.")
        return out
    for key, lang in T.LANG_NAMES.items():
        if T.contains_term(n, key) and lang != "Español":
            have = [l for l in profile.get("languages", []) if T.norm(l.get("language")) in (T.norm(lang), key)]
            if have:
                need, got = language_rank(text), language_rank(have[0].get("level", ""))
                ok = got and (not need or got >= need)
                out.update(status="cumple" if ok else "parcial",
                           evidence=f"Perfil: {have[0]['language']} {have[0].get('level') or '(nivel no indicado)'}"
                           + ("" if ok else " — nivel menor al pedido o no indicado."))
            else:
                out.update(status="falta", evidence=f"No figura {lang} en el perfil.")
            return out
    skills = T.find_vocab(text, T.SKILL_VOCAB)
    if skills:
        have = [s for s in skills if term_in(corpus_norm, s)]
        ratio = len(have) / len(skills)
    else:
        toks = [t for t in T.tokens(text) if len(t) > 3]
        have = [t for t in toks if T.contains_term(corpus_norm, t)]
        ratio = len(have) / len(toks) if toks else 0
    status = "cumple" if ratio >= 0.7 else ("parcial" if ratio >= 0.3 else "falta")
    evidence = ("Coincidencias en el perfil: " + ", ".join(have[:8])) if have else "Sin coincidencias en el perfil."
    out.update(status=status, evidence=evidence)
    return out


# ---------------- análisis con IA ----------------
_S = {"type": "string"}
JOB_EVAL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["requirements", "responsibilities", "skills_required", "keywords", "years_required", "seniority",
                 "education_required", "languages_required", "modality", "contract_type"],
    "properties": {
        "requirements": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["text", "type", "category", "status", "evidence"],
            "properties": {
                "text": _S,
                "type": {"type": "string", "enum": ["excluyente", "requisito", "deseable"]},
                "category": {"type": "string", "enum": ["experiencia", "habilidad", "formacion", "idioma", "otro"]},
                "status": {"type": "string", "enum": ["cumple", "parcial", "falta"]},
                "evidence": _S,
            }}},
        "responsibilities": {"type": "array", "items": _S},
        "skills_required": {"type": "array", "items": _S},
        "keywords": {"type": "array", "items": _S},
        "years_required": {"type": "integer"},
        "seniority": {"type": "string", "enum": ["", "trainee", "junior", "semi senior", "senior", "lead"]},
        "education_required": _S,
        "languages_required": {"type": "array", "items": _S},
        "modality": {"type": "string", "enum": ["", "remoto", "hibrido", "presencial"]},
        "contract_type": _S,
    },
}

JOB_EVAL_SYSTEM = """Analizás avisos de empleo y los comparás con el perfil real de un candidato.
1) Extraé del aviso: requisitos (marcá "excluyente" solo si el aviso lo dice: excluyente, indispensable, obligatorio,
   must have; "deseable" si dice deseable, valorable, plus; si no, "requisito"), responsabilidades, herramientas y
   habilidades pedidas, 10-20 palabras clave del aviso, años de experiencia pedidos (0 si no los indica), seniority,
   formación pedida, idiomas pedidos, modalidad y tipo de contratación (si no figuran, "").
2) Para cada requisito evaluá al candidato SOLO con la evidencia del perfil: "cumple" (evidencia clara),
   "parcial" (evidencia relacionada pero incompleta) o "falta" (sin evidencia). En "evidence" citá brevemente
   el dato del perfil que lo respalda o explicá qué falta. No supongas conocimientos que el perfil no menciona."""


def analyze_job(title: str, description: str, profile: dict) -> dict:
    """Extrae requisitos/responsabilidades y evalúa cada requisito contra el perfil."""
    corpus_norm = T.norm(profile_corpus(profile))
    years = profile_years(profile)
    if ai_available() and description and len(description) > 80:
        try:
            import json

            data = complete_json(
                JOB_EVAL_SYSTEM,
                f"<aviso>\nTítulo: {title}\n{description[:20000]}\n</aviso>\n\n<perfil>\n{json.dumps(profile, ensure_ascii=False)}\n</perfil>",
                JOB_EVAL_SCHEMA, effort="low", max_tokens=12000,
            )
            data["method"] = "claude"
            if not data.get("years_required"):
                data["years_required"] = None
            return data
        except AIError as exc:
            fallback = analyze_job_heuristic(title, description)
            fallback["ai_error"] = str(exc)
            analysis = fallback
    else:
        analysis = analyze_job_heuristic(title, description)
    analysis["requirements"] = [_eval_requirement_heuristic(r, profile, corpus_norm, years) for r in analysis["requirements"]]
    return analysis


# ---------------- puntuación ----------------
def _ratio_terms_in(terms: list[str], corpus_norm: str) -> tuple[list[str], list[str]]:
    have = [t for t in terms if term_in(corpus_norm, t)]
    missing = [t for t in terms if t not in have]
    return have, missing


def score_job(job: dict, analysis: dict, profile: dict, search: dict) -> dict:
    corpus = profile_corpus(profile)
    corpus_norm = T.norm(corpus)
    cand_years = profile_years(profile)
    criteria = []

    def add(key, label, weight, value, explanation):
        value = max(0.0, min(1.0, value))
        criteria.append({"key": key, "label": label, "weight": weight, "value": round(value, 2),
                         "points": round(weight * value, 1), "explanation": explanation})

    # 1. Experiencia
    cand_titles = " ".join([profile.get("headline", "")] + [e.get("title", "") for e in profile.get("experience", [])]
                           + search.get("titles", []) + search.get("alt_titles", []))
    title_toks = [t for t in T.tokens(job["title"]) if len(t) > 2]
    cand_norm = T.norm(cand_titles)
    title_hits = [t for t in title_toks if T.contains_term(cand_norm, t)]
    title_sim = len(title_hits) / len(title_toks) if title_toks else 0.5
    yreq = analysis.get("years_required")
    years_factor = 1.0 if not yreq else min(1.0, cand_years / yreq) if yreq else 1.0
    add("experiencia", "Coincidencia de experiencia", 25, 0.6 * title_sim + 0.4 * years_factor,
        f"Términos del puesto presentes en tus cargos/objetivos: {', '.join(title_hits) or 'ninguno'}. "
        + (f"Experiencia total {cand_years} años vs {yreq} pedidos." if yreq else f"Experiencia total {cand_years} años (el aviso no indica años)."))

    # 2. Habilidades técnicas
    skills_req = analysis.get("skills_required") or []
    have_sk, miss_sk = _ratio_terms_in(skills_req, corpus_norm)
    if skills_req:
        add("habilidades", "Habilidades técnicas y herramientas", 25, len(have_sk) / len(skills_req),
            f"Tenés {len(have_sk)} de {len(skills_req)}: {', '.join(have_sk) or '—'}. Faltan: {', '.join(miss_sk) or '—'}.")
    else:
        add("habilidades", "Habilidades técnicas y herramientas", 25, 0.5,
            "El aviso no detalla herramientas concretas (valor neutro).")

    # 3. Formación
    edu_req = analysis.get("education_required") or ""
    edu_text = T.norm(" ".join(f"{e.get('degree','')} {e.get('field','')} {e.get('status','')}" for e in profile.get("education", [])))
    if edu_req:
        req_toks = [t for t in T.tokens(edu_req) if len(t) > 4 and t not in ("titulo", "carrera", "graduado", "estudiante", "universitario", "universitaria", "afines")]
        hits = [t for t in req_toks if T.contains_term(edu_text, t)]
        has_degree = bool(profile.get("education"))
        val = (0.4 if has_degree else 0.0) + (0.6 * len(hits) / len(req_toks) if req_toks else (0.6 if has_degree else 0))
        add("formacion", "Formación", 10, val, f"Se pide: «{edu_req[:140]}». Coincidencias: {', '.join(hits) or 'ninguna directa'}.")
    else:
        add("formacion", "Formación", 10, 1.0 if profile.get("education") else 0.7, "El aviso no exige una formación específica.")

    # 4. Palabras clave
    kws = [k for k in (analysis.get("keywords") or []) if k][:20]
    have_kw, miss_kw = _ratio_terms_in(kws, corpus_norm)
    add("palabras_clave", "Palabras clave del aviso", 15, (len(have_kw) / len(kws)) if kws else 0.5,
        f"{len(have_kw)} de {len(kws)} palabras clave del aviso aparecen en tu perfil.")

    # 5. Seniority
    job_sen = analysis.get("seniority") or ""
    cand_sen = candidate_seniority(cand_years)
    if job_sen in SENIORITY_ORDER:
        dist = abs(SENIORITY_ORDER.index(job_sen) - SENIORITY_ORDER.index(cand_sen))
        add("seniority", "Seniority", 10, {0: 1.0, 1: 0.6}.get(dist, 0.1),
            f"El aviso apunta a {job_sen}; tu experiencia corresponde aproximadamente a {cand_sen}.")
    else:
        add("seniority", "Seniority", 10, 0.7, f"El aviso no indica seniority; tu nivel estimado: {cand_sen}.")

    # 6. Modalidad y ubicación
    mod = job.get("modality") or analysis.get("modality") or ""
    allowed = search.get("modalities") or ["remoto", "hibrido", "presencial"]
    loc = job.get("location", "")
    if mod and mod in allowed and (mod == "remoto" or is_amba(loc) or not loc):
        val, exp = 1.0, f"Modalidad {mod} aceptada" + (f" y ubicación en zona ({loc})." if loc else ".")
    elif not mod:
        val = 0.7 if (not loc or is_amba(loc)) else 0.3
        exp = f"Modalidad no informada. Ubicación: {loc or 'no informada'}."
    else:
        val, exp = 0.2, f"Modalidad {mod} / ubicación {loc or '—'} fuera de tus preferencias."
    add("modalidad_ubicacion", "Modalidad y ubicación", 10, val, exp)

    # 7. Requisitos excluyentes
    reqs = analysis.get("requirements") or []
    excl = [r for r in reqs if r.get("type") == "excluyente"]
    pts = {"cumple": 1.0, "parcial": 0.5, "falta": 0.0}
    if excl:
        val = sum(pts.get(r.get("status"), 0) for r in excl) / len(excl)
        add("excluyentes", "Requisitos excluyentes", 5, val,
            f"{sum(1 for r in excl if r.get('status') == 'cumple')} de {len(excl)} requisitos excluyentes cumplidos.")
    else:
        add("excluyentes", "Requisitos excluyentes", 5, 1.0, "El aviso no marca requisitos como excluyentes.")

    total = sum(c["points"] for c in criteria)
    missing_excl = [r for r in excl if r.get("status") == "falta"]
    penalty = min(20, 10 * len(missing_excl))
    total = max(0, min(100, round(total - penalty)))

    met = [r for r in reqs if r.get("status") == "cumple"]
    partial = [r for r in reqs if r.get("status") == "parcial"]
    missing = [r for r in reqs if r.get("status") == "falta"]
    strengths = [c["explanation"] for c in sorted(criteria, key=lambda c: -c["value"]) if c["value"] >= 0.7][:3]
    return {
        "score": total,
        "criteria": criteria,
        "penalty": penalty,
        "penalty_reason": (f"-{penalty} por {len(missing_excl)} requisito(s) excluyente(s) faltante(s)" if penalty else ""),
        "requirements_met": met, "requirements_partial": partial, "requirements_missing": missing,
        "matched_skills": have_sk, "missing_skills": miss_sk, "matched_keywords": have_kw, "missing_keywords": miss_kw,
        "strengths": strengths,
        "partial_analysis": not job.get("description_complete", True) or not (job.get("description") or "").strip(),
        "method": analysis.get("method", "heuristico"),
        "disclaimer": DISCLAIMER,
    }


# ---------------- filtros excluyentes ----------------
def hard_filter(job: dict, analysis: dict, search: dict, profile: dict) -> str | None:
    """Devuelve el motivo de descarte o None si la oferta pasa los criterios definidos."""
    title_n = T.norm(job["title"])
    for kw in search.get("exclude_keywords", []):
        if kw and T.contains_term(title_n, kw):
            return f"El título contiene una palabra excluida: «{kw}»."
    max_age = search.get("max_age_days")
    pub = job.get("published_at")
    if max_age and isinstance(pub, datetime) and pub < datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=int(max_age)):
        return f"Aviso publicado hace más de {max_age} días ({pub:%d/%m/%Y})."
    mod = job.get("modality") or analysis.get("modality") or ""
    allowed = search.get("modalities") or []
    if mod and allowed and mod not in allowed:
        return f"Modalidad «{mod}» no incluida en tus preferencias ({', '.join(allowed)})."
    loc = job.get("location", "")
    if T.norm(loc) in ("argentina", "ar", "republica argentina"):
        loc = ""  # solo el país: ciudad desconocida, no se descarta
    accepted_locs = search.get("locations") or []
    if mod != "remoto" and loc and not is_amba(loc) and not any(T.contains_term(T.norm(loc), T.norm(a)) for a in accepted_locs):
        if not any(T.contains_term(T.norm(loc), t) for t in ("remote", "remoto", "anywhere", "worldwide")):
            return f"Ubicación fuera de Buenos Aires y alrededores: «{loc}»."
    if mod == "remoto" and loc:
        ln = T.norm(loc)
        if not any(T.contains_term(ln, t) for t in REMOTE_OK_TERMS) and not is_amba(loc):
            return f"Remoto restringido a otra región: «{loc}»."
    min_sal = search.get("min_salary")
    cur = (search.get("salary_currency") or "ARS").upper()
    if min_sal and job.get("salary_max") and (job.get("salary_currency") or "ARS") == cur and job["salary_max"] < float(min_sal):
        return f"Salario publicado ({job.get('salary_text') or job['salary_max']}) menor al mínimo pretendido ({cur} {min_sal:,.0f})."
    max_years = search.get("max_years_required")
    yreq = analysis.get("years_required")
    if max_years not in (None, "") and yreq and yreq > int(max_years):
        return f"Pide {yreq} años de experiencia (tu máximo configurado: {max_years})."
    contracts = [T.norm(c) for c in search.get("contract_types", []) if c]
    ct = T.norm(job.get("contract_type") or analysis.get("contract_type") or "")
    if contracts and ct and not any(c in ct or ct in c for c in contracts):
        return f"Tipo de contratación «{job.get('contract_type') or analysis.get('contract_type')}» no incluido en tus preferencias."
    return None
