"""Utilidades de texto: normalización, tokenización, vocabulario de habilidades y fechas."""
from __future__ import annotations

import re
import unicodedata
from datetime import date

STOPWORDS = set(
    """
a al algo algun alguna algunas alguno algunos ante antes aqui asi aun bajo bien cada como con contra cual cuales
cuando de del desde donde dos el ella ellas ellos en entre era es esa esas ese eso esos esta estas este esto estos
fue fuera ha hace hacia han hasta hay la las le les lo los mas me mi mis mucho muy nos o otra otras otro otros para
pero poco por porque que quien se sea segun ser si sin sobre son su sus tambien tanto te tener tiene tienen todo
todos tu un una uno unos y ya yo nuestro nuestra nuestros nuestras vos usted ustedes etc deseable excluyente
requisito requisitos experiencia conocimiento conocimientos manejo buscamos ofrecemos empresa trabajo puesto
nivel años anos ano año minimo minima minimos capacidad habilidad habilidades persona personas perfil equipo
area areas tareas funciones responsabilidades principales nuevas nuevo nueva importante lider sector
the and or of to in for with on at by an be is are as from this that will you your we our have has a an it its
not but if can all any more most other some such than too very experience knowledge skills skill years year
strong ability work team role job position plus required preferred requirements responsibilities
""".split()
)

# Vocabulario de habilidades/herramientas para detectar requisitos en avisos (no se usa para "inventar"
# habilidades del candidato: solo para encontrar qué pide el aviso y comparar contra el perfil real).
SKILL_VOCAB = [
    # ofimática y datos
    "excel", "excel avanzado", "tablas dinamicas", "macros", "vba", "power bi", "tableau", "looker", "google sheets",
    "google workspace", "office", "microsoft office", "word", "powerpoint", "outlook", "access", "sql", "mysql",
    "postgresql", "oracle", "sql server", "mongodb", "python", "r", "sas", "spss", "stata", "pandas", "etl",
    "data analysis", "analisis de datos", "estadistica", "big data", "bigquery", "snowflake", "databricks", "dbt",
    # ERP / CRM / gestión
    "sap", "sap fi", "sap mm", "sap sd", "sap hr", "tango", "tango gestion", "bejerman", "oracle erp", "dynamics",
    "salesforce", "hubspot", "zoho", "odoo", "netsuite", "jira", "confluence", "trello", "asana", "notion", "monday",
    "kanban", "scrum", "agile", "metodologias agiles", "pmp", "gestion de proyectos", "project management",
    # RR.HH.
    "reclutamiento", "seleccion", "seleccion de personal", "recruiting", "talent acquisition", "hunting", "sourcing",
    "entrevistas por competencias", "onboarding", "induccion", "capacitacion", "desarrollo organizacional",
    "clima laboral", "cultura organizacional", "evaluacion de desempeno", "liquidacion de sueldos", "nomina",
    "payroll", "relaciones laborales", "employer branding", "people analytics", "hrbp", "business partner",
    "beneficios", "compensaciones", "descripcion de puestos", "psicotecnicos", "assessment", "ats", "workday",
    "successfactors", "bumeran", "linkedin recruiter", "gestion del talento", "comunicacion interna",
    # administración / finanzas
    "contabilidad", "facturacion", "cuentas a pagar", "cuentas a cobrar", "conciliaciones bancarias", "tesoreria",
    "impuestos", "afip", "arca", "presupuesto", "control de gestion", "auditoria", "costos", "finanzas",
    "analisis financiero", "ifrs", "niif", "compras", "logistica", "supply chain", "inventarios", "stock",
    "comercio exterior", "atencion al cliente", "customer service", "ventas", "negociacion", "marketing",
    "marketing digital", "seo", "sem", "google ads", "meta ads", "community manager", "redes sociales", "crm",
    # IT / desarrollo
    "javascript", "typescript", "react", "angular", "vue", "node", "node.js", "java", "spring", "c#", ".net", "php",
    "laravel", "django", "flask", "fastapi", "go", "golang", "ruby", "rails", "kotlin", "swift", "android", "ios",
    "html", "css", "git", "github", "gitlab", "docker", "kubernetes", "aws", "azure", "gcp", "linux", "terraform",
    "ci/cd", "jenkins", "rest", "api", "graphql", "microservicios", "testing", "qa", "selenium", "cypress",
    "figma", "ux", "ui", "machine learning", "inteligencia artificial", "ia", "llm", "nlp", "power apps",
    "power automate", "rpa", "uipath", "itil", "soporte tecnico", "redes", "ciberseguridad",
]

