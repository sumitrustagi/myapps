@echo off
REM ============================================================
REM  Multi-Platform Bulk Call Tester — EXE Builder
REM  Builds CUCM + MS Teams + Webex Calling tester
REM ============================================================
title Multi-Platform Call Tester — Build

echo.
echo  ============================================================
echo   MULTI-PLATFORM CALL TESTER — EXE Builder
echo  ============================================================
echo.

python --version >nul 2>&1 || (echo [ERROR] Python not found & pause & exit /b 1)

if not exist "venv\" (
    echo [INFO] Creating virtual environment...
    python -m venv venv
)
call venv\Scripts\activate.bat

echo [INFO] Installing dependencies...
pip install --upgrade pip -q
pip install msal requests openpyxl pyinstaller pyVoIP -q

echo.
echo [INFO] Building EXE...
echo.

pyinstaller ^
  --onefile ^
  --windowed ^
  --name "MultiPlatform_Call_Tester" ^
  --add-data "teams_engine.py;." ^
  --add-data "webex_engine.py;." ^
  --add-data "report_engine.py;." ^
  --add-data "sample_numbers.csv;." ^
  --hidden-import "msal" ^
  --hidden-import "msal.application" ^
  --hidden-import "requests" ^
  --hidden-import "openpyxl" ^
  --hidden-import "openpyxl.styles" ^
  --hidden-import "tkinter" ^
  --hidden-import "tkinter.ttk" ^
  --hidden-import "tkinter.filedialog" ^
  --hidden-import "tkinter.messagebox" ^
  --hidden-import "tkinter.scrolledtext" ^
  --collect-all msal ^
  --noconfirm ^
  multi_caller.py

echo.
if exist "dist\MultiPlatform_Call_Tester.exe" (
    echo  ============================================================
    echo   BUILD SUCCESSFUL!
    echo   EXE: dist\MultiPlatform_Call_Tester.exe
    echo  ============================================================
) else (
    echo [ERROR] Build failed. Check output above.
)
pause
