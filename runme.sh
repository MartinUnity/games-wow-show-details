#!/usr/bin/env bash

# Supervisor script to start/stop/status three project processes:
# - extraction.py
# - scripts/save-game-cleanup.py
# - show-data.py (streamlit)
#
# Usage: ./runme.sh start|stop|restart|status [name]
# where [name] is one of: extraction, cleanup, streamlit

set -euo pipefail

# Default BASE_DIR is the script's directory (where runme.sh lives)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$SCRIPT_DIR"
PID_DIR="$BASE_DIR/runme.pids"
LOG_DIR="$BASE_DIR/runme.logs"
mkdir -p "$PID_DIR" "$LOG_DIR"

# Use venv python if available
VENV_PY="$BASE_DIR/.venv/bin/python"
if [[ -x "$VENV_PY" ]]; then
	PYTHON="$VENV_PY"
else
	PYTHON="python"
fi

# Allow overriding the BASE_DIR using (in order of precedence):
# 1) Environment variable TERRA_INV_DIR
# 2) The `BASE_DIR` value from config.py (read via the selected Python)
# 3) The script's directory (fallback already set)
if [[ -n "${TERRA_INV_DIR:-}" ]]; then
	BASE_DIR="$TERRA_INV_DIR"
else
	# Try to read config.BASE_DIR using the chosen Python interpreter
	if command -v "$PYTHON" >/dev/null 2>&1; then
		cfg_base=$($PYTHON - <<PYCODE 2>/dev/null
import sys
try:
	import config
	print(config.BASE_DIR)
except Exception:
	sys.exit(1)
PYCODE
		) || true
		if [[ -n "$cfg_base" ]]; then
			BASE_DIR="$cfg_base"
		fi
	fi
fi

# Ensure BASE_DIR is absolute and exists; fall back to script dir if not
if [[ ! -d "$BASE_DIR" ]]; then
	BASE_DIR="$SCRIPT_DIR"
fi

declare -A CMD
declare -A PIDFILE
declare -A LOGFILE

# Start the parser and the Streamlit UI only for this repo
CMD[parser]="cd \"$BASE_DIR\" && PYTHONPATH=\"$BASE_DIR\" $PYTHON $BASE_DIR/wow-parser.py"
CMD[streamlit]="cd \"$BASE_DIR\" && PYTHONPATH=\"$BASE_DIR\" $PYTHON -m streamlit run $BASE_DIR/streamlit_app.py --server.headless true"

PIDFILE[parser]="$PID_DIR/parser.pid"
PIDFILE[streamlit]="$PID_DIR/streamlit.pid"

LOGFILE[parser]="$LOG_DIR/parser.log"
LOGFILE[streamlit]="$LOG_DIR/streamlit.log"

is_running() {
	local pidfile="$1"
	if [[ -f "$pidfile" ]]; then
		local pid
		pid=$(<"$pidfile")
		if kill -0 "$pid" 2>/dev/null; then
			echo "$pid"
			return 0
		else
			return 1
		fi
	fi
	return 1
}

start_one() {
	local name=$1
	local cmd=${CMD[$name]}
	local pidfile=${PIDFILE[$name]}
	local logfile=${LOGFILE[$name]}

	if pid=$(is_running "$pidfile"); then
		echo "$name already running (pid $pid)"
		return 0
	fi

	echo "Starting $name..."
	nohup bash -lc "$cmd" >"$logfile" 2>&1 &
	echo $! > "$pidfile"
	sleep 0.1
	pid=$(<"$pidfile")
	echo "$name started (pid $pid) - log: $logfile"
}

stop_one() {
	local name=$1
	local pidfile=${PIDFILE[$name]}

	if pid=$(is_running "$pidfile"); then
		echo "Stopping $name (pid $pid)..."
		kill "$pid" || true
		# wait up to 5 seconds
		for i in {1..10}; do
			if ! kill -0 "$pid" 2>/dev/null; then
				break
			fi
			sleep 0.5
		done
		if kill -0 "$pid" 2>/dev/null; then
			echo "Force killing $pid"
			kill -9 "$pid" || true
		fi
		rm -f "$pidfile"
		echo "$name stopped"
	else
		echo "$name not running"
	fi
}

status_one() {
	local name=$1
	local pidfile=${PIDFILE[$name]}
	if pid=$(is_running "$pidfile"); then
		echo "$name running (pid $pid)"
	else
		echo "$name stopped"
	fi
}

start_all() {
	for n in "parser" "streamlit"; do
		start_one "$n"
	done
}

stop_all() {
	for n in "streamlit" "parser"; do
		stop_one "$n"
	done
}

status_all() {
	for n in "parser" "streamlit"; do
		status_one "$n"
	done
}

usage() {
	cat <<EOF
Usage: $0 <command> [name]
Commands:
	start [name]    Start a process or all if name omitted
	stop [name]     Stop a process or all if name omitted
	restart [name]  Restart a process or all if name omitted
	status [name]   Show status of a process or all if name omitted
	logs [name]     Tail the log for a process (requires name)
Names: parser, streamlit
EOF
}

case ${1:-} in
	start)
		if [[ -n ${2:-} ]]; then
			start_one "$2"
		else
			start_all
		fi
		;;
	stop)
		if [[ -n ${2:-} ]]; then
			stop_one "$2"
		else
			stop_all
		fi
		;;
	restart)
		if [[ -n ${2:-} ]]; then
			stop_one "$2"
			start_one "$2"
		else
			stop_all
			start_all
		fi
		;;
	status)
		if [[ -n ${2:-} ]]; then
			status_one "$2"
		else
			status_all
		fi
		;;
	logs)
		if [[ -n ${2:-} ]]; then
			tail -f "${LOGFILE[$2]}"
		else
			echo "Please provide a name to tail logs for"
			usage
			exit 2
		fi
		;;
	*)
		usage
		exit 2
		;;
esac
