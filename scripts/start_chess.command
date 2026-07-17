#!/bin/bash
# Double-click this file (or click the Dock alias) to start the Chess Analyzer.
# Opens the browser automatically and shows a notification with the iPhone URL.

CHESS_DIR="$(dirname "$0")/.."
PORT=5051

cd "$CHESS_DIR"

# Kill any existing instance on this port
lsof -ti:$PORT | xargs kill -9 2>/dev/null
sleep 1

echo "Starting Chess Analyzer on port $PORT..."
CHESS_NO_RELOAD=1 python3 app.py > /tmp/chess_analyzer.log 2>&1 &
SERVER_PID=$!

# Wait until the server responds (up to 15 seconds)
echo -n "Waiting for server"
for i in $(seq 1 15); do
  if curl -s "http://localhost:$PORT/" > /dev/null 2>&1; then
    echo " ready."
    break
  fi
  echo -n "."
  sleep 1
done

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
