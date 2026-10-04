"""
Cipher Elite MultiSession Plugin (Fixed & Complete)
Connect multiple Telethon userbot accounts to mirror commands across accounts.
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

# ============================================================
# CONFIGURATION & SETTINGS
# ============================================================

MAX_SESSIONS = 5
DATA_DIR = Path("multisession_data")
DATA_DIR.mkdir(exist_ok=True)
DATA_FILE = DATA_DIR / "config.json"

MAIN_CLIENT = None
CLIENTS = {}           # account_id -> TelegramClient
ACCOUNT_INFO = {}      # account_id -> info dict
SYNC_ENABLED = True
PROCESSING = set()     # Event hash deduplication

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

# ============================================================
# HELPER FUNCTIONS
# ============================================================

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

def make_event_id(source_id, chat_id, text):
    raw = f"{source_id}|{chat_id}|{text}|{int(time.time())}"
    return hashlib.sha256(raw.encode()).hexdigest()

async def save_account_info(client):
    me = await client.get_me()
    if not me:
        raise RuntimeError("Account ko verify nahi kiya ja saka.")
    
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
        raise RuntimeError("Config file me API_ID ya API_HASH nahi mili.")

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

# ============================================================
# SESSION MANAGEMENT (ADD / REMOVE)
# ============================================================

async def connect_new_session(session_string):
    if not can_add_session():
        return False, f"Maximum limit ({MAX_SESSIONS} extra sessions) reach ho chuki hai."

    session_string = session_string.strip()
    if not session_string:
        return False, "Session string empty hai."

    client = None
    try:
        client = create_client(session_string)
        await client.connect()

        if not await client.is_user_authorized():
            await client.disconnect()
            return False, "Ye session invalid ya expired hai."

        account_id = await save_account_info(client)
        if account_id in CLIENTS:
            await client.disconnect()
            return False, "Ye account pehle se hi connected hai."

        if not CONFIG.get("primary_id"):
            CONFIG["primary_id"] = account_id

        CONFIG["sessions"][str(account_id)] = {"session": session_string}
        save_config(CONFIG)

        CLIENTS[account_id] = client
        attach_event_listeners(client, account_id)

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
        return False, "Ye account ID list me nahi hai."

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

# ============================================================
# COMMAND SYNCHRONIZATION ENGINE
# ============================================================

async def execute_remote_command(target_client, chat_id, command_text):
    """Secondary account group me main account ka command bhejta hai."""
    try:
        await target_client.send_message(chat_id, command_text)
    except Exception:
        pass

async def handle_incoming_sync(sender_id, chat_id, text):
    """Main account se command aane par baki secondary accounts trigger honge."""
    global SYNC_ENABLED

    if not SYNC_ENABLED or not is_command(text):
        return

    cmd = get_command(text)
    # Management commands sync nahi honge
    if cmd in {"ms", "multisession", "mssync"}:
        return

    # Self-loop protection via unique event ID
    event_id = make_event_id(sender_id, chat_id, text)
    if event_id in PROCESSING:
        return

    PROCESSING.add(event_id)
    try:
        jobs = []
        for account_id, client in list(CLIENTS.items()):
            # Send command from all secondary accounts except the original sender
            if account_id == sender_id or not client.is_connected():
                continue
            jobs.append(execute_remote_command(client, chat_id, text))

        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)
    finally:
        await asyncio.sleep(2)
        PROCESSING.discard(event_id)

def attach_event_listeners(client, account_id):
    """Har account me incoming aur outgoing commands listen karne ke liye handlers attach karta hai."""
    
    @client.on(events.NewMessage)
    async def multi_session_event_handler(event):
        try:
            # Agar primary account se koi message group/chat me aaya
            primary_id = CONFIG.get("primary_id")
            sender_id = event.sender_id
            
            if sender_id == primary_id and is_command(event.raw_text):
                await handle_incoming_sync(sender_id, event.chat_id, event.raw_text.strip())
        except Exception:
            pass

# ============================================================
# TEXT VIEWS & DISPLAY HELPERS
# ============================================================

def get_help_ui():
    prefix = get_prefix()
    return f"""
✨ **Cipher Elite MultiSession Control**

Is plugin se aap apne main userbot me secondary Telegram accounts connect kar sakte hain. Aap primary account se command chalaenge, toh baki saare connected accounts bhi group me wahi action karenge.

⚙️ **Commands Overview:**

1️⃣ **`{prefix}ms add <string_session>`**
   • **Meaning:** Secondary account ko add/connect karta hai.
   • **Example:** `{prefix}ms add 1BJW4...`

2️⃣ **`{prefix}ms show` / `{prefix}ms list`**
   • **Meaning:** Sabhi active aur connected accounts ki ID aur Username list dikhata hai.

3️⃣ **`{prefix}ms remove <account_id>`**
   • **Meaning:** Kisi connected account ko list se hata kar disconnect kar deta hai.
   • **Example:** `{prefix}ms remove 123456789`

4️⃣ **`{prefix}ms status`**
   • **Meaning:** Sync ON/OFF state, connected accounts count, aur primary ID dikhata hai.

