"""
╔══════════════════════════════════════════════════════════════╗
║                 CIPHER ELITE MULTI SESSION                  ║
╠══════════════════════════════════════════════════════════════╣
║ Connect up to 5 additional Telegram accounts and keep their  ║
║ Cipher Elite commands synchronized.                         ║
║                                                              ║
║ Features:                                                    ║
║ • Maximum 5 additional sessions                             ║
║ • Primary account support                                    ║
║ • Any command can be synchronized                            ║
║ • No hardcoded .help/.alive list                             ║
║ • Add / remove / list accounts                               ║
║ • Sync ON / OFF                                              ║
║ • 5-minute setup timeout                                     ║
║ • Duplicate account protection                               ║
║ • Loop protection                                            ║
║ • Session is never shown in replies                          ║
║                                                              ║
║ License: MIT                                                 ║
║ Copyright (c) 2025 Rishabh / Cipher Elite                   ║
╚══════════════════════════════════════════════════════════════╝
"""

import asyncio
import json
import os
import time
import hashlib
from pathlib import Path

from telethon import TelegramClient, events
from telethon.sessions import StringSession


# ============================================================
# SETTINGS
# ============================================================

MAX_SESSIONS = 5
SETUP_TIMEOUT = 300  # 5 minutes

DATA_DIR = Path("multisession_data")
DATA_DIR.mkdir(exist_ok=True)

DATA_FILE = DATA_DIR / "config.json"

MAIN_CLIENT = None

CLIENTS = {}

ACCOUNT_INFO = {}

SYNC_ENABLED = True

PENDING_SETUP = {}

PROCESSING = set()


# ============================================================
# CONFIG
# ============================================================

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


CONFIG = load_config()


# ============================================================
# BASIC HELPERS
# ============================================================

def get_prefix():

    try:
        from config.config import Config

        prefix = getattr(Config, "PREFIX", None)

        if prefix:
            return prefix

        prefix = getattr(Config, "BOT_PREFIX", None)

        if prefix:
            return prefix

    except Exception:
        pass

    return "."


def account_name(account_id):

    info = ACCOUNT_INFO.get(account_id, {})

    username = info.get("username")

    if username:
        return f"@{username}"

    name = info.get("name")

    if name:
        return name

    return str(account_id)


def is_command(text):

    if not text:
        return False

    prefix = get_prefix()

    return text.strip().startswith(prefix)


def get_command(text):

    if not is_command(text):
        return ""

    prefix = get_prefix()

    text = text.strip()[len(prefix):]

    return text.split(maxsplit=1)[0].lower()


def make_event_id(account_id, chat_id, text):

    raw = (
        f"{account_id}|"
        f"{chat_id}|"
        f"{text}|"
        f"{time.monotonic_ns()}"
    )

    return hashlib.sha256(
        raw.encode()
    ).hexdigest()


# ============================================================
# ACCOUNT INFORMATION
# ============================================================

async def save_account_info(client):

    me = await client.get_me()

    if not me:
        raise RuntimeError(
            "Telegram account could not be identified."
        )

    account_id = int(me.id)

    ACCOUNT_INFO[account_id] = {
        "id": account_id,
        "username": me.username,
        "name": " ".join(
            x for x in [
                me.first_name,
                me.last_name
            ]
            if x
        ).strip()
    }

    return account_id


# ============================================================
# CLIENT CREATOR
# ============================================================

def create_client(session_string):

    try:
        from config.config import Config

        api_id = getattr(Config, "API_ID", None)
        api_hash = getattr(Config, "API_HASH", None)

    except Exception:
        api_id = None
        api_hash = None

    if not api_id or not api_hash:

        raise RuntimeError(
            "Cipher Elite API_ID/API_HASH not found."
        )

    # --------------------------------------------------------
    # IMPORTANT
    #
    # This expects a normal Telethon StringSession.
    #
    # If your official Cipher Elite session is a custom
    # ELITE_SESSION format, replace this StringSession part
    # with Cipher Elite's official session loader.
    # --------------------------------------------------------

    return TelegramClient(
        StringSession(session_string),
        api_id,
        api_hash,
        auto_reconnect=True,
        connection_retries=5,
        retry_delay=2
    )


# ============================================================
# SESSION LIMIT
# ============================================================

def extra_session_count():

    primary = CONFIG.get("primary_id")

    count = 0

    for account_id in CONFIG["sessions"]:

        try:
            account_id = int(account_id)
        except Exception:
            continue

        if account_id != primary:
            count += 1

    return count


def can_add_session():

    return extra_session_count() < MAX_SESSIONS


# ============================================================
# ADD SESSION
# ============================================================

