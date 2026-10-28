#!/bin/bash
# JobHunter AI - inicio en Mac (doble clic). La primera vez puede tardar unos minutos.
cd "$(dirname "$0")"
echo "================================================"
echo "  JobHunter AI - iniciando en tu computadora"
echo "================================================"
if ! command -v python3 >/dev/null 2>&1; then
  echo "No se encontró Python. Instalalo desde https://www.python.org/downloads/ y volvé a abrir este archivo."
  read -p "Presioná Enter para cerrar..."; exit 1
fi
if [ ! -x ".venv/bin/python" ]; then
  echo "Primera vez: preparando el entorno..."
  python3 -m venv .venv || { echo "No se pudo crear el entorno."; read -p "Enter para cerrar..."; exit 1; }
fi
if [ ! -f ".venv/instalado.txt" ]; then
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -r requirements.txt || { echo "Falló la instalación. Revisá tu conexión."; read -p "Enter para cerrar..."; exit 1; }
  echo ok > .venv/instalado.txt
fi
[ -f .env ] || cp .env.example .env
export RUN_SCHEDULER_IN_WEB=true
echo
echo "JobHunter AI está funcionando en http://localhost:8000"
echo "NO cierres esta ventana mientras la uses. La búsqueda diaria se hace sola mientras esté abierta."
(sleep 4; open http://localhost:8000) &
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
