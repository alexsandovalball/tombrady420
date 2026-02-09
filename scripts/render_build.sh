#!/usr/bin/env bash
set -euo pipefail

echo "Starting Render build script"

if [ ! -f requirements.txt ]; then
  echo "ERROR: requirements.txt not found in repository root."
  echo "Render attempted to run 'pip install -r requirements.txt' and failed with 'No such file or directory'."
  echo "Ensure requirements.txt is committed to the default branch or update Render settings if you build from a subdirectory."
  exit 1
fi

python -m pip install --upgrade pip
pip install -r requirements.txt

echo "Dependencies installed successfully"
