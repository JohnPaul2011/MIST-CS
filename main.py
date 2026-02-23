import asyncio
import websockets
import json
from datetime import datetime, timedelta
import uuid
import os
from base64 import b64decode
import signal

# ───────────────────────────────────────────────
# ASCII STARTUP BANNER
# ───────────────────────────────────────────────

BANNER = r"""
   ____ _           _     ____                 
  / ___| |__   __ _| |_  / ___|  ___ _ ____   _____ _ __ 
 | |   | '_ \ / _` | __| \___ \ / _ \ '__\ \ / / _ \ '__|
 | |___| | | | (_| | |_   ___) |  __/ |   \ V /  __/ |   
  \____|_| |_|\__,_|\__| |____/ \___|_|    \_/ \___|_|   

                WebSocket Chat Server
──────────────────────────────────────────────────────────

HOW IT WORKS:
• Clients connect using WebSocket.
• Basic Auth required (username + CHAT_PASS).
• Messages are broadcast to all users.
• Admins can moderate chat.

USER COMMANDS:
• /help
• /users
• AUTH ADMIN <password>

ADMIN COMMANDS:
• /delete <msg_id>
• /clear_chat

Health:
• GET /health
──────────────────────────────────────────────────────────
"""

# ───────────────────────────────────────────────
# CONFIG
# ───────────────────────────────────────────────

CHAT_PASS = os.environ.get("CHAT_PASS", "default-insecure-123-change-me")
ADMIN_PASSWORD = os.environ.get("CHAT_ADMIN_PASS", "admin-secret-2025")

PORT = int(os.environ.get("PORT", 10000))
HOST = "0.0.0.0"

# ───────────────────────────────────────────────
# Logging
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

connected_clients = {}
admin_sessions = set()
usernames = set()
message_history = {}
connection_times = {}

# ───────────────────────────────────────────────
# Message helpers
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
        except websockets.exceptions.ConnectionClosed:
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
        log_attempt("FAILED", detail="missing Authorization")
        return None, "Missing Authorization header"

    try:
        encoded = auth[6:].strip()
        decoded = b64decode(encoded).decode("utf-8")
        username, provided_pass = decoded.split(":", 1)

        if provided_pass.strip() != CHAT_PASS:
            log_attempt("FAILED", username=username, detail="wrong password")
            return None, "Incorrect password"

        log_attempt("SUCCESS", username=username)
        return username.strip(), None

    except Exception as e:
        log_attempt("FAILED", detail=str(e))
        return None, "Invalid credentials format"

# ───────────────────────────────────────────────
# WebSocket handler
# ───────────────────────────────────────────────

async def ws_handler(websocket, path):
    username, error = authenticate(websocket.request_headers)

    if error:
        await websocket.send(system_msg(f"Login failed: {error}"))
        await websocket.close(1008, "Login failed")
        return

    if username in usernames:
        await websocket.send(system_msg(f"Username '{username}' is taken"))
        await websocket.close(1008, "Username in use")
        return

    connection_times[websocket] = datetime.utcnow()
    connected_clients[websocket] = username
    usernames.add(username)

    await websocket.send(system_msg(f"Welcome, {username}!"))
    await broadcast(system_msg(f"{username} joined the chat"))

    try:
        async for message in websocket:
            text = message.strip()
            if not text:
                continue

            if text.startswith("AUTH ADMIN "):
                provided = text[11:].strip()
                if provided == ADMIN_PASSWORD:
                    admin_sessions.add(websocket)
                    await websocket.send(system_msg("Admin privileges granted"))
                else:
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

    except websockets.exceptions.ConnectionClosed:
        pass

    except Exception as e:
        log_attempt("ERROR", username=username, detail=str(e))

    finally:
        cleanup(websocket)

# ───────────────────────────────────────────────
# HTTP handler (compatible with old versions)
# ───────────────────────────────────────────────

async def process_http_request(path, request_headers):
    if path == "/health":
        body = json.dumps({"status": "ok"}).encode("utf-8")
        headers = [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(body)))
        ]
        return 200, headers, body

    body = b"Not Found"
    return 404, [
        ("Content-Type", "text/plain"),
        ("Content-Length", str(len(body)))
    ], body

# ───────────────────────────────────────────────
# Server startup
# ───────────────────────────────────────────────

async def main():
    print(BANNER)
    print(f"Starting server on {HOST}:{PORT}\n")

    server = await websockets.serve(
        ws_handler,
        HOST,
        PORT,
        ping_interval=20,
        ping_timeout=60,
        process_request=process_http_request,
        compression=None
    )

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, loop.stop)

    try:
        await server.wait_closed()
    finally:
        print("Closing connections...")
        for ws in list(connected_clients):
            try:
                await ws.close()
            except:
                pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped")
