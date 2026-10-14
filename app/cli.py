"""Comandos de línea para cron externos (Render Cron Job, systemd timer, GitHub Actions, crontab).

  python -m app.cli run-daily            # ejecuta para todos los usuarios cuyo horario ya pasó y no corrieron hoy
  python -m app.cli run-now --email X    # fuerza una ejecución para un usuario
  python -m app.cli check-sources        # prueba la conexión de cada fuente configurada
"""
from __future__ import annotations

import argparse
import json
import logging

from .connectors import CONNECTORS, ConnectorError, NotConfigured, SearchQuery
from .db import SessionLocal, init_db
from .models import User
from .scheduler import tick
from .services.pipeline import default_search_config, get_search_config, run_for_user


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="jobhunter")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run-daily")
    p = sub.add_parser("run-now")
    p.add_argument("--email", required=True)
    p.add_argument("--no-email", action="store_true", help="no enviar el informe por correo")
    c = sub.add_parser("check-sources")
    c.add_argument("--email", help="usar la configuración de búsqueda de este usuario")
    c.add_argument("--term", default="analista")
    args = parser.parse_args()
    init_db()

    if args.cmd == "run-daily":
        tick()
    elif args.cmd == "run-now":
        db = SessionLocal()
        user = db.query(User).filter_by(email=args.email.lower()).first()
        if not user:
            raise SystemExit("Usuario no encontrado")
        run = run_for_user(db, user, trigger="cli", send_email=not args.no_email)
        print(json.dumps({"run": run.id, "status": run.status, "stats": run.stats, "errors": run.errors,
                          "sources": run.source_logs}, ensure_ascii=False, indent=2))
    elif args.cmd == "check-sources":
        db = SessionLocal()
        search = default_search_config()
        if args.email:
            user = db.query(User).filter_by(email=args.email.lower()).first()
            search = get_search_config(db, user.id) if user else search
        q = SearchQuery(terms=[args.term], location="Buenos Aires", max_age_days=3, modalities=search["modalities"], options=search)
        for key, conn in CONNECTORS.items():
            try:
                jobs = conn.fetch(q)
                print(f"[OK]    {conn.name}: {len(jobs)} avisos. Ej.: {jobs[0].title + ' — ' + jobs[0].url if jobs else '-'}")
            except NotConfigured as exc:
                print(f"[CONF]  {conn.name}: {exc}")
            except ConnectorError as exc:
                print(f"[ERROR] {conn.name}: {exc}")


if __name__ == "__main__":
    main()
