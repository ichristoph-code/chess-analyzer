#!/bin/bash
# Double-click this file to start the Chess Analyzer and open it in the browser.

CHESS_DIR="$(dirname "$0")/.."
PORT=5051

cd "$CHESS_DIR"

# Kill any existing instance on port
lsof -ti:$PORT | xargs kill -9 2>/dev/null
sleep 1

echo "Starting Chess Analyzer on port $PORT..."
python3 app.py &
SERVER_PID=$!

# Wait until the server responds (up to 10 seconds)
for i in $(seq 1 10); do
  if curl -s "http://localhost:$PORT/" > /dev/null 2>&1; then
    break
  fi
  sleep 1
done

# Open the browser
open "http://localhost:$PORT/"

LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)"
if [ -n "$LAN_IP" ]; then
  echo ""
  echo "iPhone URL: http://$LAN_IP:$PORT/"
  echo "Open that address in Safari while your iPhone is on the same Wi-Fi."
  echo "Use Share → Add to Home Screen to install it like an app."
fi

echo "Chess Analyzer running (PID $SERVER_PID). Close this window to stop the server."

# Keep the terminal open and show server logs
wait $SERVER_PID
