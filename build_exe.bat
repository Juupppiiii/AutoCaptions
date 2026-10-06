@echo off
REM Run this on Windows, inside the taglishcap folder, with Python 3.10+ installed.
REM It installs dependencies and packages the app into dist\TaglishCap\TaglishCap.exe

echo === Installing Python dependencies (first run will download ~2-3 GB, includes torch) ===
pip install -r requirements.txt

echo === Building TaglishCap.exe with PyInstaller ===
pyinstaller --noconfirm --windowed --name "TaglishCap" ^
  --collect-all faster_whisper ^
  --collect-all ctranslate2 ^
  --collect-all fontTools ^
  main.py

echo.
echo === Done ===
echo Your app is at: dist\TaglishCap\TaglishCap.exe
echo You can zip the whole "dist\TaglishCap" folder to share/move it - it must stay together.
pause