5️⃣ **`{prefix}ms on` / `{prefix}ms off`**
   • **Meaning:** Multi-account syncing feature ko enable ya disable karta hai.
"""

def get_sessions_ui():
    if not CLIENTS:
        return "⚠️ **MultiSession:** Koi bhi secondary session connected nahi hai."

    primary = CONFIG.get("primary_id")
    lines = ["👥 **Connected Accounts List:**\n"]
    for acc_id in CLIENTS:
        role = "⭐ [Primary]" if acc_id == primary else "🔗 [Secondary]"
        lines.append(f"{role} **{account_name(acc_id)}** | ID: `{acc_id}`")

    lines.append(f"\n📊 **Total Connected:** `{extra_session_count()}/{MAX_SESSIONS}`")
    lines.append(f"⚡ **Sync System:** `{'Active 🟢' if SYNC_ENABLED else 'Disabled 🔴'}`")
    return "\n".join(lines)

def get_status_ui():
    primary = CONFIG.get("primary_id")
    return f"""
📊 **MultiSession System Status**

• **Sync Engine:** `{'Active 🟢' if SYNC_ENABLED else 'Disabled 🔴'}`
• **Total Accounts:** `{len(CLIENTS)}` (Secondary: `{extra_session_count()}/{MAX_SESSIONS}`)
• **Primary Account:** `{account_name(primary) if primary else 'Not Set'}` (`{primary}`)
• **Loop Protection:** `Enabled 🛡️`
• **Command Prefix:** `{get_prefix()}`
"""

async def safe_edit(event, text):
    try:
        return await event.edit(text)
    except MessageNotModifiedError:
        return None
    except Exception:
        return None

# ============================================================
# MANAGEMENT COMMAND HANDLERS
# ============================================================

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
            await safe_edit(event, get_help_ui())
            return

        parts = raw.strip().split(maxsplit=1)
        action = parts[0].lower()
        argument = parts[1].strip() if len(parts) > 1 else ""

        # HELP
        if action in {"help", "?"}:
            await safe_edit(event, get_help_ui())

        # ADD SESSION
        elif action in {"add", "connect"}:
            if not argument:
                await safe_edit(event, f"⚠️ Usage: `{prefix}ms add <string_session>`")
                return
            
            await safe_edit(event, "⏳ Account connect ho raha hai, kripya wait karein...")
            ok, result = await connect_new_session(argument)
            
            if not ok:
                await safe_edit(event, f"❌ **Connection Failed:** `{result}`")
            else:
                await safe_edit(event, f"✅ **Account Connected Successfully!**\n👤 Account: {account_name(result)}\n🆔 ID: `{result}`")

        # SHOW / LIST SESSIONS
        elif action in {"list", "show", "sessions"}:
            await safe_edit(event, get_sessions_ui())

        # REMOVE SESSION
        elif action in {"remove", "disconnect", "del", "rm"}:
            if not argument.isdigit():
                await safe_edit(event, f"⚠️ Usage: `{prefix}ms remove <account_id>`")
                return
            
            ok, err = await remove_session(int(argument))
            if ok:
                await safe_edit(event, f"🗑️ Account ID `{argument}` disconnect kar diya gaya.")
            else:
                await safe_edit(event, f"❌ **Failed:** {err}")

        # STATUS
        elif action == "status":
            await safe_edit(event, get_status_ui())

        # SYNC TOGGLE ON / OFF
        elif action in {"on", "enable"}:
            SYNC_ENABLED = CONFIG["enabled"] = True
            save_config(CONFIG)
            await safe_edit(event, "🟢 **MultiSession Sync Enable kar diya gaya hai.**")

        elif action in {"off", "disable"}:
            SYNC_ENABLED = CONFIG["enabled"] = False
            save_config(CONFIG)
            await safe_edit(event, "🔴 **MultiSession Sync Disable kar diya gaya hai.**")

async def load_saved_sessions():
    """Bot restart hone par pehle se saved sessions connect karta hai."""
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
            attach_event_listeners(client, account_id)
        except Exception:
            if client:
                try:
                    await client.disconnect()
                except Exception:
                    pass

# ============================================================
# PLUGIN INITIALIZATION
# ============================================================

def init(client):
    global MAIN_CLIENT, SYNC_ENABLED
    MAIN_CLIENT = client
    SYNC_ENABLED = CONFIG.get("enabled", True)

    add_handler("multisession", [".ms help"], "Multi-Account Sync Manager")
    
    task = asyncio.create_task(initialize_plugin())
    task.add_done_callback(lambda t: None)

async def initialize_plugin():
    try:
        primary_id = await save_account_info(MAIN_CLIENT)
        CLIENTS[primary_id] = MAIN_CLIENT

        if not CONFIG.get("primary_id"):
            CONFIG["primary_id"] = primary_id
            save_config(CONFIG)

        attach_event_listeners(MAIN_CLIENT, primary_id)
        await load_saved_sessions()
        await register_commands()
    except Exception:
        pass
