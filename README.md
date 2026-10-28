# JobHunter AI

Asistente personal de búsqueda laboral: cargás tu CV una vez y la aplicación busca ofertas nuevas todos los días,
las compara con tu perfil, descarta las incompatibles, genera un CV adaptado para ATS (DOCX y PDF) para cada puesto
relevante y te envía un informe diario por email.

> La app **nunca se postula ni envía tus datos a empresas**, **no inventa experiencia** y **no simula ofertas**.
> Si una fuente no está configurada o falla, se informa como tal en el panel y en el informe.

---

## 1. Arquitectura

```
 Navegador (SPA vanilla JS)  ──HTTP/JSON──▶  FastAPI (app/main.py)
                                              ├─ api/            auth, perfil, ofertas, CVs, sistema
                                              ├─ services/
                                              │   ├─ profile.py      lectura PDF/DOCX + perfil maestro (Claude o heurístico)
                                              │   ├─ matching.py     requisitos, filtros excluyentes, puntuación 0-100
                                              │   ├─ cv_tailor.py    CV adaptado + validación anti-invención
                                              │   ├─ documents.py    render DOCX (python-docx) y PDF (reportlab)
                                              │   ├─ pipeline.py     ejecución diaria: fuentes → dedupe → filtros → score → CV → informe
                                              │   ├─ report.py       informe HTML + envío SMTP
                                              │   └─ ai.py           cliente Claude (SDK oficial de Anthropic)
                                              ├─ connectors/     un conector independiente por fuente
                                              └─ security.py     scrypt, cookie firmada, CSRF, cifrado Fernet de archivos
 Worker (app/worker.py, APScheduler) ─────▶  misma base de datos; revisa cada 5 min quién debe ejecutar hoy
 Base de datos: SQLite (dev) o PostgreSQL (prod) · Archivos: DATA_DIR/files cifrados (CVs originales y generados)
```

| Requisito | Implementación |
|---|---|
| Frontend | SPA sin build (`static/`), responsive, en español |
| Backend | FastAPI + SQLAlchemy 2 |
| Base de datos | SQLite por defecto; PostgreSQL con `DATABASE_URL` (incluido en docker-compose) |
| Autenticación | Email + contraseña (scrypt), cookie HttpOnly firmada, SameSite=Lax + cabecera anti-CSRF |
| Documentos | Cifrados en disco con Fernet (`FILE_ENCRYPTION_KEY`); descarga solo con sesión del dueño |
| Secretos | Solo variables de entorno del servidor; el frontend nunca los recibe |
| Automatización | Worker APScheduler separado, o `RUN_SCHEDULER_IN_WEB=true`, o `python -m app.cli run-daily` desde un cron externo |
| Eliminación | Ajustes → "Eliminar perfil y archivos" / "Eliminar cuenta completa" (borra filas y archivos) |

## 2. Fuentes de empleo: qué se puede consultar legítimamente

| Fuente | ¿API de búsqueda para particulares? | Cómo la integra JobHunter |
|---|---|---|
| **LinkedIn** (prioritaria) | No. La Jobs API es solo para partners aprobados de Talent Solutions y sirve para publicar avisos propios; los términos prohíben el scraping. | **Alertas de empleo por email**: creás alertas diarias en LinkedIn y la app lee esos emails de tu casilla por IMAP en solo lectura (título, empresa, ubicación, link). |
| **ZonaJobs** (prioritaria) | No hay API pública de búsqueda. | Alertas por email (IMAP). |
| **Computrabajo** (prioritaria) | No hay API pública de búsqueda. | Alertas por email (IMAP). |
| **Indeed** (prioritaria) | No. La Publisher API fue discontinuada; las APIs actuales son para ATS/empleadores y requieren aprobación. | Alertas por email (IMAP). |
| **Jooble** | Sí: API REST oficial con clave gratuita; cobertura en Argentina (agrega avisos de múltiples sitios). | Conector `jooble` (necesita `JOOBLE_API_KEY`). |
| **Careerjet** | Sí: API para partners, sitio para Argentina (`es_AR`). | Conector `careerjet` (necesita `CAREERJET_API_KEY`). |
| **Get on Board** | Sí: API pública v0 sin clave (búsqueda de avisos publicados). LatAm, fuerte en tech/datos/RR.HH. tech. | Conector `getonbrd`, sin configuración. |
| **Greenhouse / Lever** | Sí: APIs públicas de "job board" de cada empresa. | Conectores `greenhouse` y `lever`: cargás las empresas que te interesan. |
| **Remotive** | Sí: API pública (solo empleos remotos). | Conector `remotive` (desactivado por defecto). |