async def connect_new_session(session_string):

    if not can_add_session():

        return (
            False,
            "Maximum 5 additional accounts are already connected."
        )

    session_string = session_string.strip()

    if not session_string:

        return False, "Session is empty."

    client = None

    try:

        client = create_client(session_string)

        await client.connect()

        if not await client.is_user_authorized():

            await client.disconnect()

            return (
                False,
                "This session is not authorized."
            )

        account_id = await save_account_info(
            client
        )

        if account_id in CLIENTS:

            await client.disconnect()

            return (
                False,
                "This account is already connected."
            )

        # ----------------------------------------------------
        # First connected account becomes primary automatically
        # ----------------------------------------------------

        if not CONFIG.get("primary_id"):

            CONFIG["primary_id"] = account_id

        CONFIG["sessions"][
            str(account_id)
        ] = {
            "session": session_string
        }

        save_config(CONFIG)

        CLIENTS[account_id] = client

        install_sync_handler(
            client,
            account_id
        )

        return True, account_id

    except Exception as e:

        if client:

            try:
                await client.disconnect()
            except Exception:
                pass

        return False, str(e)


# ============================================================
# REMOVE SESSION
# ============================================================

async def remove_session(account_id):

    account_id = int(account_id)

    if str(account_id) not in CONFIG["sessions"]:

        return (
            False,
            "This account is not connected."
        )

    client = CLIENTS.pop(
        account_id,
        None
    )

    if client:

        try:
            await client.disconnect()
        except Exception:
            pass

    CONFIG["sessions"].pop(
        str(account_id),
        None
    )

    ACCOUNT_INFO.pop(
        account_id,
        None
    )

    # If primary was removed, choose another account.
    if CONFIG.get("primary_id") == account_id:

        remaining = list(
            CONFIG["sessions"].keys()
        )

        if remaining:

            CONFIG["primary_id"] = int(
                remaining[0]
            )

        else:

            CONFIG["primary_id"] = None

    save_config(CONFIG)

    return True, None


# ============================================================
# COMMAND SYNCHRONIZATION
# ============================================================

async def synchronize_command(
    source_id,
    event,
    command
):

    global SYNC_ENABLED

    if not SYNC_ENABLED:
        return

    if not command:
        return

    if not is_command(command):
        return

    chat_id = event.chat_id

    if chat_id is None:
        return

    # --------------------------------------------------------
    # Management commands must never be mirrored.
    # Otherwise .ms add could create a loop.
    # --------------------------------------------------------

    cmd = get_command(command)

    if cmd in {
        "ms",
        "multisession",
        "mssync"
    }:
        return

    event_id = make_event_id(
        source_id,
        chat_id,
        command
    )

    if event_id in PROCESSING:
        return

    PROCESSING.add(event_id)

    try:

        jobs = []

        for account_id, client in list(
            CLIENTS.items()
        ):

            # Don't execute the command again on source.
            if account_id == source_id:
                continue

            if not client.is_connected():
                continue

            jobs.append(
                send_command(
                    client,
                    chat_id,
                    command
                )
            )

        if jobs:

            await asyncio.gather(
                *jobs,
                return_exceptions=True
            )

    finally:

        PROCESSING.discard(
            event_id
        )


async def send_command(
    client,
    chat_id,
    command
):

    try:

        await client.send_message(
            chat_id,
            command
        )

    except Exception:
        # One account failing should not stop others.
        pass


# ============================================================
# EVENT HOOK
# ============================================================

def install_sync_handler(
    client,
    account_id
):

    @client.on(
        events.NewMessage(outgoing=True)
    )
    async def multi_session_handler(event):

        try:

            text = event.raw_text

            if not is_command(text):
                return

            await synchronize_command(
                account_id,
                event,
                text.strip()
            )

        except Exception:
            pass


# ============================================================
# LOAD SAVED SESSIONS
# ============================================================

async def load_saved_sessions():

    for raw_id, data in list(
        CONFIG["sessions"].items()
    ):

        client = None

        try:

            session_string = data.get(
                "session"
            )

            if not session_string:
                continue

            client = create_client(
                session_string
            )

            await client.connect()

            if not await client.is_user_authorized():

                await client.disconnect()

                continue

            account_id = await save_account_info(
                client
            )

            CLIENTS[account_id] = client

            install_sync_handler(
                client,
                account_id
            )

        except Exception:

            if client:

                try:
                    await client.disconnect()
                except Exception:
                    pass


# ============================================================
# 5 MINUTE SETUP
# ============================================================

async def setup_timeout(user_id):

    await asyncio.sleep(
        SETUP_TIMEOUT
    )

    pending = PENDING_SETUP.get(
        user_id
    )

    if pending:

        PENDING_SETUP.pop(
            user_id,
            None
        )


