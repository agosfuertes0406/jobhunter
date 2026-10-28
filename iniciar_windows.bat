@echo off
rem JobHunter AI - inicio en Windows (doble clic).
rem Se vuelve a abrir dentro de una ventana que NO se cierra, para poder leer cualquier mensaje.
if /i "%~1"=="--dentro" goto main
start "JobHunter AI" cmd /k call "%~f0" --dentro
exit /b

:main
chcp 65001 >nul
cd /d "%~dp0"
set "LOG=%~dp0inicio_log.txt"
echo ================================================
echo   JobHunter AI - iniciando en tu computadora
echo ================================================
echo Inicio %date% %time% > "%LOG%"

set "PY="
py -3 --version >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto python_ok
python --version >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto python_ok
echo.
echo [ERROR] No se encontro Python.
echo Instalalo desde https://www.python.org/downloads/
echo En el instalador marca la casilla "Add python.exe to PATH".
echo Despues cerra esta ventana y volve a hacer doble clic en iniciar_windows.
echo [ERROR] Python no encontrado >> "%LOG%"
goto fin

:python_ok
%PY% --version
%PY% --version >> "%LOG%" 2>&1

if exist ".venv\Scripts\python.exe" goto venv_ok
echo Primera vez: preparando el entorno, puede tardar unos minutos...
%PY% -m venv .venv >> "%LOG%" 2>&1
if errorlevel 1 goto error_venv

:venv_ok
if exist ".venv\instalado.txt" goto instalado
echo Instalando componentes (solo la primera vez, puede tardar 5 minutos)...
".venv\Scripts\python.exe" -m pip install --upgrade pip >> "%LOG%" 2>&1
".venv\Scripts\python.exe" -m pip install -r requirements.txt >> "%LOG%" 2>&1
if errorlevel 1 goto error_pip
echo ok> ".venv\instalado.txt"

:instalado
if not exist ".env" copy ".env.example" ".env" >nul
set "RUN_SCHEDULER_IN_WEB=true"
echo.
echo JobHunter AI esta funcionando: abri http://localhost:8000 en tu navegador.
echo NO cierres esta ventana mientras la uses. Para detener la app, cerra esta ventana.
echo.
start "" http://localhost:8000
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
echo.
echo [AVISO] La app se detuvo. Si fue un error, manda el archivo inicio_log.txt de esta carpeta.
goto fin

:error_venv
echo.
echo [ERROR] No se pudo crear el entorno de Python. Detalle en inicio_log.txt
goto fin

:error_pip
echo.
echo [ERROR] Fallo la instalacion de componentes. Revisa tu conexion a internet.
echo Manda el archivo inicio_log.txt de esta carpeta para ver el detalle.
rmdir /s /q .venv >nul 2>&1
goto fin

:fin
echo.
echo Podes cerrar esta ventana.
