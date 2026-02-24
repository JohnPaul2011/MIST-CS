import asyncio
import aiohttp
from aiohttp import web, WSMsgType
import json
from datetime import datetime
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

                WebSocket Chat Server (aiohttp)
──────────────────────────────────────────────────────────
• Basic Auth required (username + CHAT_PASS)
• AUTH ADMIN <password> to become admin
• /users
• /delete <msg_id>     (admin)
• /clear_chat          (admin)
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


def log_disconnect(username: str, connected_at: datetime):
    now = datetime.utcnow()
    duration = now - connected_at
    seconds = int(duration.total_seconds())
    ts = now.strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] DISCONNECT user={username} duration={seconds}s")

# ───────────────────────────────────────────────
# Global state
# ───────────────────────────────────────────────

connected_clients = {}          # ws → username
admin_sessions = set()
usernames = set()
message_history = []            # list of dict payloads
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
    return {
        "type": "message",
        "msg_id": msg_id,
        "username": username,
        "content": content,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }


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

async def broadcast(message: str, exclude=None):
    dead = []
    for ws in list(connected_clients):
        if ws is exclude or ws.closed:
            continue
        try:
            await ws.send_str(message)
        except Exception:
            dead.append(ws)

    for ws in dead:
        await cleanup(ws)


async def cleanup(websocket):
    if websocket not in connected_clients:
        return

    username = connected_clients.pop(websocket, None)
    if username:
        usernames.discard(username)
        admin_sessions.discard(websocket)

        connected_at = connection_times.pop(websocket, None)
        if connected_at:
            log_disconnect(username, connected_at)

        await broadcast(system_msg(f"{username} has left the chat."))


def authenticate(headers):
    auth = headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return None, "Missing Authorization header"

    try:
        encoded = auth[6:].strip()
        decoded = b64decode(encoded).decode("utf-8")
        username, provided_pass = decoded.split(":", 1)

        if provided_pass.strip() != CHAT_PASS:
            return None, "Incorrect password"

        return username.strip(), None

    except Exception:
        return None, "Invalid credentials format"

# ───────────────────────────────────────────────
# WebSocket handler
# ───────────────────────────────────────────────

async def websocket_handler(request):
    headers = dict(request.headers)
    username, error = authenticate(headers)

    if error:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_str(system_msg(f"Login failed: {error}"))
        await ws.close()
        return ws

    if username in usernames:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_str(system_msg("Username already taken"))
        await ws.close()
        return ws

    ws = web.WebSocketResponse(autoping=True, heartbeat=20.0)
    await ws.prepare(request)

    connection_times[ws] = datetime.utcnow()
    connected_clients[ws] = username
    usernames.add(username)

    await ws.send_str(system_msg(f"Welcome, {username}!"))
    await broadcast(system_msg(f"{username} joined the chat"))

    # Send history to new client
    for msg in message_history:
        await ws.send_json(msg)

    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue

            text = msg.data.strip()
            if not text:
                continue

            # ─── Admin commands ───────────────────────────────────────
            if text.startswith("AUTH ADMIN "):
                provided = text[11:].strip()
                if provided == ADMIN_PASSWORD:
                    admin_sessions.add(ws)
                    await ws.send_str(system_msg("Admin privileges granted"))
                else:
                    await ws.send_str(system_msg("Incorrect admin password"))
                continue

            if text == "/users":
                await ws.send_str(
                    system_msg(f"Online: {', '.join(sorted(usernames))}")
                )
                continue

            if text == "/clear_chat":
                if ws not in admin_sessions:
                    await ws.send_str(system_msg("You are not admin"))
                    continue
                message_history.clear()
                await broadcast(clear_all_announcement())
                await broadcast(system_msg("Chat has been cleared by admin"))
                continue

            if text.startswith("/delete "):
                if ws not in admin_sessions:
                    await ws.send_str(system_msg("You are not admin"))
                    continue

                parts = text.split(maxsplit=1)
                if len(parts) != 2:
                    await ws.send_str(system_msg("Usage: /delete <msg_id>"))
                    continue

                target_prefix = parts[1].strip()
                if len(target_prefix) < 4:
                    await ws.send_str(system_msg("Message ID too short"))
                    continue

                new_history = []
                found = False
                for m in message_history:
                    if m.get("msg_id", "").startswith(target_prefix):
                        found = True
                    else:
                        new_history.append(m)

                if found:
                    message_history[:] = new_history
                    await broadcast(delete_announcement(target_prefix))
                    await ws.send_str(system_msg(f"Deleted message(s) starting with {target_prefix}"))
                    await broadcast(system_msg(f"Message(s) {target_prefix}… deleted by admin"))
                else:
                    await ws.send_str(system_msg(f"No message found with ID: {target_prefix}"))
                continue

            # Normal message
            msg_id = uuid.uuid4().hex[:8]
            payload = chat_msg(username, text, msg_id)

            message_history.append(payload)
            await broadcast(json.dumps(payload), exclude=ws)

    except Exception as e:
        print(f"WebSocket error for {username}: {e}")

    finally:
        await cleanup(ws)

    return ws


# ───────────────────────────────────────────────
# Health check endpoint
# ───────────────────────────────────────────────

async def health_handler(request):
    return web.json_response({
        "status": "ok",
        "connected_clients": len(connected_clients),
        "uptime": str(datetime.utcnow() - datetime.fromtimestamp(0))
    })


# ───────────────────────────────────────────────
# Server startup
# ───────────────────────────────────────────────

async def main():
    print(BANNER)
    print(f"Starting server →  {HOST}:{PORT}\n")

    app = web.Application()
    app.router.add_get("/health", health_handler)
    app.router.add_get("/", websocket_handler)  # WebSocket lives here

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, HOST, PORT)
    await site.start()

    print("Server is running...")

    loop = asyncio.get_running_loop()

    def shutdown():
        print("\nShutting down...")
        for ws in list(connected_clients):
            asyncio.create_task(ws.close(code=1001, reason="Server shutdown"))

    loop.add_signal_handler(signal.SIGTERM, shutdown)
    loop.add_signal_handler(signal.SIGINT, shutdown)

    await asyncio.Future()  # run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped")
