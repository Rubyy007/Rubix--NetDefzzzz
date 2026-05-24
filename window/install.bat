@echo off
setlocal EnableDelayedExpansion

:: ============================================================================
::  RUBIX Network Defense Engine — Windows Installer
::  Run this file as Administrator
::  Double-click → Right-click → Run as Administrator
:: ============================================================================

:: Check Administrator
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  +------------------------------------------------------------------+
    echo  ^|          RUBIX INSTALLER — ADMINISTRATOR REQUIRED               ^|
    echo  +------------------------------------------------------------------+
    echo  ^|                                                                  ^|
    echo  ^|  Right-click install.bat and choose "Run as Administrator"       ^|
    echo  ^|                                                                  ^|
    echo  +------------------------------------------------------------------+
    echo.
    pause
    exit /b 1
)

cls
echo.
echo  +------------------------------------------------------------------+
echo  ^|         RUBIX Network Defense Engine — Installer v1.0           ^|
echo  +------------------------------------------------------------------+
echo.

:: ── Check Npcap ──────────────────────────────────────────────────────────────
echo  [*] Checking Npcap...
set NPCAP_FOUND=0

if exist "C:\Windows\System32\Npcap\wpcap.dll"  set NPCAP_FOUND=1
if exist "C:\Windows\System32\wpcap.dll"         set NPCAP_FOUND=1

if %NPCAP_FOUND%==0 (
    echo.
    echo  +------------------------------------------------------------------+
    echo  ^|                  NPCAP NOT INSTALLED                            ^|
    echo  +------------------------------------------------------------------+
    echo  ^|                                                                  ^|
    echo  ^|  RUBIX requires Npcap to capture network packets.               ^|
    echo  ^|                                                                  ^|
    echo  ^|  1. Go to:  https://npcap.com/#download                         ^|
    echo  ^|  2. Download the Npcap installer                                ^|
    echo  ^|  3. Run it as Administrator                                      ^|
    echo  ^|  4. TICK: Install Npcap in WinPcap API-compatible Mode          ^|
    echo  ^|  5. Reboot if prompted                                           ^|
    echo  ^|  6. Run this installer again                                     ^|
    echo  ^|                                                                  ^|
    echo  +------------------------------------------------------------------+
    echo.
    echo  Opening Npcap download page in your browser...
    start https://npcap.com/#download
    echo.
    pause
    exit /b 1
)
echo  [OK] Npcap found

:: ── Check required files ──────────────────────────────────────────────────────
echo  [*] Checking required files...
set MISSING=0

if not exist "%~dp0rubix.exe" (
    echo  [!] MISSING: rubix.exe
    set MISSING=1
)
if not exist "%~dp0rubix-cli.exe" (
    echo  [!] MISSING: rubix-cli.exe
    set MISSING=1
)
if not exist "%~dp0configs\rubix.windows.yaml" (
    echo  [!] MISSING: configs\rubix.windows.yaml
    set MISSING=1
)
if not exist "%~dp0configs\rules.yaml" (
    echo  [!] MISSING: configs\rules.yaml
    set MISSING=1
)

if %MISSING%==1 (
    echo.
    echo  +------------------------------------------------------------------+
    echo  ^|  Required files are missing from the installer folder.          ^|
    echo  ^|  Make sure your folder looks like this:                         ^|
    echo  ^|                                                                  ^|
    echo  ^|    rubix-deploy\                                                 ^|
    echo  ^|      install.bat          ^<-- this file                         ^|
    echo  ^|      rubix.exe                                                   ^|
    echo  ^|      rubix-cli.exe                                               ^|
    echo  ^|      configs\                                                    ^|
    echo  ^|        rubix.windows.yaml                                        ^|
    echo  ^|        rubix.common.yaml                                         ^|
    echo  ^|        rules.yaml                                                ^|
    echo  ^|                                                                  ^|
    echo  +------------------------------------------------------------------+
    echo.
    pause
    exit /b 1
)
echo  [OK] All required files present

:: ── Create install directories ────────────────────────────────────────────────
echo  [*] Creating directories...

