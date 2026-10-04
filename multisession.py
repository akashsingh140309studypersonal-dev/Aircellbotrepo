"""
Cipher Elite MultiSession Module
Allows connecting up to 5 secondary Telethon accounts to duplicate/respond to main userbot commands.
"""

import asyncio
import json
import os
import time
import hashlib
from pathlib import Path

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import MessageNotModifiedError
from plugins.bot import add_handler

# Settings & Globals
MAX_SESSIONS = 5
SETUP_TIMEOUT = 300

DATA_DIR = Path("multisession_data")
DATA_DIR.mkdir(exist_ok=True)
DATA_FILE = DATA_DIR / "config.json"

MAIN_CLIENT = None
CLIENTS = {}
ACCOUNT_INFO = {}
SYNC_ENABLED = True
PENDING_SETUP = {}
PROCESSING = set()

DEFAULT_CONFIG = {
    "enabled": True,
    "primary_id": None,
    "sessions": {}
}

def load_config():
    if not DATA_FILE.exists():
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("enabled", True)
        data.setdefault("primary_id", None)
        data.setdefault("sessions", {})
        return data
    except Exception:
        return dict(DEFAULT_CONFIG)

def save_config(data):
    temp = DATA_FILE.with_suffix(".tmp")
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(temp, DATA_FILE)
    try:
        os.chmod(DATA_FILE, 0o600)
    except Exception:
        pass

CONFIG = load_config()

# Helpers
def get_prefix():
    try:
        from config.config import Config
        return getattr(Config, "PREFIX", None) or getattr(Config, "BOT_PREFIX", None) or "."
    except Exception:
        return "."

def account_name(account_id):
    info = ACCOUNT_INFO.get(account_id, {})
    if info.get("username"):
        return f"@{info['username']}"
    if info.get("name"):
        return info["name"]
    return str(account_id)

def is_command(text):
    if not text:
        return False
    return text.strip().startswith(get_prefix())

def get_command(text):
    if not is_command(text):
        return ""
    prefix = get_prefix()
    text = text.strip()[len(prefix):]
    return text.split(maxsplit=1)[0].lower()

def make_event_id(account_id, chat_id, text):
    raw = f"{account_id}|{chat_id}|{text}|{time.monotonic_ns()}"
    return hashlib.sha256(raw.encode()).hexdigest()

async def save_account_info(client):
    me = await client.get_me()
    if not me:
        raise RuntimeError("Account identify nahi ho saka.")
    
    account_id = int(me.id)
    ACCOUNT_INFO[account_id] = {
        "id": account_id,
        "username": me.username,
        "name": " ".join([x for x in [me.first_name, me.last_name] if x]).strip()
    }
    return account_id

def create_client(session_string):
    try:
        from config.config import Config
        api_id = getattr(Config, "API_ID", None)
        api_hash = getattr(Config, "API_HASH", None)
    except Exception:
        api_id, api_hash = None, None

    if not api_id or not api_hash:
        raise RuntimeError("API_ID / API_HASH config me nahi mila.")

    return TelegramClient(
        StringSession(session_string),
        api_id,
        api_hash,
        auto_reconnect=True,
        connection_retries=5,
        retry_delay=2
    )

def extra_session_count():
    primary = CONFIG.get("primary_id")
    count = 0
    for account_id in CONFIG["sessions"]:
        try:
            if int(account_id) != primary:
                count += 1
        except Exception:
            continue
    return count

def can_add_session():
    return extra_session_count() < MAX_SESSIONS

# Session Management
async def connect_new_session(session_string):
    if not can_add_session():
        return False, "Maximum 5 sessions limit reached ho chuka hai."

    session_string = session_string.strip()
    if not session_string:
        return False, "Session string khali hai."

    client = None
    try:
        client = create_client(session_string)
        await client.connect()

        if not await client.is_user_authorized():
            await client.disconnect()
            return False, "Session authorized nahi hai."

        account_id = await save_account_info(client)
        if account_id in CLIENTS:
            await client.disconnect()
            return False, "Ye account pehle se connected hai."

        if not CONFIG.get("primary_id"):
            CONFIG["primary_id"] = account_id

        CONFIG["sessions"][str(account_id)] = {"session": session_string}
        save_config(CONFIG)

        CLIENTS[account_id] = client
        install_sync_handler(client, account_id)

        return True, account_id
    except Exception as e:
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass
        return False, str(e)

