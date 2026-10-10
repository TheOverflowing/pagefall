#!/bin/zsh
set -e
cd "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
  .venv/bin/python -m playwright install chromium
fi
if [[ ! -x .build/vision-ocr || native/vision_ocr.swift -nt .build/vision-ocr ]]; then
  mkdir -p .build
  swiftc -O native/vision_ocr.swift -o .build/vision-ocr
fi
if [[ ! -d 拾页.app || native/App.swift -nt 拾页.app/Contents/MacOS/Novelist ]]; then
  bash scripts/build_app.sh
fi
open 拾页.app
