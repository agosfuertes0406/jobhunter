"""Programación de la ejecución diaria (independiente del navegador).

Cada 5 minutos se revisa, para cada usuario, si ya pasó su horario configurado (hora de Buenos Aires)
y todavía no hubo una ejecución automática hoy. Así se recupera solo si el servidor estuvo apagado
a la hora pactada, y los cambios de horario se aplican sin reiniciar."""
from __future__ import annotations

import logging
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler

from . import config
from .db import SessionLocal, init_db
from .models import Run, User
from .services.pipeline import local_now, run_for_user

log = logging.getLogger("jobhunter.scheduler")
_lock_handle = None


def due_users() -> list[int]:
    now = local_now()
    today, hhmm = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
    db = SessionLocal()
    try:
        ids = []
        for user in db.query(User).all():
            s = user.get_settings()
            if not s.get("run_enabled", True) or hhmm < s.get("run_time", "08:00"):
                continue
            done = db.query(Run.id).filter(Run.user_id == user.id, Run.local_date == today,
                                           Run.trigger == "scheduler").first()
            if not done:
                ids.append(user.id)
        return ids
    finally:
        db.close()


def tick() -> None:
    for uid in due_users():
        db = SessionLocal()
        try:
            user = db.get(User, uid)
            log.info("Ejecución diaria para usuario %s", uid)
            run = run_for_user(db, user, trigger="scheduler")
            log.info("Ejecución %s terminada: %s %s", run.id, run.status, run.stats)
        except Exception:  # noqa: BLE001
            log.exception("Fallo la ejecución diaria del usuario %s", uid)
        finally:
            db.close()


def _acquire_lock() -> bool:
    """Evita dos schedulers simultáneos sobre la misma base (p. ej. varios workers web)."""
    global _lock_handle
    path = Path(config.DATA_DIR) / "scheduler.lock"
    _lock_handle = open(path, "a+")
    try:
        try:
            import fcntl  # Linux / macOS

            fcntl.flock(_lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            import msvcrt  # Windows

            _lock_handle.seek(0)
            msvcrt.locking(_lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
        return True
    except OSError:
        return False


def start_background() -> BackgroundScheduler | None:
    if not _acquire_lock():
        log.info("Otro proceso ya ejecuta el scheduler.")
        return None
    sched = BackgroundScheduler(timezone=config.TIMEZONE)
    sched.add_job(tick, "interval", minutes=5, id="daily-check", max_instances=1, coalesce=True)
    sched.start()
    log.info("Scheduler iniciado en proceso web (revisión cada 5 minutos).")
    return sched


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_db()
    if not _acquire_lock():
        raise SystemExit("Ya hay un scheduler activo para esta base de datos.")
    sched = BlockingScheduler(timezone=config.TIMEZONE)
    sched.add_job(tick, "interval", minutes=5, id="daily-check", max_instances=1, coalesce=True)
    log.info("Worker JobHunter AI iniciado. Zona horaria %s. Revisión cada 5 minutos.", config.TIMEZONE)
    tick()
    sched.start()


if __name__ == "__main__":
    main()
