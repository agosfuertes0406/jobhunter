@echo off
chcp 65001 >nul
title JobHunter AI
cd /d "%~dp0"
echo ================================================
echo   JobHunter AI - iniciando en tu computadora
echo ================================================

set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY (where python >nul 2>nul && set PY=python)
if not defined PY (
  echo.
  echo No se encontro Python. Instalalo desde https://www.python.org/downloads/
  echo IMPORTANTE: en el instalador marca la casilla "Add python.exe to PATH".
  echo Despues volve a hacer doble clic en este archivo.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Primera vez: preparando el entorno (puede tardar unos minutos^)...
  %PY% -m venv .venv || (echo No se pudo crear el entorno. & pause & exit /b 1)
)
if not exist ".venv\instalado.txt" (
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo Fallo la instalacion. Revisa tu conexion a internet. & pause & exit /b 1)
  echo ok> ".venv\instalado.txt"
)
if not exist ".env" copy ".env.example" ".env" >nul

set RUN_SCHEDULER_IN_WEB=true
echo.
echo JobHunter AI esta funcionando en http://localhost:8000
echo NO cierres esta ventana mientras la uses. La busqueda diaria se hace sola mientras este abierta.
echo Para detenerla, cerra esta ventana.
echo.
start "" /b cmd /c "timeout /t 4 >nul & start http://localhost:8000"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