async def remove_session(account_id):
    account_id = int(account_id)
    if str(account_id) not in CONFIG["sessions"]:
        return False, "Ye account connected nahi hai."

    client = CLIENTS.pop(account_id, None)
    if client:
        try:
            await client.disconnect()
        except Exception:
            pass

    CONFIG["sessions"].pop(str(account_id), None)
    ACCOUNT_INFO.pop(account_id, None)

    if CONFIG.get("primary_id") == account_id:
        remaining = list(CONFIG["sessions"].keys())
        CONFIG["primary_id"] = int(remaining[0]) if remaining else None

    save_config(CONFIG)
    return True, None

# Sync Logic (Work Feature)
async def synchronize_command(source_id, event, command):
    global SYNC_ENABLED

    if not SYNC_ENABLED or not command or not is_command(command):
        return

    chat_id = event.chat_id
    if chat_id is None:
        return

    cmd = get_command(command)
    if cmd in {"ms", "multisession", "mssync"}:
        return

    event_id = make_event_id(source_id, chat_id, command)
    if event_id in PROCESSING:
        return

    PROCESSING.add(event_id)
    try:
        jobs = []
        for account_id, client in list(CLIENTS.items()):
            if account_id == source_id or not client.is_connected():
                continue
            jobs.append(send_command(client, chat_id, command))

        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)
    finally:
        PROCESSING.discard(event_id)

async def send_command(client, chat_id, command):
    try:
        await client.send_message(chat_id, command)
    except Exception:
        pass

def install_sync_handler(client, account_id):
    @client.on(events.NewMessage(outgoing=True))
    async def multi_session_handler(event):
        try:
            text = event.raw_text
            if is_command(text):
                await synchronize_command(account_id, event, text.strip())
        except Exception:
            pass

async def load_saved_sessions():
    for raw_id, data in list(CONFIG["sessions"].items()):
        client = None
        try:
            session_string = data.get("session")
            if not session_string:
                continue

            client = create_client(session_string)
            await client.connect()

            if not await client.is_user_authorized():
                await client.disconnect()
                continue

            account_id = await save_account_info(client)
            CLIENTS[account_id] = client
            install_sync_handler(client, account_id)
        except Exception:
            if client:
                try:
                    await client.disconnect()
                except Exception:
                    pass

# Texts & Views
def help_text():
    prefix = get_prefix()
    return (
        f"✦ **MultiSession Control Panel**\n\n"
        f"**Available Commands:**\n"
        f"• `{prefix}ms add <string_session>` - Naya account connect karein\n"
        f"• `{prefix}ms remove <account_id>` - Connected account ko disconnect karein\n"
        f"• `{prefix}ms list` / `{prefix}ms show` - Saare sessions dekhein\n"
        f"• `{prefix}ms status` - Bot sync status dekhein\n"
        f"• `{prefix}ms on` / `{prefix}ms off` - Command sync Toggle karein\n"
    )

def show_sessions_text():
    if not CLIENTS:
        return "✦ **Connected Sessions**\n\nAbhi koi secondary session connected nahi hai."

    primary = CONFIG.get("primary_id")
    lines = ["✦ **Connected Sessions List**\n"]
    for account_id in CLIENTS:
        star = "⭐ [Primary]" if account_id == primary else "🔗 [Connected]"
        lines.append(f"{star} {account_name(account_id)} (ID: `{account_id}`)")

    lines.append(f"\nTotal Sessions: `{extra_session_count()}/{MAX_SESSIONS}`")
    return "\n".join(lines)

