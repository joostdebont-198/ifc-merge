#!/usr/bin/env bash
# Builds "dist/IFC Merge.app" on macOS. Needs Python 3.10+ with Tk (python.org installer or `brew install python-tk`).
set -e
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt pyinstaller
python3 -m PyInstaller --noconfirm --onefile --windowed --name "IFC Merge" \
  --collect-all ifcopenshell --collect-all ifcpatch --collect-all tkinterdnd2 \
  --hidden-import numpy ifc_merge_app.py
echo "Done: dist/IFC Merge.app"
