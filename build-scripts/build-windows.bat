@echo off
REM Ibis Publisher — Windows build script
REM Creates IbisPublisher.exe in dist\
REM Run from: ibis-publisher\companion-app\

echo 🦢 Ibis Publisher — Windows Build
echo ==================================

REM Install deps
echo Installing Python dependencies...
pip install -r requirements.txt

REM Copy schema
echo Copying shared files...
copy ..\shared\schema.sql .\schema.sql

REM Build
echo Building .exe...
pyinstaller ^
    --name "IbisPublisher" ^
    --windowed ^
    --onefile ^
    --add-data "schema.sql;." ^
    --hidden-import "plyer.platforms.win.notification" ^
    app.py

echo Done: dist\IbisPublisher.exe
