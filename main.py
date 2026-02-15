import asyncio
import websockets
import json
from datetime import datetime, timedelta
import uuid
import os
from base64 import b64decode
import http.server
import socketserver
from threading import Thread

# ───────────────────────────────────────────────
# CONFIGURATION
# ───────────────────────────────────────────────

CHAT_PASS = os.environ.get("CHAT_PASS", "default-insecure-123-change-me")
ADMIN_PASSWORD = os.environ.get("CHAT_ADMIN_PASS", "admin-secret-2025")

# Use Render-provided PORT or fallback to 10000 for local testing
PORT = int(os.environ.get("PORT", 10000))
HOST = "0.0.0.0"

# ───────────────────────────────────────────────
# Simple HTTP health-check server (for Render probes)
# ───────────────────────────────────────────────

class HealthHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path in ('/', '/health', '/healthz', '/ready'):
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK\n")
        else:
            self.send_response(404)
            self.end_headers()

    def do_HEAD(self):
        self.do_GET()  # HEAD should return same headers, no body

    def log_message(self, format, *args):
        # Silence health check logs (optional - comment out to see them)
        return


def run_http_health_server():
    with socketserver.TCPServer((HOST, PORT), HealthHandler) as httpd:
        print(f"HTTP health endpoint running on http://{HOST}:{PORT}/health")
        httpd.serve_forever()


# ───────────────────────────────────────────────
# Logging helpers
# ───────────────────────────────────────────────

def log_attempt(event_type: str, username: str = None, detail: str = None):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    user_part = f" user={username}" if username else ""
    detail_part = f" - {detail}" if detail else ""
    print(f"[{ts}] LOGIN {event_type.upper()}{user_part}{detail_part}")


def log_disconnect(username: str, connected_at: datetime, reason: str = "normal"):
    now = datetime.utcnow()
    duration = now - connected_at
    seconds = int(duration.total_seconds())
    duration_str = str(timedelta(seconds=seconds))
    ts = now.strftime("%Y-%m-%d %H:%M:%S UTC")
    print(
        f"[{ts}] DISCONNECT user={username} "
        f"duration={duration_str} ({seconds}s) "
        f"reason={reason}"
    )


# ───────────────────────────────────────────────
# Global state
# ───────────────────────────────────────────────

connected_clients = {}       # websocket → username
admin_sessions = set()
usernames = set()
message_history = {}
connection_times = {}


# ───────────────────────────────────────────────
# Message formatters
# ───────────────────────────────────────────────

def system_msg(text):
    return json.dumps({
        "type": "system",
        "content": text,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    })


def chat_msg(username, content, msg_id):
    return json.dumps({
        "type": "message",
        "msg_id": msg_id,
        "username": username,
        "content": content,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    })


def delete_announcement(msg_id):
    return json.dumps({
        "type": "delete",
        "msg_id": msg_id,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    })


def clear_all_announcement():
    return json.dumps({
        "type": "clear_all",
        "timestamp": datetime.utcnow().isoformat() + "Z"
    })


# ───────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────

async def broadcast(message, exclude=None):
    dead = []
    for ws in list(connected_clients):
        if ws is exclude:
            continue
        try:
            await ws.send(message)
        except:
            dead.append(ws)
    for ws in dead:
        cleanup(ws)


def cleanup(websocket):
    if websocket in connected_clients:
        username = connected_clients.pop(websocket)
        usernames.discard(username)
        admin_sessions.discard(websocket)

        connected_at = connection_times.pop(websocket, None)
        if connected_at:
            log_disconnect(username, connected_at)

        asyncio.create_task(
            broadcast(system_msg(f"{username} has left the chat."))
        )


# ───────────────────────────────────────────────
# Authentication
# ───────────────────────────────────────────────

def authenticate(headers):
    auth = headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        log_attempt("FAILED", detail="missing or invalid Authorization header")
        return None, "Missing or invalid Authorization header (Basic expected)"

    try:
        encoded = auth[6:].strip()
        decoded = b64decode(encoded).decode("utf-8")
        username, provided_pass = decoded.split(":", 1)

        username = username.strip()
        if not username:
            log_attempt("FAILED", detail="empty username")
            return None, "Username cannot be empty"

        if provided_pass.strip() != CHAT_PASS:
            log_attempt("FAILED", username=username, detail="wrong password")
            return None, "Incorrect password"

        log_attempt("SUCCESS", username=username)
        return username, None

    except Exception as e:
        log_attempt("FAILED", detail=f"malformed credentials - {str(e)}")
        return None, f"Invalid credentials format"