set INSTALL_DIR=C:\Program Files\RUBIX
set CONFIG_DIR=C:\Program Files\RUBIX\configs
set DATA_DIR=C:\ProgramData\rubix
set LOG_DIR=C:\ProgramData\rubix\logs

mkdir "%INSTALL_DIR%"  2>nul
mkdir "%CONFIG_DIR%"   2>nul
mkdir "%DATA_DIR%"     2>nul
mkdir "%LOG_DIR%"      2>nul

echo  [OK] Directories created

:: ── Copy binaries ─────────────────────────────────────────────────────────────
echo  [*] Installing binaries...

copy /Y "%~dp0rubix.exe"     "%INSTALL_DIR%\rubix.exe"     >nul
copy /Y "%~dp0rubix-cli.exe" "%INSTALL_DIR%\rubix-cli.exe" >nul

echo  [OK] Binaries installed to %INSTALL_DIR%

:: ── Copy configs (never overwrite existing — preserve user edits) ─────────────
echo  [*] Installing config files...

if not exist "%CONFIG_DIR%\rubix.windows.yaml" (
    copy /Y "%~dp0configs\rubix.windows.yaml" "%CONFIG_DIR%\rubix.windows.yaml" >nul
    echo  [OK] rubix.windows.yaml installed
) else (
    echo  [--] rubix.windows.yaml already exists — keeping your version
)

if not exist "%CONFIG_DIR%\rubix.common.yaml" (
    if exist "%~dp0configs\rubix.common.yaml" (
        copy /Y "%~dp0configs\rubix.common.yaml" "%CONFIG_DIR%\rubix.common.yaml" >nul
        echo  [OK] rubix.common.yaml installed
    )
) else (
    echo  [--] rubix.common.yaml already exists — keeping your version
)

if not exist "%CONFIG_DIR%\rules.yaml" (
    copy /Y "%~dp0configs\rules.yaml" "%CONFIG_DIR%\rules.yaml" >nul
    echo  [OK] rules.yaml installed
) else (
    echo  [--] rules.yaml already exists — keeping your version
)

:: ── Add to system PATH ────────────────────────────────────────────────────────
echo  [*] Adding RUBIX to system PATH...

:: Read current PATH from registry to avoid truncation
for /f "tokens=2*" %%A in (
    'reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v PATH 2^>nul'
) do set "CURRENT_PATH=%%B"

:: Only add if not already in PATH
echo !CURRENT_PATH! | find /i "%INSTALL_DIR%" >nul
if %errorlevel% neq 0 (
    reg add "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" ^
        /v PATH /t REG_EXPAND_SZ ^
        /d "!CURRENT_PATH!;%INSTALL_DIR%" /f >nul
    echo  [OK] Added to system PATH
) else (
    echo  [--] Already in system PATH
)

:: ── Stop existing service if running ─────────────────────────────────────────
sc query RUBIX >nul 2>&1
if %errorlevel%==0 (
    echo  [*] Stopping existing RUBIX service...
    sc stop RUBIX >nul 2>&1
    timeout /t 2 /nobreak >nul
    sc delete RUBIX >nul 2>&1
    echo  [OK] Old service removed
)

:: ── Install Windows service ───────────────────────────────────────────────────
echo  [*] Installing Windows service...

sc create RUBIX ^
    binPath= "\"%INSTALL_DIR%\rubix.exe\"" ^
    DisplayName= "RUBIX Network Defense Engine" ^
    description= "Real-time network packet capture, threat detection and blocking" ^
    start= auto ^
    obj= LocalSystem >nul

if %errorlevel% neq 0 (
    echo  [!] Warning: Service installation failed
    echo      You can still run rubix.exe manually as Administrator
) else (
    echo  [OK] Windows service installed
)

:: Set service description separately (more reliable)
sc description RUBIX "RUBIX Network Defense Engine — real-time threat detection and blocking" >nul 2>&1

:: ── Create Desktop shortcuts ──────────────────────────────────────────────────
echo  [*] Creating shortcuts...

:: Create a VBScript to make the shortcut (no external tools needed)
set VBSCRIPT=%TEMP%\rubix_shortcut.vbs

