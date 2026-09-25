#!/bin/bash
# Usage:
#   ./run.sh              -> start the web UI (http://127.0.0.1:7860)
#   ./run.sh --ui --share -> web UI with a temporary public link
#   ./run.sh URL_OR_FILE [options] -> command-line mode (see: ./run.sh --help)
cd "$(dirname "$0")"
if [ -d venv ]; then
  NVIDIA_LIBS=$(find "$(pwd)"/venv/lib/python3*/site-packages/nvidia -name "lib" -type d 2>/dev/null | paste -sd ":" -)
  [ -n "$NVIDIA_LIBS" ] && export LD_LIBRARY_PATH="$NVIDIA_LIBS:$LD_LIBRARY_PATH"
  source venv/bin/activate
fi
if [ $# -eq 0 ] || [ "$1" = "--ui" ]; then
  [ "$1" = "--ui" ] && shift
  exec python app.py "$@"
fi
exec python main.py "$@"