Limitaciones honestas:
- Los emails de alerta **no traen la descripción completa** del aviso: esas ofertas se marcan como "análisis parcial".
  En la ficha de la oferta podés pegar la descripción y la app la reanaliza al instante. También podés cargar avisos
  manualmente ("+ Agregar oferta manual").
- Jooble y Careerjet devuelven un extracto del aviso, también marcado como análisis parcial.
- Los formatos de respuesta de las APIs están implementados según su documentación pública. **En el entorno donde se
  construyó la app no había salida de red hacia esos dominios**, así que la primera conexión real debe verificarse con
  `python -m app.cli check-sources` (ver sección 6). El parser de alertas se probó con emails de ejemplo ficticios;
  si algún portal cambia el formato de sus emails, el conector lo reportará con 0 resultados en el panel de Fuentes.

## Uso gratis en tu computadora (sin servidor y sin IA paga)

1. Instalá **Python 3.12 o superior** desde https://www.python.org/downloads/
   (en Windows, marcá la casilla **"Add python.exe to PATH"** en el instalador).
2. Descargá este proyecto (botón verde **Code → Download ZIP** en GitHub) y descomprimilo en una carpeta fija,
   por ejemplo `Documentos/jobhunter`.
3. Doble clic en **`iniciar_windows.bat`** (Windows) o **`iniciar_mac.command`** (Mac; si macOS lo bloquea:
   clic derecho → Abrir). La primera vez instala todo (unos minutos); después arranca en segundos y abre
   http://localhost:8000 en tu navegador.
4. Dejá esa ventana abierta mientras uses la app. La búsqueda diaria se ejecuta sola a la hora configurada
   **mientras la ventana esté abierta**; si a esa hora la computadora estaba apagada, se ejecuta apenas la abrís.
5. Opcional: completá el archivo `.env` (se crea solo la primera vez) con tu Gmail y su contraseña de aplicación
   para recibir el informe y leer las alertas de LinkedIn, ZonaJobs, Computrabajo e Indeed. Cerrá y volvé a abrir
   la app después de editarlo.

Sin `ANTHROPIC_API_KEY` la app usa el análisis local: revisá y corregí tu perfil después de subir el CV.
Tus datos quedan solo en tu computadora, en la carpeta `data/` (hacé copia de seguridad de esa carpeta).

## 3. Puesta en marcha local (desarrollo)

```bash
cd jobhunter-ai
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # completá lo que tengas (todo es opcional para probar)
uvicorn app.main:app --reload --port 8000     # web
python -m app.worker                           # en otra terminal: ejecución diaria automática
```
Abrí http://localhost:8000, creá tu cuenta, subí tu CV, revisá el perfil, configurá la búsqueda y presioná **Buscar ahora**.

Pruebas automáticas: `python -m pytest -q` (12 pruebas; usan datos ficticios y no salen a la red).

## 4. Qué tenés que configurar (y cómo)

