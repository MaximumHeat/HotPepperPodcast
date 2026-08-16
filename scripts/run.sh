#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${ROOT}/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="python3"
if [ "${1:-}" = "web" ]; then
  shift
  PYTHONPATH="${ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}" exec "$PYTHON" -m hotpepperpodcast.web_cli "$@"
fi
PYTHONPATH="${ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}" exec "$PYTHON" -m hotpepperpodcast.cli "$@"
