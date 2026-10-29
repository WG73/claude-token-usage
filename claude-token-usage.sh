#!/bin/sh
# Startet die Anzeige unter Linux/WSL im Hintergrund, ohne das Terminal zu belegen.
dir=$(dirname "$(readlink -f "$0")")
if ! python3 -c "import tkinter" 2>/dev/null; then
	echo "Tkinter fehlt - installieren mit: sudo apt install python3-tk" >&2
	exit 1
fi
nohup python3 "$dir/claude_usage.pyw" >/dev/null 2>&1 &