| Para… | Variables | Cómo obtenerlas |
|---|---|---|
| Producción segura | `APP_SECRET_KEY`, `FILE_ENCRYPTION_KEY`, `COOKIE_SECURE=true`, `ALLOW_REGISTRATION=false` (después de crear tu cuenta) | Comandos de generación en `.env.example`. **Hacé backup de `FILE_ENCRYPTION_KEY`**: sin ella no se pueden leer los archivos. |
| Análisis con IA (Claude) | `ANTHROPIC_API_KEY` (opcional `ANTHROPIC_MODEL`, por defecto `claude-opus-5-5`) | https://console.anthropic.com → API Keys. Sin clave funciona con análisis heurístico local (menos preciso; la interfaz lo indica). |
| Informe por email | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | Gmail: activá verificación en 2 pasos → https://myaccount.google.com/apppasswords → creá una contraseña de aplicación. Host `smtp.gmail.com`, puerto 587. |
| LinkedIn / ZonaJobs / Computrabajo / Indeed | `IMAP_HOST`, `IMAP_USER`, `IMAP_PASSWORD` | Gmail: `imap.gmail.com` + la misma contraseña de aplicación. Luego creá en cada portal una **alerta diaria por email** con tu búsqueda y ubicación Buenos Aires (pasos en la pantalla *Fuentes*). Recomendado: usar una casilla dedicada a búsqueda laboral. |
| Jooble | `JOOBLE_API_KEY` | https://jooble.org/api/about → formulario de solicitud de clave. |
| Careerjet | `CAREERJET_API_KEY`, `CAREERJET_USER_IP` | https://www.careerjet.com/partners/ → cuenta de partner. |
| Greenhouse / Lever | — | En *Búsqueda*, cargá el identificador de cada empresa (`boards.greenhouse.io/<id>`, `jobs.lever.co/<id>`). |

El estado de IA, SMTP e IMAP se ve en **Ajustes → Estado del servidor**; el de cada fuente en **Fuentes**.

## 5. Despliegue y automatización diaria

**Docker Compose (VPS propio):** `cp .env.example .env`, completar, y `docker compose up -d --build`.
Levanta `web` (puerto 8000), `worker` (scheduler diario) y `db` (PostgreSQL). Poné un proxy HTTPS delante (Caddy/Nginx) y `COOKIE_SECURE=true`.

**Plataformas con cron (Render, Railway, Fly.io):**
- Servicio web: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Cron job cada 15 minutos (o una vez por día después de tu horario): `python -m app.cli run-daily`
- Base PostgreSQL gestionada en `DATABASE_URL` y un disco persistente para `DATA_DIR`.

**Un solo proceso:** `RUN_SCHEDULER_IN_WEB=true` (un lock de archivo evita schedulers duplicados).

Funcionamiento: cada 5 minutos el scheduler revisa, por usuario, si ya pasó el horario configurado en *Ajustes*
(hora de Buenos Aires) y si todavía no hubo ejecución automática hoy. Si el servidor estuvo apagado a esa hora,
ejecuta apenas vuelve. Cada ejecución: consulta fuentes → detecta avisos recientes → guarda título, empresa,
ubicación, modalidad, salario, fecha y URL → extrae requisitos y responsabilidades → deduplica (URL normalizada y
empresa+puesto) → filtra (modalidad, zona AMBA, salario, antigüedad, años requeridos, contratación, palabras
excluidas, compatibilidad mínima) → puntúa → genera CVs para las mejores → arma y envía el informe → registra
errores por fuente. Las ofertas "Nueva" del día anterior pasan a "Pendiente de revisar".

## 6. Verificación

```bash
python -m pytest -q                                    # pruebas automáticas
python -m app.cli check-sources --term "analista"      # prueba real de cada fuente configurada
python -m app.cli run-now --email tu@email.com         # ejecución completa inmediata (muestra estadísticas y errores)
```

## 7. Compatibilidad y CV adaptado

- **Puntuación 0-100** con 7 criterios ponderados y explicados en la ficha: experiencia (25), habilidades técnicas (25),
  palabras clave (15), formación (10), seniority (10), modalidad/ubicación (10), requisitos excluyentes (5, con
  penalización si falta alguno). Cada requisito se clasifica como cumplido, parcial o faltante con su evidencia.
  Es una estimación orientativa: **no predice el resultado de un ATS ni de una contratación**.
- **CV adaptado**: reordena y redacta funciones reales, prioriza habilidades que tenés y usa términos del aviso solo
  cuando están respaldados. Un validador reconstruye el CV desde tu perfil maestro: empresas, cargos y fechas se
  copian del perfil; se descartan viñetas con cifras que no estén en tu perfil y habilidades que no tengas; las
  palabras clave no respaldadas se listan como "no incluidas". Formato ATS: una columna, sin tablas, gráficos ni íconos.
  Archivo: `CV_Apellido_Empresa_Puesto.docx` / `.pdf`, editable antes de descargar.
