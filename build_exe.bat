@echo off
REM Builds a single-file Windows app: dist\IFC Merge.exe
REM Run once on a Windows PC with Python 3.10+ installed.

python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller

python -m PyInstaller --noconfirm --onefile --windowed --name "IFC Merge" ^
  --collect-all ifcopenshell ^
  --collect-all ifcpatch ^
  --collect-all tkinterdnd2 ^
  --hidden-import numpy ^
  ifc_merge_app.py

echo.
echo Done. The app is in the dist folder: "dist\IFC Merge.exe"
pause