def status_text():
    primary = CONFIG.get("primary_id")
    return (
        f"✦ **MultiSession System Status**\n\n"
        f"• **Sync State:** `{'ON 🟢' if SYNC_ENABLED else 'OFF 🔴'}`\n"
        f"• **Active Sessions:** `{len(CLIENTS)}` (Extra: `{extra_session_count()}/{MAX_SESSIONS}`)\n"
        f"• **Primary Account ID:** `{primary if primary else 'Not set'}`\n"
        f"• **Loop Protection:** `Active`\n"
    )

async def safe_edit(event, text, **kwargs):
    try:
        return await event.edit(text, **kwargs)
    except MessageNotModifiedError:
        return None
    except Exception:
        return None

# Command Handlers
async def register_commands():
    if MAIN_CLIENT is None:
        return

    prefix = get_prefix()
    pattern = rf"^{prefix}(?:ms|multisession|mssync)(?:\s+(.+))?$"

    @MAIN_CLIENT.on(events.NewMessage(pattern=pattern))
    async def management_handler(event):
        global SYNC_ENABLED
        raw = event.pattern_match.group(1)

        if not raw:
            await safe_edit(event, help_text())
            return

        parts = raw.strip().split(maxsplit=1)
        action = parts[0].lower()
        argument = parts[1].strip() if len(parts) > 1 else ""

        if action in {"help", "?"}:
            await safe_edit(event, help_text())

        # ADD SESSION
        elif action in {"add", "connect"}:
            if not argument:
                await safe_edit(event, f"⚠️ Usage: `{prefix}ms add <string_session>`")
                return
            if not can_add_session():
                await safe_edit(event, "❌ Maximum 5 secondary sessions limit reached.")
                return

            await safe_edit(event, "⏳ Connecting new session, please wait...")
            ok, result = await connect_new_session(argument)
            if not ok:
                await safe_edit(event, f"❌ Session Add Failed: `{result}`")
                return

            await safe_edit(event, f"✅ **Session Successfully Connected!**\n🆔 Account ID: `{result}`")

        # SHOW / LIST SESSION
        elif action in {"list", "show", "sessions"}:
            await safe_edit(event, show_sessions_text())

        # REMOVE SESSION
        elif action in {"remove", "disconnect", "del", "rm"}:
            if not argument.isdigit():
                await safe_edit(event, f"⚠️ Usage: `{prefix}ms remove <account_id>`")
                return
            ok, err = await remove_session(int(argument))
            if ok:
                await safe_edit(event, f"✅ Session `{argument}` successfully removed.")
            else:
                await safe_edit(event, f"❌ Failed: {err}")

        # STATUS
        elif action == "status":
            await safe_edit(event, status_text())

        # ON / OFF
        elif action in {"on", "enable"}:
            SYNC_ENABLED = CONFIG["enabled"] = True
            save_config(CONFIG)
            await safe_edit(event, "🟢 MultiSession Sync Activated.")

        elif action in {"off", "disable"}:
            SYNC_ENABLED = CONFIG["enabled"] = False
            save_config(CONFIG)
            await safe_edit(event, "🔴 MultiSession Sync Deactivated.")

# Plugin Init
def init(client):
    global MAIN_CLIENT, SYNC_ENABLED
    MAIN_CLIENT = client
    SYNC_ENABLED = CONFIG.get("enabled", True)

    add_handler("multisession", [".ms help"], "MultiSession Command Manager")
    
    task = asyncio.create_task(initialize_plugin())
    task.add_done_callback(lambda t: None)

async def initialize_plugin():
    try:
        primary_id = await save_account_info(MAIN_CLIENT)
        CLIENTS[primary_id] = MAIN_CLIENT

        if not CONFIG.get("primary_id"):
            CONFIG["primary_id"] = primary_id
            save_config(CONFIG)

        install_sync_handler(MAIN_CLIENT, primary_id)
        await load_saved_sessions()
        await register_commands()
    except Exception:
        pass