SOFT_SKILLS_VOCAB = [
    "comunicacion", "trabajo en equipo", "liderazgo", "proactividad", "organizacion", "resolucion de problemas",
    "pensamiento analitico", "orientacion a resultados", "orientacion al cliente", "adaptabilidad", "empatia",
    "negociacion", "autonomia", "planificacion", "atencion al detalle", "gestion del tiempo", "creatividad",
]

LANG_NAMES = {
    "ingles": "Inglés", "english": "Inglés", "portugues": "Portugués", "portuguese": "Portugués",
    "frances": "Francés", "french": "Francés", "aleman": "Alemán", "german": "Alemán", "italiano": "Italiano",
    "italian": "Italiano", "espanol": "Español", "spanish": "Español", "chino": "Chino", "mandarin": "Chino",
}

MONTHS = {
    "ene": 1, "enero": 1, "jan": 1, "january": 1, "feb": 2, "febrero": 2, "february": 2, "mar": 3, "marzo": 3,
    "march": 3, "abr": 4, "abril": 4, "apr": 4, "april": 4, "may": 5, "mayo": 5, "jun": 6, "junio": 6, "june": 6,
    "jul": 7, "julio": 7, "july": 7, "ago": 8, "agosto": 8, "aug": 8, "august": 8, "sep": 9, "sept": 9,
    "septiembre": 9, "setiembre": 9, "september": 9, "oct": 10, "octubre": 10, "october": 10, "nov": 11,
    "noviembre": 11, "november": 11, "dic": 12, "diciembre": 12, "dec": 12, "december": 12,
}
PRESENT_WORDS = ("actualidad", "presente", "actual", "hoy", "present", "current", "now", "a la fecha")


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def norm(s: str | None) -> str:
    s = strip_accents((s or "").lower())
    s = re.sub(r"[^a-z0-9+#./ ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def tokens(s: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9+#.]+", norm(s)) if len(t) > 1 and t not in STOPWORDS and not t.isdigit()]


def contains_term(text_norm: str, term: str) -> bool:
    """Busca un término normalizado como palabra/frase completa."""
    t = norm(term)
    if not t:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", text_norm) is not None


def find_vocab(text: str, vocab: list[str]) -> list[str]:
    tn = norm(text)
    found = [v for v in vocab if contains_term(tn, v)]
    # Elimina términos contenidos en otros más específicos ("excel" si está "excel avanzado").
    return [f for f in found if not any(f != g and contains_term(norm(g), f) and len(g) > len(f) for g in found)] or found


def parse_month_year(s: str) -> date | None:
    s = norm(s)
    if not s:
        return None
    if any(w in s for w in PRESENT_WORDS):
        return date.today()
    m = re.search(r"(\d{1,2})[/\-.](\d{4})", s)
    if m and 1 <= int(m.group(1)) <= 12:
        return date(int(m.group(2)), int(m.group(1)), 1)
    m = re.search(r"([a-z]+)\.?\s*(?:de\s+)?(\d{4})", s)
    if m and m.group(1) in MONTHS:
        return date(int(m.group(2)), MONTHS[m.group(1)], 1)
    m = re.search(r"(19|20)\d{2}", s)
    if m:
        return date(int(m.group(0)), 1, 1)
    return None


def months_between(start: str, end: str) -> int:
    d1, d2 = parse_month_year(start), parse_month_year(end or "actualidad")
    if not d1 or not d2 or d2 < d1:
        return 0
    return (d2.year - d1.year) * 12 + (d2.month - d1.month) + 1


def total_experience_years(experience: list[dict]) -> float:
    """Suma de meses trabajados sin contar solapamientos."""
    spans = []
    for e in experience or []:
        d1 = parse_month_year(e.get("start", ""))
        d2 = parse_month_year(e.get("end", "") or "actualidad")
        if d1 and d2 and d2 >= d1:
            spans.append((d1.year * 12 + d1.month, d2.year * 12 + d2.month))
    spans.sort()
    total, cur = 0, None
    for a, b in spans:
        if cur is None or a > cur[1]:
            if cur:
                total += cur[1] - cur[0] + 1
            cur = [a, b]
        else:
            cur[1] = max(cur[1], b)
    if cur:
        total += cur[1] - cur[0] + 1
    return round(total / 12, 1)


def html_to_text(html: str) -> str:
    if not html:
        return ""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for li in soup.find_all("li"):
        li.insert_before("\n• ")
    for br in soup.find_all(["br", "p", "div", "h1", "h2", "h3", "h4", "tr"]):
        br.insert_before("\n")
    text = soup.get_text()
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def numbers_in(text: str) -> set[str]:
    """Números presentes en un texto (para verificar que el CV adaptado no invente métricas)."""
    return {n.replace(",", ".").rstrip(".") for n in re.findall(r"\d+(?:[.,]\d+)?", text or "")}
