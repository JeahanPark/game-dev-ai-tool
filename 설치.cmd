@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal
set "GAF_SETUP_OPTIONS="
set "GAF_SETUP_ASK="
for %%A in (%*) do (
    if /i "%%~A"=="codex" set "GAF_SETUP_OPTIONS=--codex"
    if /i "%%~A"=="ask" set "GAF_SETUP_ASK=--ask"
)
echo Game Dev AI Tool 을 준비한다. 처음에는 시간이 조금 걸린다.
py -3 -c "import sys; print('GAF_PYTHON_OK' if sys.version_info >= (3, 11) else 'OLD')" 2>nul | findstr /x /c:"GAF_PYTHON_OK" >nul
if not errorlevel 1 goto python_py
python -c "import sys; print('GAF_PYTHON_OK' if sys.version_info >= (3, 11) else 'OLD')" 2>nul | findstr /x /c:"GAF_PYTHON_OK" >nul
if not errorlevel 1 goto python_path
echo 파이썬이 없다. 아무 키나 누르면 설치한다(창을 닫으면 그만둔다)
call :wait_key
where winget >nul 2>&1
if errorlevel 1 goto python_download
winget install -e --id Python.Python.3.13 --scope user --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto python_download
set "GAF_SETUP_PYTHON=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
set "GAF_SETUP_PY_ARG="
"%GAF_SETUP_PYTHON%" -c "import sys; print('GAF_PYTHON_OK' if sys.version_info >= (3, 11) else 'OLD')" 2>nul | findstr /x /c:"GAF_PYTHON_OK" >nul
if errorlevel 1 goto python_download
goto venv

:python_py
set "GAF_SETUP_PYTHON=py"
set "GAF_SETUP_PY_ARG=-3"
goto venv

:python_path
set "GAF_SETUP_PYTHON=python"
set "GAF_SETUP_PY_ARG="
goto venv

:venv
if exist ".venv\Scripts\python.exe" goto packages
echo 파이썬을 쓸 자리를 만든다.
"%GAF_SETUP_PYTHON%" %GAF_SETUP_PY_ARG% -m venv .venv
if errorlevel 1 goto venv_failed

:packages
echo AI를 쓰는 부품을 넣는다.
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto packages_failed

:setup
".venv\Scripts\python.exe" -X utf8 -m gaf setup %GAF_SETUP_OPTIONS% %GAF_SETUP_ASK%
if errorlevel 1 goto setup_failed
echo 이 창은 닫아도 된다
pause
exit /b 0

:python_download
start "" "https://www.python.org/downloads/"
echo 파이썬을 자동으로 설치하지 못했다. 설치한 뒤 이 파일을 다시 더블클릭한다
pause
exit /b 1

:venv_failed
echo 파이썬을 쓸 자리를 만들지 못했다. 이 폴더에 쓸 수 있는지 확인한다.
pause
exit /b 1

:packages_failed
echo AI 부품을 넣지 못했다. 인터넷 연결을 확인한 뒤 이 파일을 다시 더블클릭한다.
pause
exit /b 1

:setup_failed
echo 처음 준비를 마치지 못했다. 위의 안내를 보고 이 파일을 다시 더블클릭한다.
pause
exit /b 1

:wait_key
pause >nul
exit /b 0
