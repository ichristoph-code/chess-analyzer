#!/bin/bash
# Double-click this file (or click the Dock alias) to start the Chess Analyzer.
# Opens the browser automatically and shows a notification with the iPhone URL.

CHESS_DIR="$(dirname "$0")/.."
PORT=5051

cd "$CHESS_DIR"

# Finder-launched Terminal windows can have a much smaller PATH than an
# interactive shell. Pick a Python that actually has the app dependencies
# instead of assuming the first `python3` will work.
PYTHON_BIN=""
for candidate in /opt/anaconda3/bin/python3 /opt/homebrew/bin/python3 /usr/bin/python3 "$(command -v python3)"; do
  if [ -x "$candidate" ] && "$candidate" -c "import flask, chess, anthropic, requests" 2>/dev/null; then
    PYTHON_BIN="$candidate"
    break
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  echo "Chess Analyzer could not find a Python installation with its dependencies."
  echo "Run: python3 -m pip install -r \"$CHESS_DIR/requirements.txt\""
  read -r -p "Press Return to close…"
  exit 1
fi

# Stop an older server gracefully before replacing it.
EXISTING_PIDS="$(lsof -ti:"$PORT" 2>/dev/null)"
if [ -n "$EXISTING_PIDS" ]; then
  kill $EXISTING_PIDS 2>/dev/null
  sleep 1
fi

echo "Starting Chess Analyzer on port $PORT..."
CHESS_NO_RELOAD=1 "$PYTHON_BIN" app.py > /tmp/chess_analyzer.log 2>&1 &
SERVER_PID=$!

# Wait until the server responds (up to 15 seconds)
echo -n "Waiting for server"
SERVER_READY=0
for i in $(seq 1 15); do
  if curl -s "http://localhost:$PORT/" > /dev/null 2>&1; then
    echo " ready."
    SERVER_READY=1
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    break
  fi
  echo -n "."
  sleep 1
done

if [ "$SERVER_READY" -ne 1 ]; then
  echo
  echo "Chess Analyzer did not start. Recent log:"
  tail -n 20 /tmp/chess_analyzer.log
  osascript -e 'display notification "The server could not start. Check the Terminal window for details." with title "Chess Analyzer"'
  read -r -p "Press Return to close…"
  exit 1
fi

# Open in Safari
open "http://localhost:$PORT/"

# Get LAN IP for iPhone access (tries Wi-Fi first, then ethernet)
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)"

# Show a macOS notification with the iPhone URL
if [ -n "$LAN_IP" ]; then
  IPHONE_URL="http://$LAN_IP:$PORT/"
  osascript -e "display notification \"iPhone: $IPHONE_URL\" with title \"Chess Analyzer\" subtitle \"Server running · PID $SERVER_PID\" sound name \"Glass\""
  echo "iPhone URL: $IPHONE_URL"
else
  osascript -e "display notification \"Open http://localhost:$PORT/ in your browser\" with title \"Chess Analyzer\" subtitle \"Server running · PID $SERVER_PID\" sound name \"Glass\""
fi

echo ""
echo "Server running (PID $SERVER_PID). Logs: /tmp/chess_analyzer.log"
echo "Close this window to stop the server."

# Keep the terminal open — closing it sends SIGHUP which stops the server
wait $SERVER_PID