echo Set oWS = WScript.CreateObject("WScript.Shell") > "%VBSCRIPT%"
echo sLinkFile = oWS.SpecialFolders("Desktop") ^& "\RUBIX Monitor.lnk" >> "%VBSCRIPT%"
echo Set oLink = oWS.CreateShortcut(sLinkFile) >> "%VBSCRIPT%"
echo oLink.TargetPath = "cmd.exe" >> "%VBSCRIPT%"
echo oLink.Arguments = "/K rubix-cli monitor" >> "%VBSCRIPT%"
echo oLink.WorkingDirectory = "%INSTALL_DIR%" >> "%VBSCRIPT%"
echo oLink.Description = "RUBIX Live Monitor" >> "%VBSCRIPT%"
echo oLink.Save >> "%VBSCRIPT%"

cscript //nologo "%VBSCRIPT%" >nul 2>&1
del "%VBSCRIPT%" >nul 2>&1

echo  [OK] Desktop shortcut created: "RUBIX Monitor"

:: ── Start the service ─────────────────────────────────────────────────────────
echo  [*] Starting RUBIX service...
sc start RUBIX >nul 2>&1
timeout /t 2 /nobreak >nul

sc query RUBIX | find "RUNNING" >nul 2>&1
if %errorlevel%==0 (
    echo  [OK] RUBIX service is RUNNING
) else (
    echo  [!] Service did not start automatically
    echo      Try: sc start RUBIX
    echo      Or run manually: "%INSTALL_DIR%\rubix.exe"
)

:: ── Done ──────────────────────────────────────────────────────────────────────
echo.
echo  +------------------------------------------------------------------+
echo  ^|                  RUBIX INSTALLED SUCCESSFULLY                   ^|
echo  +------------------------------------------------------------------+
echo  ^|                                                                  ^|
echo  ^|  Install location:  C:\Program Files\RUBIX\                     ^|
echo  ^|  Config files:      C:\Program Files\RUBIX\configs\             ^|
echo  ^|  Log files:         C:\ProgramData\rubix\logs\                  ^|
echo  ^|                                                                  ^|
echo  ^|  COMMANDS (open any terminal):                                   ^|
echo  ^|                                                                  ^|
echo  ^|    rubix-cli monitor      Live terminal dashboard                ^|
echo  ^|    rubix-cli status       Daemon status + uptime                 ^|
echo  ^|    rubix-cli logs         Stream security events                 ^|
echo  ^|    rubix-cli list         List blocked IPs                       ^|
echo  ^|    rubix-cli block 1.2.3.4   Block an IP                        ^|
echo  ^|    rubix-cli unblock 1.2.3.4 Unblock an IP                      ^|
echo  ^|    rubix-cli rules        List policy rules                      ^|
echo  ^|    rubix-cli reload       Reload rules without restart           ^|
echo  ^|                                                                  ^|
echo  ^|  WEB DASHBOARD:                                                  ^|
echo  ^|    http://127.0.0.1:7878                                         ^|
echo  ^|    Token printed in daemon terminal on startup                   ^|
echo  ^|                                                                  ^|
echo  ^|  SERVICE CONTROL:                                                ^|
echo  ^|    sc start RUBIX         Start the service                      ^|
echo  ^|    sc stop  RUBIX         Stop the service                       ^|
echo  ^|    sc query RUBIX         Check service status                   ^|
echo  ^|                                                                  ^|
echo  ^|  EDIT CONFIG:                                                    ^|
echo  ^|    notepad "C:\Program Files\RUBIX\configs\rubix.windows.yaml"  ^|
echo  ^|    notepad "C:\Program Files\RUBIX\configs\rules.yaml"          ^|
echo  ^|                                                                  ^|
echo  ^|  UNINSTALL:                                                      ^|
echo  ^|    Run uninstall.bat as Administrator                            ^|
echo  ^|                                                                  ^|
echo  +------------------------------------------------------------------+
echo.

:: Refresh PATH in current session
set PATH=%PATH%;%INSTALL_DIR%

echo  Press any key to close this window...
pause >nul
exit /b 0
