@echo off
setlocal enabledelayedexpansion

echo ============================================
echo  Claude-Howell Laptop Bootstrap
echo ============================================
echo.

:: Step 1: Create directory structure
echo [1/7] Creating directories...
mkdir C:\rje\dev 2>nul
mkdir C:\rje\tools 2>nul
mkdir C:\home\howell-persist 2>nul
mkdir C:\home\howell-persist\memory 2>nul
mkdir C:\home\howell-persist\bridge 2>nul
echo     Done.
echo.

:: Step 2: Clone repos
echo [2/7] Cloning howell-brain-deploy...
if exist "C:\rje\dev\howell-brain-deploy\.git" (
    echo     Already cloned, pulling latest...
    git -C C:\rje\dev\howell-brain-deploy pull origin main
) else (
    git clone https://github.com/ryanlack616/howell-brain-deploy.git C:\rje\dev\howell-brain-deploy
)
echo.

echo [3/7] Cloning claude-persist...
if exist "C:\rje\tools\claude-persist\.git" (
    echo     Already cloned, pulling latest...
    git -C C:\rje\tools\claude-persist pull
) else (
    gh repo clone ryanlack616/claude-persist C:\rje\tools\claude-persist
)
echo.

:: Step 3: Copy identity files to canonical location
echo [4/7] Copying identity files to C:\home\howell-persist\...
for %%f in (SOUL.md CONTEXT.md PROJECTS.md QUESTIONS.md) do (
    if exist "C:\rje\tools\claude-persist\%%f" (
        copy /Y "C:\rje\tools\claude-persist\%%f" "C:\home\howell-persist\%%f" >nul
        echo     Copied %%f
    ) else (
        echo     WARNING: %%f not found in claude-persist
    )
)
if exist "C:\rje\tools\claude-persist\memory" (
    xcopy /E /Y /Q "C:\rje\tools\claude-persist\memory\*" "C:\home\howell-persist\memory\" >nul
    echo     Copied memory/
)
if exist "C:\rje\tools\claude-persist\bridge\knowledge.json" (
    copy /Y "C:\rje\tools\claude-persist\bridge\knowledge.json" "C:\home\howell-persist\bridge\knowledge.json" >nul
    echo     Copied bridge\knowledge.json
)
echo.

:: Step 4: Create config.json
echo [5/7] Writing config.json...
(
    echo {
    echo   "persist_root": "C:\\home\\howell-persist",
    echo   "daemon_port": 7777,
    echo   "daemon_host": "127.0.0.1",
    echo   "max_recent_sessions": 10,
    echo   "heartbeat_interval_hours": 1,
    echo   "watcher_interval_seconds": 30,
    echo   "queue_interval_seconds": 10,
    echo   "moltbook_interval_seconds": 60
    echo }
) > "C:\rje\dev\howell-brain-deploy\config.json"
echo     Done.
echo.

:: Step 5: Install Python deps
echo [6/7] Installing Python dependencies...
cd /d C:\rje\dev\howell-brain-deploy
if exist requirements.txt (
    python -m pip install -r requirements.txt --quiet
    echo     Installed from requirements.txt
) else (
    echo     No requirements.txt found, skipping.
)
echo.

:: Step 6: Write mcp.json
echo [7/7] Writing VS Code mcp.json...
set APPDATA_PATH=%APPDATA%\Code\User
mkdir "%APPDATA_PATH%" 2>nul
if exist "%APPDATA_PATH%\mcp.json" (
    echo     mcp.json already exists — skipping to avoid overwrite.
    echo     Manually add howell-bridge entry:
    echo       "howell-bridge": { "type": "http", "url": "http://localhost:7777/mcp" }
) else (
    (
        echo {
        echo   "servers": {
        echo     "howell-bridge": {
        echo       "type": "http",
        echo       "url": "http://localhost:7777/mcp"
        echo     },
        echo     "memory": {
        echo       "type": "stdio",
        echo       "command": "npx",
        echo       "args": ["-y", "@modelcontextprotocol/server-memory"],
        echo       "env": {
        echo         "MEMORY_FILE_PATH": "C:\\home\\howell-persist\\memory.jsonl"
        echo       }
        echo     }
        echo   }
        echo }
    ) > "%APPDATA_PATH%\mcp.json"
    echo     Written to %APPDATA_PATH%\mcp.json
)
echo.

echo ============================================
echo  Bootstrap complete!
echo ============================================
echo.
echo  Next steps:
echo    1. Start daemon:  cd C:\rje\dev\howell-brain-deploy ^& python howell_daemon.py
echo    2. Verify health: curl http://localhost:7777/health
echo    3. Open VS Code and run a Copilot chat to trigger bootstrap
echo.
pause