# ───────────────────────────────────────────────
# WebSocket handler
# ───────────────────────────────────────────────

async def ws_handler(websocket):
    username, error = authenticate(websocket.request_headers)

    if error:
        await websocket.send(system_msg(f"Login failed: {error}"))
        await websocket.close(1008, "Login failed")
        return

    if username in usernames:
        log_attempt("FAILED", username=username, detail="username already in use")
        await websocket.send(system_msg(f"Username '{username}' is already taken"))
        await websocket.close(1008, "Username in use")
        return

    connection_times[websocket] = datetime.utcnow()
    connected_clients[websocket] = username
    usernames.add(username)

    await websocket.send(system_msg(
        f"Welcome, {username}!\n"
        f"To become admin send: AUTH ADMIN <admin_password>\n"
        f"Commands: /help"
    ))
    await broadcast(system_msg(f"{username} has joined the chat"))

    try:
        async for message in websocket:
            text = message.strip()
            if not text:
                continue

            if text.startswith("/"):
                parts = text.split(maxsplit=1)
                cmd = parts[0].lower()
                is_admin = websocket in admin_sessions

                if cmd == "/help":
                    help_text = "/users → show online users"
                    if is_admin:
                        help_text += "\n/delete <msg_id> → delete message\n/clear_chat → clear all messages"
                    else:
                        help_text += "\nAUTH ADMIN <password> → become admin"
                    await websocket.send(system_msg(help_text))
                    continue

                if cmd == "/users":
                    await websocket.send(system_msg(f"Online: {', '.join(sorted(usernames))}"))
                    continue

                if is_admin:
                    if cmd == "/delete" and len(parts) > 1:
                        mid = parts[1].strip()
                        if mid in message_history:
                            del message_history[mid]
                            await broadcast(delete_announcement(mid))
                            await websocket.send(system_msg(f"Message {mid} deleted"))
                        else:
                            await websocket.send(system_msg(f"Message {mid} not found"))
                        continue

                    if cmd == "/clear_chat":
                        message_history.clear()
                        await broadcast(clear_all_announcement())
                        await websocket.send(system_msg("Chat history cleared"))
                        continue

                if cmd in ("/delete", "/clear_chat"):
                    await websocket.send(system_msg("Admin rights required"))
                    continue

                await websocket.send(system_msg("Unknown command — try /help"))
                continue

            if text.startswith("AUTH ADMIN "):
                provided = text[11:].strip()
                if provided == ADMIN_PASSWORD:
                    admin_sessions.add(websocket)
                    log_attempt("ADMIN_SUCCESS", username=username)
                    await websocket.send(system_msg("Admin privileges granted"))
                else:
                    log_attempt("ADMIN_FAILED", username=username, detail="wrong admin password")
                    await websocket.send(system_msg("Incorrect admin password"))
                continue

            msg_id = uuid.uuid4().hex[:8]
            payload = chat_msg(username, text, msg_id)

            message_history[msg_id] = {
                "username": username,
                "content": text,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }

            await broadcast(payload)

    except websockets.exceptions.ConnectionClosed as e:
        reason = "closed by client" if e.code == 1000 else f"closed (code {e.code})"
        cleanup(websocket)
    except Exception as e:
        log_attempt("ERROR", username=username, detail=f"unexpected error - {str(e)}")
        cleanup(websocket)
    finally:
        cleanup(websocket)


# ───────────────────────────────────────────────
# Startup
# ───────────────────────────────────────────────

async def main():
    print(f"Starting chat server on ws://{HOST}:{PORT}")
    print(f"Shared chat password  :  {CHAT_PASS}")
    print(f"Admin password        :  {ADMIN_PASSWORD}")
    print("HTTP health checks enabled → should reduce probe noise in Render logs")

    # Start dummy HTTP server in background thread
    http_thread = Thread(target=run_http_health_server, daemon=True)
    http_thread.start()

    # Start WebSocket server
    async with websockets.serve(
        ws_handler,
        HOST,
        PORT,
        ping_interval=20,
        ping_timeout=60
    ):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped")