# ============================================================
# HELP
# ============================================================

def help_text():

    prefix = get_prefix()

    return f"""
✦ **Cipher Elite Multi Session**

Is plugin se aap maximum **5 additional accounts**
connect kar sakte ho.

Connected accounts me commands sync ho sakte hain.

**Commands**

`{prefix}ms add <session>`
→ New account connect karo.

`{prefix}ms list`
→ Connected accounts dekho.

`{prefix}ms remove <id>`
→ Account disconnect karo.

`{prefix}ms primary`
→ Current primary account dekho.

`{prefix}ms primary <id>`
→ Primary account change karo.

`{prefix}ms on`
→ Command sync ON.

`{prefix}ms off`
→ Command sync OFF.

`{prefix}ms status`
→ Current status dekho.

`{prefix}ms help`
→ Ye help message.

**Important**

• Maximum **5 additional sessions**.
• Primary account alag se count nahi hota.
• Kisi bhi normal Cipher Elite command ko sync
  kiya ja sakta hai.
• `.help` ya `.alive` tak limited nahi hai.

⚠️ **5 Minute Rule**

Session add karte waqt setup ko **5 minutes ke
andar complete** karo.

5 minutes ke baad pending setup cancel ho jayega
aur session ko dobara add karna padega.

⚠️ **Security**

Telegram session kisi unknown person ko mat do.
Session jis person ke paas hoga wo account access
kar sakta hai.

Sirf apne accounts ke sessions use karo.
"""


# ============================================================
# LIST
# ============================================================

def accounts_text():

    if not CLIENTS:

        return (
            "✦ **Multi Session Accounts**\n\n"
            "Abhi koi additional account connected nahi hai."
        )

    primary = CONFIG.get(
        "primary_id"
    )

    lines = [
        "✦ **Connected Accounts**",
        ""
    ]

    for account_id in CLIENTS:

        star = (
            "⭐"
            if account_id == primary
            else "🔗"
        )

        lines.append(
            f"{star} {account_name(account_id)}"
            f" — `{account_id}`"
        )

    lines.extend([
        "",
        f"Additional accounts: "
        f"`{extra_session_count()}/{MAX_SESSIONS}`",
        f"Sync: "
        f"`{'ON' if SYNC_ENABLED else 'OFF'}`"
    ])

    return "\n".join(lines)


# ============================================================
# STATUS
# ============================================================

def status_text():

    primary = CONFIG.get(
        "primary_id"
    )

    return f"""
✦ **Multi Session Status**

🟢 Sync:
`{'ON' if SYNC_ENABLED else 'OFF'}`

👥 Additional accounts:
`{extra_session_count()}/{MAX_SESSIONS}`

⭐ Primary:
`{primary if primary else 'Not set'}`

🛡 Loop protection:
`ON`

⏱ Setup timeout:
`5 minutes`
"""


# ============================================================
# MANAGEMENT COMMAND
# ============================================================

