#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install --upgrade pip
"$ROOT/.venv/bin/python" -m pip install -e "$ROOT[dev]"
echo "Installed HotPepperPodcast. Try:"
echo "  $ROOT/scripts/run.sh doctor"
echo "  $ROOT/scripts/run.sh import-text --input $ROOT/examples/hello.txt --output /tmp/hello.yaml"