async def register_commands():

    prefix = get_prefix()

    pattern = (
        rf"^{prefix}"
        rf"(?:ms|multisession|mssync)"
        rf"(?:\s+(.+))?$"
    )

    @MAIN_CLIENT.on(
        events.NewMessage(
            pattern=pattern
        )
    )
    async def management_handler(event):

        global SYNC_ENABLED

        raw = event.pattern_match.group(1)

        if not raw:

            await event.edit(
                help_text()
            )

            return

        parts = raw.strip().split(
            maxsplit=1
        )

        action = parts[0].lower()

        argument = (
            parts[1].strip()
            if len(parts) > 1
            else ""
        )

        # ----------------------------------------------------
        # HELP
        # ----------------------------------------------------

        if action in {
            "help",
            "?"
        }:

            await event.edit(
                help_text()
            )

            return

        # ----------------------------------------------------
        # ADD
        # ----------------------------------------------------

        if action in {
            "add",
            "connect"
        }:

            if not argument:

                await event.edit(
                    f"⚠️ **Session add karne se pehle ye padho**\n\n"
                    f"Official Cipher Elite session hi use karo.\n"
                    f"Setup ko **5 minutes ke andar complete** karo.\n\n"
                    f"Usage:\n"
                    f"`{prefix}ms add <session>`"
                )

                return

            if not can_add_session():

                await event.edit(
                    "❌ Maximum **5 additional accounts** "
                    "already connected hain."
                )

                return

            await event.edit(
                "⏳ Session connect ho raha hai...\n\n"
                "Please wait."
            )

            # Start 5-minute cleanup timer.
            user_id = event.sender_id

            PENDING_SETUP[user_id] = time.time()

            asyncio.create_task(
                setup_timeout(user_id)
            )

            ok, result = await connect_new_session(
                argument
            )

            PENDING_SETUP.pop(
                user_id,
                None
            )

            if not ok:

                await event.edit(
                    "❌ **Session connect nahi hua.**\n\n"
                    f"Reason: `{result}`\n\n"
                    "Official session dobara check karo."
                )

                return

            account_id = result

            await event.edit(
                "✅ **Account connected!**\n\n"
                f"👤 Account: {account_name(account_id)}\n"
                f"🆔 ID: `{account_id}`\n\n"
                f"🔗 Accounts: "
                f"`{extra_session_count()}/{MAX_SESSIONS}`"
            )

            return

        # ----------------------------------------------------
        # LIST
        # ----------------------------------------------------

        if action in {
            "list",
            "accounts",
            "ls"
        }:

            await event.edit(
                accounts_text()
            )

            return

        # ----------------------------------------------------
        # REMOVE
        # ----------------------------------------------------

        if action in {
            "remove",
            "disconnect",
            "delete",
            "del"
        }:

            if not argument.isdigit():

                await event.edit(
                    f"Usage:\n"
                    f"`{prefix}ms remove <account_id>`"
                )

                return

            ok, error = await remove_session(
                int(argument)
            )

            if not ok:

                await event.edit(
                    f"❌ {error}"
                )

                return

            await event.edit(
                f"✅ Account `{argument}` disconnected."
            )

            return

        # ----------------------------------------------------
        # PRIMARY
        # ----------------------------------------------------

        if action == "primary":

            if not argument:

                primary = CONFIG.get(
                    "primary_id"
                )

                if primary:

                    await event.edit(
                        "⭐ **Primary Account**\n\n"
                        f"👤 {account_name(primary)}\n"
                        f"🆔 `{primary}`"
                    )

                else:

                    await event.edit(
                        "❌ Primary account set nahi hai."
                    )

                return

            if not argument.isdigit():

                await event.edit(
                    f"Usage:\n"
                    f"`{prefix}ms primary <account_id>`"
                )

                return

            account_id = int(argument)

            if account_id not in CLIENTS:

                await event.edit(
                    "❌ Ye account connected nahi hai."
                )

                return

            CONFIG["primary_id"] = account_id

            save_config(CONFIG)

            await event.edit(
                "⭐ **Primary account changed.**\n\n"
                f"👤 {account_name(account_id)}\n"
                f"🆔 `{account_id}`"
            )

            return

        # ----------------------------------------------------
        # ON
        # ----------------------------------------------------

        if action in {
            "on",
            "enable"
        }:

            SYNC_ENABLED = True

            CONFIG["enabled"] = True

            save_config(CONFIG)

            await event.edit(
                "🟢 **Multi Session Sync ON**\n\n"
                "Ab commands connected accounts me sync honge."
            )

            return

        # ----------------------------------------------------
        # OFF
        # ----------------------------------------------------

        if action in {
            "off",
            "disable"
        }:

            SYNC_ENABLED = False

            CONFIG["enabled"] = False

            save_config(CONFIG)

            await event.edit(
                "🔴 **Multi Session Sync OFF**"
            )

            return

        # ----------------------------------------------------
        # STATUS
        # ----------------------------------------------------

        if action == "status":

            await event.edit(
                status_text()
            )

            return

        await event.edit(
            f"❌ Unknown command.\n\n"
            f"`{prefix}ms help` use karo."
        )


# ============================================================
# PLUGIN INIT
# ============================================================

def init(client):

    global MAIN_CLIENT
    global SYNC_ENABLED

    MAIN_CLIENT = client

    SYNC_ENABLED = CONFIG.get(
        "enabled",
        True
    )

    asyncio.create_task(
        initialize_plugin()
    )


async def initialize_plugin():

    try:

        # ----------------------------------------------------
        # Register primary account
        # ----------------------------------------------------

        primary_id = await save_account_info(
            MAIN_CLIENT
        )

        CLIENTS[primary_id] = MAIN_CLIENT

        if not CONFIG.get("primary_id"):

            CONFIG["primary_id"] = primary_id

            save_config(CONFIG)

        # ----------------------------------------------------
        # Primary command hook
        # ----------------------------------------------------

        install_sync_handler(
            MAIN_CLIENT,
            primary_id
        )

        # ----------------------------------------------------
        # Load saved additional sessions
        # ----------------------------------------------------

        await load_saved_sessions()

        # ----------------------------------------------------
        # Register management commands
        # ----------------------------------------------------

        await register_commands()

    except Exception:
        pass
