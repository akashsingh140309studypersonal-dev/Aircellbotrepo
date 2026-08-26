import logging
import os
import sqlite3
import datetime
import re
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeDefault, BotCommandScopeChat
from telegram.helpers import escape_markdown
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.CRITICAL)
logging.getLogger("httpx").setLevel(logging.CRITICAL)
logging.getLogger("httpcore").setLevel(logging.CRITICAL)
logging.getLogger("telegram").setLevel(logging.CRITICAL)

TOKEN = "8932328515:AAEj0Mt9dtdZmOhel6dH9EHCNWxatQgdwNc"
OWNERS = [7943423987, 8441919637]

# Conversation States
MEDIA_TEXT, OPTION_COUNT, OPTION_DETAILS, CORRECT_ANS, TIMER_INPUT, CHANNEL_SELECT = range(6)
MAT_TITLE, MAT_FILES = range(6, 8)
REQ_TITLE, REQ_FILES = range(8, 10)
RESET_PASSWD = range(10, 11)
ADD_FEATURE, EDIT_FEATURE_SELECT, EDIT_FEATURE_CODE = range(11, 14)

conn = sqlite3.connect("quiz_bot.db", check_same_thread=False)
cursor = conn.cursor()

# Database Setup
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    points INTEGER DEFAULT 0,
    diamonds INTEGER DEFAULT 0,
    attempts INTEGER DEFAULT 0,
    correct INTEGER DEFAULT 0,
    incorrect INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS channels (
    channel_id TEXT PRIMARY KEY,
    title TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    file_id TEXT,
    file_type TEXT,
    text_content TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS requested_materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    title TEXT,
    file_id TEXT,
    file_type TEXT,
    text_content TEXT,
    status TEXT DEFAULT 'pending'
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS quiz_history (
    quiz_id TEXT PRIMARY KEY,
    admin_id INTEGER,
    admin_name TEXT,
    chat_id TEXT,
    text_content TEXT,
    posted_at TIMESTAMP,
    status TEXT DEFAULT 'pending',
    skip_leaderboard INTEGER DEFAULT 0
)
""")

for owner in OWNERS:
    cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (owner,))
conn.commit()

active_quizzes = {}

def is_owner(user_id):
    return user_id in OWNERS

def is_admin(user_id):
    if is_owner(user_id):
        return True
    cursor.execute("SELECT user_id FROM admins WHERE user_id = ?", (user_id,))
    return cursor.fetchone() is not None

def get_all_channels():
    cursor.execute("SELECT channel_id, title FROM channels")
    return cursor.fetchall()

def parse_time_to_seconds(time_str):
    time_str = time_str.strip().lower()
    if time_str == "skip":
        return "skip"
    match = re.match(r"^([0-9.]+)\s*([hms]?)$", time_str)
    if not match:
        return None
    val, unit = match.groups()
    try:
        val = float(val)
    except ValueError:
        return None

    if unit == 's':
        return int(val)
    elif unit == 'm':
        return int(val * 60)
    elif unit == 'h' or unit == '':
        return int(val * 3600)
    return None

def format_username(username):
    if not username or username == "NoUsername":
        return "No Username"
    clean_uname = escape_markdown(username, version=1)
    return f"@{clean_uname}"

def safe_name(name):
    if not name:
        return "User"
    return escape_markdown(name, version=1)

def find_user(target):
    if target.startswith("@"):
        cursor.execute("SELECT user_id, full_name FROM users WHERE username = ?", (target[1:],))
    else:
        try:
            cursor.execute("SELECT user_id, full_name FROM users WHERE user_id = ?", (int(target),))
        except ValueError:
            return None, None
    res = cursor.fetchone()
    return res if res else (None, None)

def execute_custom_code():
    if os.path.exists("custom_features.py"):
        try:
            with open("custom_features.py", "r", encoding="utf-8") as f:
                code = f.read()
            exec(code, globals())
        except Exception as e:
            print(f"Error loading custom features: {e}")

# Command Scope Configuration
async def setup_bot_commands(app: Application):
    member_commands = [
        BotCommand("start", "🌟 Start bot / Main Menu"),
        BotCommand("help", "❓ Help & Command List"),
        BotCommand("myprofile", "👤 View your profile stats"),
        BotCommand("profile", "🔍 View user profile"),
        BotCommand("top", "🏆 Leaderboard Top 30"),
        BotCommand("materials", "📚 Download study notes"),
        BotCommand("reqmaterial", "📥 Request material"),
    ]

    admin_commands = member_commands + [
        BotCommand("newquiz", "📝 Create Question"),
        BotCommand("upmaterials", "📤 Upload materials"),
        BotCommand("apvmaterial", "✅ Review requested materials"),
        BotCommand("removematerial", "🗑 Remove material"),
        BotCommand("cancel", "🚫 Cancel active process"),
    ]

    owner_commands = admin_commands + [
        BotCommand("addfeature", "⚡ Add custom feature code"),
        BotCommand("editfeature", "🛠 Edit custom code"),
        BotCommand("recover", "🔄 Smart recover users to DB"),
        BotCommand("giveowner", "👑 Grant Owner rights"),
        BotCommand("add", "➕ Manually add user to DB"),
        BotCommand("givepts", "➕ Give Points"),
        BotCommand("removepts", "➖ Remove Points"),
        BotCommand("users", "📊 Total registered members"),
        BotCommand("admins", "🛡 Show admins list"),
        BotCommand("giveadmin", "➕ Promote Admin"),
        BotCommand("removeadmin", "➖ Demote Admin"),
        BotCommand("postchannel", "📢 Manage posting channels"),
        BotCommand("recent", "📜 Recent 10 Quizzes"),
        BotCommand("allhistory", "📜 Full Quiz History"),
        BotCommand("resetall", "⚠️ Reset all stats"),
    ]

    await app.bot.set_my_commands(member_commands, scope=BotCommandScopeDefault())
    for owner_id in OWNERS:
        try:
            await app.bot.set_my_commands(owner_commands, scope=BotCommandScopeChat(chat_id=owner_id))
        except Exception:
            pass

# Smart Auto-Analyse Recover
async def recover_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    
    text = ""
    reply = update.message.reply_to_message
    if reply:
        text = reply.text or reply.caption or ""
    else:
        text = update.message.text.replace("/recover", "")

    if not text.strip():
        await update.message.reply_text("⚠️ **Smart Recover Usage:**\nReply to any text/list message or send text with `/recover`. I will automatically extract user IDs and usernames/names!", parse_mode="Markdown")
        return

    matches = re.findall(r'(\d{7,12})\s*[-:|~]*\s*@?([a-zA-Z0-9_]+)?', text)
    
    if not matches:
        await update.message.reply_text("❌ No valid User IDs found in the provided text.")
        return

    added_count = 0
    added_list = []

    for uid_str, uname in matches:
        uid = int(uid_str)
        clean_uname = uname if uname and not uname.isdigit() else "NoUsername"
        fname = f"Recovered User ({uid})"
        
        cursor.execute("INSERT OR REPLACE INTO users (user_id, username, full_name) VALUES (?, ?, ?)", (uid, clean_uname, fname))
        added_list.append(f"• `{uid}` ➔ `@{clean_uname}`")
        added_count += 1

    conn.commit()
    response = f"✅ **Smart Recovery Complete!**\nTotal `{added_count}` user entries saved to DB:\n\n" + "\n".join(added_list[:30])
    if len(added_list) > 30:
        response += f"\n...and {len(added_list)-30} more."
    await update.message.reply_text(response, parse_mode="Markdown")

async def add_user_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("⚠️ Usage: `/add <user_id> [username] [full_name]`", parse_mode="Markdown")
        return
    try:
        uid = int(context.args[0])
        uname = context.args[1].replace("@", "") if len(context.args) > 1 else "NoUsername"
        fname = " ".join(context.args[2:]) if len(context.args) > 2 else f"User_{uid}"
        
        cursor.execute("INSERT OR REPLACE INTO users (user_id, username, full_name) VALUES (?, ?, ?)", (uid, uname, fname))
        conn.commit()
        await update.message.reply_text(f"✅ User `{uid}` (`@{uname}`) added/updated successfully!", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ User ID must be numeric.")

# Custom Features Editing Only
async def addfeature_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    await update.message.reply_text("📝 **Code Addition Mode**\nSend the raw Python code to add to custom dynamic features:\n(Type /cancel to abort)", parse_mode="Markdown")
    return ADD_FEATURE

async def addfeature_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text
    with open("custom_features.py", "a", encoding="utf-8") as f:
        f.write("\n" + code + "\n")
    execute_custom_code()
    await update.message.reply_text("✅ Dynamic feature code added and executed successfully!", parse_mode="Markdown")
    return ConversationHandler.END

async def editfeature_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    
    if not os.path.exists("custom_features.py"):
        await update.message.reply_text("❌ No custom features found to edit. Add features via /addfeature first.", parse_mode="Markdown")
        return ConversationHandler.END

    with open("custom_features.py", "r", encoding="utf-8") as f:
        code_snippet = f.read()

    msg_text = (
        f"📜 **Current Custom Features Code Preview:**\n"
        f"```python\n{code_snippet[:3500]}\n```\n\n"
        f"📌 Send the **NEW replacement Python code** for `custom_features.py`:"
    )
    await update.message.reply_text(msg_text, parse_mode="Markdown")
    return EDIT_FEATURE_CODE

async def editfeature_save_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text
    with open("custom_features.py", "w", encoding="utf-8") as f:
        f.write(code + "\n")
    execute_custom_code()
    await update.message.reply_text("✅ Custom features code updated successfully!", parse_mode="Markdown")
    return ConversationHandler.END

# Start Command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uname = user.username if user.username else "NoUsername"
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)",
        (user.id, uname, user.full_name)
    )
    cursor.execute("UPDATE users SET username = ?, full_name = ? WHERE user_id = ?", (uname, user.full_name, user.id))
    conn.commit()

    quote = "💡 *\"Success isn't always about greatness. It's about consistency. Consistent hard work leads to success. Greatness will come!\"*"
    
    welcome_text = (
        f"✨ ━━━━━━━━━━━━━━━━━━━━ ✨\n"
        f"👋 **Welcome, {safe_name(user.first_name)}!**\n\n"
        f"{quote}\n"
        f"✨ ━━━━━━━━━━━━━━━━━━━━ ✨\n\n"
        f"🎯 *Ready to boost your learning journey today?*\n"
        f"Click **❓ Help Menu** below to view available commands."
    )

    buttons = [
        [InlineKeyboardButton("📚 Study Materials", callback_data="btn_materials"), InlineKeyboardButton("🏆 Leaderboard", callback_data="btn_top")],
        [InlineKeyboardButton("👤 My Profile", callback_data="btn_profile"), InlineKeyboardButton("❓ Help Menu", callback_data="btn_help")]
    ]

    await update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

# Help Command
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = safe_name(update.effective_user.first_name)
    
    menu_text = (
        f"✨ ──────────────────────── ✨\n"
        f"👋 Welcome {user_name} 🎀!\n"
        f"✨ ──────────────────────── ✨\n\n"
    )

    if is_owner(user_id):
        menu_text += (
            "👑 ━━━━ OWNER CONTROLS ━━━━ 👑\n"
            "👑 ❯ /addfeature • Add live bot code feature\n"
            "👑 ❯ /editfeature • Edit custom features code\n"
            "👑 ❯ /recover • Recover bulk users to DB\n"
            "👑 ❯ /giveowner <ID> • Grant owner rights\n"
            "👑 ❯ /add <ID> [username] [name] • Add user to DB\n"
            "👑 ❯ /givepts <ID/@user> <pts> • Add Points\n"
            "👑 ❯ /removepts <ID/@user> <pts> • Deduct Points\n"
            "👑 ❯ /users • Registered members count\n"
            "👑 ❯ /admins • Active admins list\n"
            "👑 ❯ /giveadmin <ID/@user> • Grant admin role\n"
            "👑 ❯ /removeadmin <ID/@user> • Revoke admin role\n"
            "👑 ❯ /postchannel • Manage posting channels\n"
            "👑 ❯ /recent • Last 10 quiz history\n"
            "👑 ❯ /allhistory • Complete quiz history\n"
            "👑 ❯ /resetall • Reset all stats (Password Protected)\n\n"
        )
    if is_admin(user_id):
        menu_text += (
            "🛠 ━━━━ ADMIN CONTROLS ━━━━ 🛠\n"
            "🛠 ❯ /newquiz • Create Question\n"
            "🛠 ❯ /upmaterials • Upload material files\n"
            "🛠 ❯ /apvmaterial • Review user requested materials\n"
            "🛠 ❯ /removematerial • Remove material by title\n"
            "🛠 ❯ /cancel • Abort active process\n\n"
        )

    menu_text += (
        "👤 ━━━━ MEMBER CONTROLS ━━━━ 👤\n"
        "👤 ❯ /myprofile • View personal stats\n"
        "👤 ❯ /profile <ID/@user> • Inspect member profile\n"
        "👤 ❯ /top • View Top 30 leaderboard\n"
        "👤 ❯ /materials • Download study notes\n"
        "👤 ❯ /reqmaterial • Request new study material\n\n"
        "✨ ──────────────────────── ✨"
    )

    if update.callback_query:
        await update.callback_query.message.reply_text(menu_text, parse_mode="Markdown")
    else:
        await update.message.reply_text(menu_text, parse_mode="Markdown")

async def give_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("⚠️ Usage: `/giveowner <user_id>`", parse_mode="Markdown")
        return
    try:
        new_owner = int(context.args[0])
        if new_owner not in OWNERS:
            OWNERS.append(new_owner)
            cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (new_owner,))
            conn.commit()
            await update.message.reply_text(f"👑 Added `{new_owner}` as Owner successfully!", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ User ID must be numeric.")

# Buttons Quick Callback Router
async def start_buttons_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "btn_materials":
        await materials_list(update, context)
    elif query.data == "btn_top":
        await top_leaderboard(update, context)
    elif query.data == "btn_profile":
        await view_profile(update, context)
    elif query.data == "btn_help":
        await help_command(update, context)

async def users_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    cursor.execute("SELECT user_id, full_name, username FROM users")
    all_users = cursor.fetchall()
    
    if not all_users:
        await update.message.reply_text("📊 No registered users found.")
        return

    msg = f"📊 **Registered Members Count ({len(all_users)}):**\n\n"
    for u in all_users:
        u_disp = format_username(u[2])
        msg += f"• {safe_name(u[1])} ({u_disp}) - ID: `{u[0]}`\n"
    
    if len(msg) > 4000:
        for x in range(0, len(msg), 4000):
            await update.message.reply_text(msg[x:x+4000], parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, parse_mode="Markdown")

async def post_channel_manager(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if len(context.args) >= 1:
        ch_id = context.args[0]
        title = " ".join(context.args[1:]) if len(context.args) > 1 else ch_id
        
        # Try retrieving title via API if not provided explicitly
        if title == ch_id:
            try:
                chat = await context.bot.get_chat(ch_id)
                if chat and chat.title:
                    title = chat.title
            except Exception:
                pass

        cursor.execute("INSERT OR REPLACE INTO channels (channel_id, title) VALUES (?, ?)", (ch_id, title))
        conn.commit()
        await update.message.reply_text(f"✅ Added channel **{safe_name(title)}** (`{ch_id}`) to posting list!", parse_mode="Markdown")
        return
    
    channels = get_all_channels()
    msg = "📢 **Registered Posting Channels List:**\n\n"
    buttons = []
    if not channels:
        msg += "No registered channel found.\nUsage: `/postchannel <channel_id/@username> [Display_Name]`"
    else:
        for ch_id, title in channels:
            msg += f"• **{safe_name(title)}** (`{ch_id}`)\n"
            buttons.append([InlineKeyboardButton(f"🗑 Remove {title}", callback_data=f"remch_{ch_id}")])
    
    markup = InlineKeyboardMarkup(buttons) if buttons else None
    await update.message.reply_text(msg, reply_markup=markup, parse_mode="Markdown")

async def channel_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_owner(query.from_user.id):
        return
    if query.data.startswith("remch_"):
        ch_id = query.data.replace("remch_", "")
        cursor.execute("DELETE FROM channels WHERE channel_id = ?", (ch_id,))
        conn.commit()
        await query.edit_message_text(f"🗑 Channel `{ch_id}` removed successfully.", parse_mode="Markdown")

async def give_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text("⚠️ Usage: `/givepts <user_id or @username> <points>`", parse_mode="Markdown")
        return
    target, pts_str = context.args[0], context.args[1]
    try:
        pts = int(pts_str)
    except ValueError:
        await update.message.reply_text("❌ Points must be a valid number.", parse_mode="Markdown")
        return

    uid, name = find_user(target)
    if not uid:
        await update.message.reply_text("❌ User not found in database.", parse_mode="Markdown")
        return

    cursor.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (pts, uid))
    conn.commit()
    await update.message.reply_text(f"✅ Added `{pts}` points to **{safe_name(name)}** (`{uid}`).", parse_mode="Markdown")

async def remove_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text("⚠️ Usage: `/removepts <user_id or @username> <points>`", parse_mode="Markdown")
        return
    target, pts_str = context.args[0], context.args[1]
    try:
        pts = int(pts_str)
    except ValueError:
        await update.message.reply_text("❌ Points must be a valid number.", parse_mode="Markdown")
        return

    uid, name = find_user(target)
    if not uid:
        await update.message.reply_text("❌ User not found in database.", parse_mode="Markdown")
        return

    cursor.execute("UPDATE users SET points = MAX(0, points - ?) WHERE user_id = ?", (pts, uid))
    conn.commit()
    await update.message.reply_text(f"🗑 Deducted `{pts}` points from **{safe_name(name)}** (`{uid}`).", parse_mode="Markdown")

async def admins_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    cursor.execute("SELECT admins.user_id, users.username, users.full_name FROM admins LEFT JOIN users ON admins.user_id = users.user_id")
    all_admins = cursor.fetchall()
    msg = f"🛡️ **Admin List ({len(all_admins)}):**\n\n"
    for uid, uname, fname in all_admins:
        u_disp = format_username(uname) if uname else "No Username"
        name_disp = safe_name(fname) if fname else "User"
        msg += f"• {name_disp} ({u_disp}) - ID: `{uid}`\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def give_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("⚠️ Usage: `/giveadmin <user_id or @username>`", parse_mode="Markdown")
        return
    target = context.args[0]
    uid, name = find_user(target)
    if not uid:
        try:
            uid = int(target)
            name = "Direct Admin"
        except ValueError:
            await update.message.reply_text("❌ User ID not found and input is not numeric.", parse_mode="Markdown")
            return

    cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (uid,))
    conn.commit()
    await update.message.reply_text(f"✅ Granted Admin privileges to **{safe_name(name)}** (`{uid}`).", parse_mode="Markdown")

async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("⚠️ Usage: `/removeadmin <user_id or @username>`", parse_mode="Markdown")
        return
    target = context.args[0]
    uid, name = find_user(target)
    if not uid:
        try:
            uid = int(target)
            name = "User"
        except ValueError:
            await update.message.reply_text("❌ User not found.", parse_mode="Markdown")
            return

    if uid in OWNERS:
        await update.message.reply_text("❌ Owner access cannot be removed.")
        return

    cursor.execute("DELETE FROM admins WHERE user_id = ?", (uid,))
    conn.commit()
    await update.message.reply_text(f"🗑 Revoked Admin privileges from **{safe_name(name)}** (`{uid}`).", parse_mode="Markdown")

async def resetall_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    await update.message.reply_text("⚠️ **WARNING:** Reset ALL statistics?\nPlease enter Admin Password to confirm:", parse_mode="Markdown")
    return RESET_PASSWD

async def resetall_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    if password == "2kAircell":
        cursor.execute("UPDATE users SET points = 0, diamonds = 0, attempts = 0, correct = 0, incorrect = 0")
        conn.commit()
        await update.message.reply_text("✅ **ALL USER DATA RESET TO ZERO!**", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Incorrect Password. Reset aborted.", parse_mode="Markdown")
    return ConversationHandler.END

async def view_profile(update: Update, context: ContextTypes.DEFAULT_TYPE, target_uid=None, target_uname=None):
    if target_uname:
        cursor.execute("SELECT * FROM users WHERE username = ?", (target_uname,))
    elif target_uid:
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (target_uid,))
    else:
        user = update.effective_user
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user.id,))

    user_data = cursor.fetchone()
    if not user_data:
        await update.message.reply_text("❌ Profile not found.", parse_mode="Markdown")
        return

    uid, uname, fname, pts, dia, att, corr, incorr = user_data
    acc = (corr / att * 100) if att > 0 else 0.0
    u_disp = format_username(uname)

    profile_text = (
        f"═══════════════════════════\n"
        f"👤  **MEMBER PROFILE STATS**\n"
        f"═══════════════════════════\n\n"
        f"🔹 **Name:** {safe_name(fname)}\n"
        f"🔹 **Username:** {u_disp}\n"
        f"🆔 **User ID:** `{uid}`\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 **Total Points:** `{pts}` Pts\n"
        f"🎯 **Accuracy:** `{acc:.1f}%`\n"
        f"📝 **Questions Attempted:** `{att}`\n"
        f"✅ **Correct Answers:** `{corr}`\n"
        f"❌ **Incorrect Answers:** `{incorr}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    if update.callback_query:
        await update.callback_query.message.reply_text(profile_text, parse_mode="Markdown")
    else:
        await update.message.reply_text(profile_text, parse_mode="Markdown")

async def my_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await view_profile(update, context)

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await view_profile(update, context)
        return
    target = context.args[0]
    if target.startswith("@"):
        await view_profile(update, context, target_uname=target[1:])
    else:
        try:
            await view_profile(update, context, target_uid=int(target))
        except ValueError:
            await update.message.reply_text("❌ Invalid User ID or Username.")

async def top_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cursor.execute("SELECT full_name, username, points FROM users ORDER BY points DESC LIMIT 30")
    top_users = cursor.fetchall()
    user_is_admin = is_admin(update.effective_user.id)

    if not top_users:
        msg_text = "🏆 No users found in leaderboard yet."
    else:
        msg_text = "🏆 **Top 30 Leaderboard:**\n\n"
        for idx, (fname, uname, pts) in enumerate(top_users, 1):
            s_fname = safe_name(fname)
            if user_is_admin:
                u_disp = format_username(uname)
                msg_text += f"{idx}. **{s_fname}** ({u_disp}) - `{pts}` pts\n"
            else:
                msg_text += f"{idx}. **{s_fname}** - `{pts}` pts\n"

    if update.callback_query:
        await update.callback_query.message.reply_text(msg_text, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg_text, parse_mode="Markdown")

async def reqmaterials_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['req'] = {'title': '', 'files': []}
    await update.message.reply_text("📝 **Material Request Mode**\nEnter Title/Subject:\n(Type /cancel to abort)", parse_mode="Markdown")
    return REQ_TITLE

async def reqmaterials_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['req']['title'] = update.message.text
    await update.message.reply_text("📥 Send files/photos. Send **/done** when finished.", parse_mode="Markdown")
    return REQ_FILES

async def reqmaterials_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    req = context.user_data['req']
    msg = update.message
    file_id = None
    file_type = "text"
    text_content = msg.text or msg.caption or ""

    if msg.document:
        file_id = msg.document.file_id
        file_type = "document"
    elif msg.photo:
        file_id = msg.photo[-1].file_id
        file_type = "photo"
    elif msg.video:
        file_id = msg.video.file_id
        file_type = "video"

    req['files'].append({'file_id': file_id, 'file_type': file_type, 'text_content': text_content})
    await update.message.reply_text(f"✅ File #{len(req['files'])} received! Send more or write **/done**.", parse_mode="Markdown")
    return REQ_FILES

async def reqmaterials_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    req = context.user_data.get('req', {})
    title, files = req.get('title'), req.get('files', [])
    if not files:
        await update.message.reply_text("❌ No files provided.")
        return ConversationHandler.END

    user = update.effective_user
    for item in files:
        cursor.execute(
            "INSERT INTO requested_materials (user_id, title, file_id, file_type, text_content) VALUES (?, ?, ?, ?, ?)",
            (user.id, title, item['file_id'], item['file_type'], item['text_content'])
        )
    conn.commit()
    await update.message.reply_text(f"🎉 Request for **'{safe_name(title)}'** submitted!", parse_mode="Markdown")
    context.user_data.clear()
    return ConversationHandler.END

async def apvmaterial_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    cursor.execute("SELECT DISTINCT title FROM requested_materials WHERE status = 'pending'")
    rows = cursor.fetchall()
    if not rows:
        await update.message.reply_text("📥 No pending material requests.")
        return

    buttons = []
    for (title,) in rows:
        title_sub = title[:20]
        buttons.append([
            InlineKeyboardButton(f"📄 {title}", callback_data=f"viewreq_{title_sub}"),
            InlineKeyboardButton("✅ Approve", callback_data=f"apvreq_{title_sub}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"rejreq_{title_sub}")
        ])
    await update.message.reply_text("📝 **Pending Material Requests:**", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

async def apvmaterial_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    data = query.data
    if data.startswith("viewreq_"):
        t_key = data.replace("viewreq_", "")
        cursor.execute("SELECT title, file_id, file_type, text_content FROM requested_materials WHERE title LIKE ? AND status='pending'", (f"{t_key}%",))
        rows = cursor.fetchall()
        for title, file_id, file_type, text_content in rows:
            caption = f"📋 Request: **{safe_name(title)}**\n\n{text_content}"
            if file_type == "document":
                await context.bot.send_document(chat_id=query.message.chat_id, document=file_id, caption=caption, parse_mode="Markdown")
            elif file_type == "photo":
                await context.bot.send_photo(chat_id=query.message.chat_id, photo=file_id, caption=caption, parse_mode="Markdown")
            elif file_type == "video":
                await context.bot.send_video(chat_id=query.message.chat_id, video=file_id, caption=caption, parse_mode="Markdown")
            else:
                await context.bot.send_message(chat_id=query.message.chat_id, text=caption, parse_mode="Markdown")
    elif data.startswith("apvreq_"):
        t_key = data.replace("apvreq_", "")
        cursor.execute("SELECT title, file_id, file_type, text_content FROM requested_materials WHERE title LIKE ? AND status='pending'", (f"{t_key}%",))
        rows = cursor.fetchall()
        for title, file_id, file_type, text_content in rows:
            cursor.execute("INSERT INTO materials (title, file_id, file_type, text_content) VALUES (?, ?, ?, ?)", (title, file_id, file_type, text_content))
        cursor.execute("UPDATE requested_materials SET status='approved' WHERE title LIKE ?", (f"{t_key}%",))
        conn.commit()
        await query.edit_message_text(f"✅ Material request approved & published!")
    elif data.startswith("rejreq_"):
        t_key = data.replace("rejreq_", "")
        cursor.execute("UPDATE requested_materials SET status='rejected' WHERE title LIKE ?", (f"{t_key}%",))
        conn.commit()
        await query.edit_message_text(f"❌ Material request rejected.")

async def upmaterials_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    context.user_data['mat'] = {'title': '', 'files': []}
    await update.message.reply_text("📚 **Upload Mode**\nEnter Title:\n(Type /cancel to abort)", parse_mode="Markdown")
    return MAT_TITLE

async def upmaterials_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['mat']['title'] = update.message.text
    await update.message.reply_text("📥 Send files. Send **/done** when finished.", parse_mode="Markdown")
    return MAT_FILES

async def upmaterials_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mat = context.user_data['mat']
    msg = update.message
    file_id = None
    file_type = "text"
    text_content = msg.text or msg.caption or ""

    if msg.document:
        file_id = msg.document.file_id
        file_type = "document"
    elif msg.photo:
        file_id = msg.photo[-1].file_id
        file_type = "photo"
    elif msg.video:
        file_id = msg.video.file_id
        file_type = "video"

    mat['files'].append({'file_id': file_id, 'file_type': file_type, 'text_content': text_content})
    await update.message.reply_text(f"✅ File #{len(mat['files'])} received! Send more or write **/done**.", parse_mode="Markdown")
    return MAT_FILES

async def upmaterials_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mat = context.user_data.get('mat', {})
    title, files = mat.get('title'), mat.get('files', [])
    if not files:
        await update.message.reply_text("❌ No files uploaded.")
        return ConversationHandler.END

    for item in files:
        cursor.execute(
            "INSERT INTO materials (title, file_id, file_type, text_content) VALUES (?, ?, ?, ?)",
            (title, item['file_id'], item['file_type'], item['text_content'])
        )
    conn.commit()
    await update.message.reply_text(f"🎉 Material **'{safe_name(title)}'** saved!", parse_mode="Markdown")
    context.user_data.clear()
    return ConversationHandler.END

async def remove_material_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("⚠️ Usage: `/removematerial <Title or ID>`", parse_mode="Markdown")
        return
    target = " ".join(context.args)
    if target.isdigit():
        cursor.execute("DELETE FROM materials WHERE id = ?", (int(target),))
    else:
        cursor.execute("DELETE FROM materials WHERE title LIKE ?", (f"%{target}%",))
    deleted = cursor.rowcount
    conn.commit()
    if deleted > 0:
        await update.message.reply_text(f"🗑️ Removed `{deleted}` file(s).", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ Material not found.", parse_mode="Markdown")

async def materials_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cursor.execute("SELECT DISTINCT title FROM materials")
    rows = cursor.fetchall()
    if not rows:
        msg_text = "📁 No study materials uploaded yet."
        markup = None
    else:
        msg_text = "📚 **Available Study Materials:**"
        buttons = []
        for (title,) in rows:
            row_btns = [InlineKeyboardButton(f"📄 {title}", callback_data=f"getmat_{title[:30]}")]
            if is_admin(update.effective_user.id):
                row_btns.append(InlineKeyboardButton("🗑 Remove", callback_data=f"deltitle_{title[:30]}")) 
            buttons.append(row_btns)
        markup = InlineKeyboardMarkup(buttons)

    if update.callback_query:
        await update.callback_query.message.reply_text(msg_text, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg_text, reply_markup=markup, parse_mode="Markdown")

async def materials_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("getmat_"):
        title_key = data.replace("getmat_", "")
        cursor.execute("SELECT title, file_id, file_type, text_content FROM materials WHERE title LIKE ?", (f"{title_key}%",))
        rows = cursor.fetchall()
        for title, file_id, file_type, text_content in rows:
            caption_text = f"📚 **{safe_name(title)}**\n\n{text_content}"
            if file_type == "document":
                await context.bot.send_document(chat_id=query.message.chat_id, document=file_id, caption=caption_text, parse_mode="Markdown")
            elif file_type == "photo":
                await context.bot.send_photo(chat_id=query.message.chat_id, photo=file_id, caption=caption_text, parse_mode="Markdown")
            elif file_type == "video":
                await context.bot.send_video(chat_id=query.message.chat_id, video=file_id, caption=caption_text, parse_mode="Markdown")
            else:
                await context.bot.send_message(chat_id=query.message.chat_id, text=caption_text, parse_mode="Markdown")
    elif data.startswith("deltitle_"):
        if not is_admin(query.from_user.id):
            return
        title_key = data.replace("deltitle_", "")
        cursor.execute("DELETE FROM materials WHERE title LIKE ?", (f"{title_key}%",))
        conn.commit()
        await query.edit_message_text("🗑 All files under this material deleted.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("🚫 Process cancelled.")
    return ConversationHandler.END

# Quiz Creation Flow
async def new_quiz_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    context.user_data['quiz'] = {}
    await update.message.reply_text("📸 Send Photo or Text for Question:\n(Type /cancel anytime to abort)")
    return MEDIA_TEXT

async def get_media_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quiz = context.user_data['quiz']
    if update.message.photo:
        quiz['photo'] = update.message.photo[-1].file_id
        quiz['text'] = update.message.caption or ""
    else:
        quiz['photo'] = None
        quiz['text'] = update.message.text

    buttons = [
        [InlineKeyboardButton("1️⃣ Single Choice MCQ", callback_data="type_mcq")],
        [InlineKeyboardButton("☑️ Multiple Choice MCQ (Multi-Select)", callback_data="type_mcq_multi")],
        [InlineKeyboardButton("🔢 Integer/Decimal Numpad", callback_data="type_num")]
    ]
    await update.message.reply_text("Select Question Type:", reply_markup=InlineKeyboardMarkup(buttons))
    return OPTION_COUNT

async def set_type_or_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    quiz = context.user_data['quiz']
    
    if query.data == "type_num":
        quiz['type'] = "NUMERIC"
        await query.message.reply_text("Enter correct answer (e.g., 100 or 1.15):")
        return CORRECT_ANS
    elif query.data == "type_mcq":
        quiz['type'] = "MCQ"
        await query.message.reply_text("Enter total options required (2 to 10):")
        return OPTION_COUNT
    elif query.data == "type_mcq_multi":
        quiz['type'] = "MCQ_MULTI"
        await query.message.reply_text("Enter total options required (2 to 10):")
        return OPTION_COUNT

async def process_option_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quiz = context.user_data['quiz']
    if quiz.get('type') in ["MCQ", "MCQ_MULTI"] and 'max_options' not in quiz:
        try:
            count = int(update.message.text)
            if not (2 <= count <= 10):
                raise ValueError
            quiz['max_options'] = count
            quiz['options'] = {}
            quiz['current_opt'] = 0
            labels = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J']
            await update.message.reply_text(f"Enter option **{labels[0]}** (or type 'null'):", parse_mode="Markdown")
            return OPTION_DETAILS
        except ValueError:
            await update.message.reply_text("Enter valid number from 2 to 10.")
            return OPTION_COUNT

async def process_option_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quiz = context.user_data['quiz']
    labels = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J']
    idx = quiz['current_opt']

    user_input = update.message.text.strip()
    if user_input.lower() == "null":
        quiz['options'][labels[idx]] = f"Option {labels[idx]}"
    else:
        quiz['options'][labels[idx]] = user_input

    quiz['current_opt'] += 1

    if quiz['current_opt'] < quiz['max_options']:
        next_label = labels[quiz['current_opt']]
        await update.message.reply_text(f"Enter option **{next_label}** (or type 'null'):", parse_mode="Markdown")
        return OPTION_DETAILS
    else:
        if quiz['type'] == "MCQ_MULTI":
            await update.message.reply_text("Which options are correct? (e.g. Write `AB` or `A,C` for multiple answers):", parse_mode="Markdown")
        else:
            await update.message.reply_text("Which option is correct? (e.g. A or B):", parse_mode="Markdown")
        return CORRECT_ANS

async def process_correct_ans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quiz = context.user_data['quiz']
    ans = update.message.text.strip().upper()

    if quiz['type'] in ["MCQ", "MCQ_MULTI"]:
        clean_ans = "".join(sorted(list(set(re.findall(r'[A-J]', ans)))))
        quiz['correct_ans'] = clean_ans
    else:
        quiz['correct_ans'] = ans

    await update.message.reply_text("⏱ Enter ranking timer (e.g. `10s`, `2m`, `1h`) OR type **`skip`** to hide leaderboard completely:", parse_mode="Markdown")
    return TIMER_INPUT

async def select_posting_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quiz = context.user_data['quiz']
    time_res = parse_time_to_seconds(update.message.text)

    if time_res is None:
        await update.message.reply_text("❌ Invalid format! Examples:\n• `30s` (30 seconds)\n• `5m` (5 minutes)\n• `skip` (No Leaderboard)", parse_mode="Markdown")
        return TIMER_INPUT

    quiz['skip_lb'] = 1 if time_res == "skip" else 0
    quiz['seconds'] = time_res if not quiz['skip_lb'] else 0

    channels = get_all_channels()
    if not channels:
        quiz['target_channels'] = [update.effective_chat.id]
        return await publish_quiz(update, context)

    buttons = []
    for ch_id, title in channels:
        display_text = f"📢 {title}" if title != ch_id else f"📢 Channel ({ch_id})"
        buttons.append([InlineKeyboardButton(display_text, callback_data=f"pubch_{ch_id}")])
    
    if len(channels) > 1:
        buttons.append([InlineKeyboardButton("🌐 Post in ALL Channels", callback_data="pubch_ALL")])

    await update.message.reply_text("📢 **Select target channel to post this Question:**", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    return CHANNEL_SELECT

async def publish_quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ch_data = query.data.replace("pubch_", "")
    quiz = context.user_data['quiz']

    if ch_data == "ALL":
        quiz['target_channels'] = [ch[0] for ch in get_all_channels()]
    else:
        quiz['target_channels'] = [ch_data]

    return await publish_quiz(update, context)

def build_quiz_markup(quiz_id, q_type, options, selections=None):
    selections = selections or []
    buttons = []
    row = []

    if q_type == "MCQ":
        for k, v in options.items():
            row.append(InlineKeyboardButton(f"{k}: {v}", callback_data=f"mcq#{quiz_id}#{k}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
    elif q_type == "MCQ_MULTI":
        for k, v in options.items():
            icon = "☑️" if k in selections else "⬜"
            row.append(InlineKeyboardButton(f"{icon} {k}: {v}", callback_data=f"multi#{quiz_id}#{k}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("📥 Submit Answer", callback_data=f"multi#{quiz_id}#submit")])
    elif q_type == "NUMERIC":
        buttons = [
            [InlineKeyboardButton("1", callback_data=f"num#{quiz_id}#1"), InlineKeyboardButton("2", callback_data=f"num#{quiz_id}#2"), InlineKeyboardButton("3", callback_data=f"num#{quiz_id}#3")],
            [InlineKeyboardButton("4", callback_data=f"num#{quiz_id}#4"), InlineKeyboardButton("5", callback_data=f"num#{quiz_id}#5"), InlineKeyboardButton("6", callback_data=f"num#{quiz_id}#6")],
            [InlineKeyboardButton("7", callback_data=f"num#{quiz_id}#7"), InlineKeyboardButton("8", callback_data=f"num#{quiz_id}#8"), InlineKeyboardButton("9", callback_data=f"num#{quiz_id}#9")],
            [InlineKeyboardButton(".", callback_data=f"num#{quiz_id}#."), InlineKeyboardButton("0", callback_data=f"num#{quiz_id}#0"), InlineKeyboardButton("❌", callback_data=f"num#{quiz_id}#del")],
            [InlineKeyboardButton("📥 Submit", callback_data=f"num#{quiz_id}#sub")]
        ]
    return InlineKeyboardMarkup(buttons)

async def publish_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quiz = context.user_data['quiz']
    target_channels = quiz.get('target_channels', [update.effective_chat.id])
    seconds = quiz.get('seconds', 0)
    skip_lb = quiz.get('skip_lb', 0)
    user = update.effective_user

    for target_chat in target_channels:
        timestamp_id = int(datetime.datetime.now().timestamp() * 1000)
        quiz_id = f"q{timestamp_id}"
        markup = build_quiz_markup(quiz_id, quiz['type'], quiz.get('options'))

        text_to_send = quiz['text']
        try:
            if quiz['photo']:
                msg = await context.bot.send_photo(chat_id=target_chat, photo=quiz['photo'], caption=text_to_send, reply_markup=markup, parse_mode="Markdown")
            else:
                msg = await context.bot.send_message(chat_id=target_chat, text=text_to_send, reply_markup=markup, parse_mode="Markdown")
        except Exception as e:
            if update.message:
                await update.message.reply_text(f"❌ Failed to post in `{target_chat}`: {e}", parse_mode="Markdown")
            continue

        cursor.execute("""
            INSERT INTO quiz_history (quiz_id, admin_id, admin_name, chat_id, text_content, posted_at, status, skip_leaderboard)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (quiz_id, user.id, user.full_name, str(target_chat), text_to_send[:50], datetime.datetime.now(), 'pending', skip_lb))
        conn.commit()

        active_quizzes[quiz_id] = {
            'message_id': msg.message_id,
            'chat_id': target_chat,
            'type': quiz['type'],
            'options': quiz.get('options', {}),
            'correct_ans': quiz['correct_ans'],
            'responses': {},
            'temp_inputs': {},
            'temp_multi': {},
            'skip_lb': skip_lb
        }

        if not skip_lb:
            context.job_queue.run_once(
                declare_leaderboard,
                when=seconds,
                data={'quiz_id': quiz_id},
                job_kwargs={'misfire_grace_time': 3600}
            )

    confirm_msg = "🚀 Question posted successfully!"
    if update.callback_query:
        await update.callback_query.message.reply_text(confirm_msg)
    else:
        await update.message.reply_text(confirm_msg)

    context.user_data.clear()
    return ConversationHandler.END

async def handle_quiz_clicks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = query.data

    uname = user.username if user.username else "NoUsername"
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)",
        (user.id, uname, user.full_name)
    )
    cursor.execute("UPDATE users SET username = ?, full_name = ? WHERE user_id = ?", (uname, user.full_name, user.id))
    conn.commit()

    parts = data.split("#", 2)
    if len(parts) < 3:
        return
    prefix, quiz_id, action = parts[0], parts[1], parts[2]

    if quiz_id not in active_quizzes:
        await query.answer("⚠️ Quiz expired or declared.", show_alert=True)
        return

    q_data = active_quizzes[quiz_id]
    correct_ans = str(q_data['correct_ans'])

    if prefix == "mcq":
        if user.id in q_data['responses']:
            await query.answer("You have already answered this question!", show_alert=True)
            return

        is_correct = (action == correct_ans)
        pts = 10 if is_correct else -5
        corr = 1 if is_correct else 0
        incorr = 0 if is_correct else 1

        cursor.execute("""
            UPDATE users SET points = points + ?, attempts = attempts + 1, correct = correct + ?, incorrect = incorrect + ?
            WHERE user_id = ?
        """, (pts, corr, incorr, user.id))
        conn.commit()

        q_data['responses'][user.id] = {
            'ans': action,
            'timestamp': datetime.datetime.now(),
            'name': user.full_name
        }

        if is_correct:
            await query.answer("✅ Excellent! You are Right! (+10 Pts)", show_alert=True)
        else:
            await query.answer(f"❌ You are Wrong! (-5 Pts)\n\n🎯 Correct Answer was: {correct_ans}", show_alert=True)

    elif prefix == "multi":
        if user.id in q_data['responses']:
            await query.answer("You have already submitted your answer!", show_alert=True)
            return

        user_selections = q_data['temp_multi'].get(user.id, [])

        if action == "submit":
            if not user_selections:
                await query.answer("Please select at least one option before submitting!", show_alert=True)
                return

            final_user_ans = "".join(sorted(user_selections))
            is_correct = (final_user_ans == correct_ans)
            pts = 10 if is_correct else -5
            corr = 1 if is_correct else 0
            incorr = 0 if is_correct else 1

            cursor.execute("""
                UPDATE users SET points = points + ?, attempts = attempts + 1, correct = correct + ?, incorrect = incorrect + ?
                WHERE user_id = ?
            """, (pts, corr, incorr, user.id))
            conn.commit()

            q_data['responses'][user.id] = {
                'ans': final_user_ans,
                'timestamp': datetime.datetime.now(),
                'name': user.full_name
            }

            if is_correct:
                await query.answer("✅ Brilliant! All selected options are Correct! (+10 Pts)", show_alert=True)
            else:
                await query.answer(f"❌ You are Wrong! (-5 Pts)\n\n🎯 Correct Combination was: {correct_ans}", show_alert=True)
        else:
            if action in user_selections:
                user_selections.remove(action)
            else:
                user_selections.append(action)
            q_data['temp_multi'][user.id] = user_selections
            markup = build_quiz_markup(quiz_id, q_data['type'], q_data['options'], selections=user_selections)
            await query.edit_message_reply_markup(reply_markup=markup)
            await query.answer(f"Selected: {', '.join(user_selections) if user_selections else 'None'}")

    elif prefix == "num":
        if user.id in q_data['responses']:
            await query.answer("You have already submitted your answer!", show_alert=True)
            return

        current_val = q_data['temp_inputs'].get(user.id, "")

        if action in "0123456789.":
            if len(current_val) < 10:
                current_val += action
                q_data['temp_inputs'][user.id] = current_val
                await query.answer(f"Entered: {current_val}")
        elif action == "del":
            current_val = current_val[:-1]
            q_data['temp_inputs'][user.id] = current_val
            await query.answer("Deleted.")
        elif action == "sub":
            if not current_val:
                await query.answer("Enter answer first!", show_alert=True)
                return

            is_correct = (current_val == correct_ans)
            pts = 10 if is_correct else -5
            corr = 1 if is_correct else 0
            incorr = 0 if is_correct else 1

            cursor.execute("""
                UPDATE users SET points = points + ?, attempts = attempts + 1, correct = correct + ?, incorrect = incorrect + ?
                WHERE user_id = ?
            """, (pts, corr, incorr, user.id))
            conn.commit()

            q_data['responses'][user.id] = {
                'ans': current_val,
                'timestamp': datetime.datetime.now(),
                'name': user.full_name
            }

            if is_correct:
                await query.answer("✅ Correct Answer! (+10 Pts)", show_alert=True)
            else:
                await query.answer(f"❌ You are Wrong! (-5 Pts)\n\n🎯 Correct Answer was: {correct_ans}", show_alert=True)

async def declare_leaderboard(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    quiz_id = job_data['quiz_id']

    if quiz_id not in active_quizzes:
        return

    q_data = active_quizzes[quiz_id]

    cursor.execute("UPDATE quiz_history SET status='declared' WHERE quiz_id=?", (quiz_id,))
    conn.commit()

    if q_data.get('skip_lb'):
        del active_quizzes[quiz_id]
        return

    responses = q_data['responses']
    correct_ans = str(q_data['correct_ans'])

    correct_submissions = []
    total_correct = 0
    total_incorrect = 0

    for uid, resp in responses.items():
        is_correct = (str(resp['ans']) == correct_ans)
        if is_correct:
            total_correct += 1
            correct_submissions.append((resp['name'], resp['timestamp']))
        else:
            total_incorrect += 1

    correct_submissions.sort(key=lambda x: x[1])

    rank_text = "🏆 **Rankings for this question:**\n\n"
    top_15 = correct_submissions[:15]
    if top_15:
        for idx, (name, _) in enumerate(top_15, 1):
            rank_text += f"{idx}. {safe_name(name)} - 10 pts\n"
    else:
        rank_text += "No correct answers!\n"

    rank_text += f"\n📊 **Total Attempted Users:** {len(responses)}\n"
    rank_text += f"✅ **Total Correct Answers Submitted:** {total_correct}\n"
    rank_text += f"❌ **Total Incorrect Answers:** {total_incorrect}\n"
    rank_text += f"🎯 **Correct Answer:** {correct_ans}"

    await context.bot.send_message(
        chat_id=q_data['chat_id'],
        text=rank_text,
        reply_to_message_id=q_data['message_id'],
        parse_mode="Markdown"
    )

    del active_quizzes[quiz_id]

async def history_list(update: Update, context: ContextTypes.DEFAULT_TYPE, limit=None):
    if not is_owner(update.effective_user.id):
        return
    query = "SELECT quiz_id, admin_name, posted_at, status, skip_leaderboard FROM quiz_history ORDER BY posted_at DESC"
    if limit:
        query += f" LIMIT {limit}"

    cursor.execute(query)
    rows = cursor.fetchall()

    if not rows:
        await update.message.reply_text("📜 No quiz history found.")
        return

    msg = f"📜 **Quiz Audit History ({'Recent 10' if limit else 'All'}):**\n\n"
    buttons = []
    for qid, aname, pat, st, skip in rows:
        msg += f"• `{qid}` | By: **{safe_name(aname)}** | Status: `{st}`\n"
        if qid in active_quizzes:
            buttons.append([InlineKeyboardButton(f"⚡ Force Declare {qid}", callback_data=f"force_dec_{qid}")])

    markup = InlineKeyboardMarkup(buttons) if buttons else None
    await update.message.reply_text(msg, reply_markup=markup, parse_mode="Markdown")

async def recent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await history_list(update, context, limit=10)

async def allhistory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await history_list(update, context, limit=None)

async def force_declare_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_owner(query.from_user.id):
        return

    qid = query.data.replace("force_dec_", "")
    if qid in active_quizzes:
        mock_job = type('MockJob', (), {'data': {'quiz_id': qid}})()
        mock_context = type('MockContext', (), {'job': mock_job, 'bot': context.bot})()
        await declare_leaderboard(mock_context)
        await query.edit_message_text(f"✅ Quiz `{qid}` has been force-declared!")

async def silent_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    pass

def main():
    execute_custom_code()
    app = Application.builder().token(TOKEN).post_init(setup_bot_commands).read_timeout(60).write_timeout(60).connect_timeout(60).build()

    add_feature_conv = ConversationHandler(
        per_message=False,
        entry_points=[CommandHandler("addfeature", addfeature_start)],
        states={
            ADD_FEATURE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addfeature_save)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    edit_feature_conv = ConversationHandler(
        per_message=False,
        entry_points=[CommandHandler("editfeature", editfeature_start)],
        states={
            EDIT_FEATURE_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, editfeature_save_code)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    quiz_conv = ConversationHandler(
        per_message=False,
        entry_points=[CommandHandler("newquiz", new_quiz_start)],
        states={
            MEDIA_TEXT: [MessageHandler(filters.PHOTO | filters.TEXT & ~filters.COMMAND, get_media_text)],
            OPTION_COUNT: [
                CallbackQueryHandler(set_type_or_count, pattern="^type_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_option_count)
            ],
            OPTION_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_option_details)],
            CORRECT_ANS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_correct_ans)],
            TIMER_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_posting_channel)],
            CHANNEL_SELECT: [CallbackQueryHandler(publish_quiz_callback, pattern="^pubch_")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    mat_conv = ConversationHandler(
        per_message=False,
        entry_points=[CommandHandler("upmaterials", upmaterials_start)],
        states={
            MAT_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, upmaterials_title)],
            MAT_FILES: [
                CommandHandler("done", upmaterials_done),
                MessageHandler(filters.ALL & ~filters.COMMAND, upmaterials_files)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    req_conv = ConversationHandler(
        per_message=False,
        entry_points=[CommandHandler("reqmaterial", reqmaterials_start)],
        states={
            REQ_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reqmaterials_title)],
            REQ_FILES: [
                CommandHandler("done", reqmaterials_done),
                MessageHandler(filters.ALL & ~filters.COMMAND, reqmaterials_files)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    reset_conv = ConversationHandler(
        per_message=False,
        entry_points=[CommandHandler("resetall", resetall_start)],
        states={
            RESET_PASSWD: [MessageHandler(filters.TEXT & ~filters.COMMAND, resetall_confirm)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("recover", recover_user))
    app.add_handler(CommandHandler("giveowner", give_owner))
    app.add_handler(CommandHandler("add", add_user_cmd))
    app.add_handler(CommandHandler("givepts", give_points))
    app.add_handler(CommandHandler("removepts", remove_points))
    app.add_handler(CommandHandler("users", users_list))
    app.add_handler(CommandHandler("admins", admins_list))
    app.add_handler(CommandHandler("giveadmin", give_admin))
    app.add_handler(CommandHandler("removeadmin", remove_admin))
    app.add_handler(CommandHandler("postchannel", post_channel_manager))
    app.add_handler(CommandHandler("myprofile", my_profile))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("top", top_leaderboard))
    app.add_handler(CommandHandler("materials", materials_list))
    app.add_handler(CommandHandler("removematerial", remove_material_cmd))
    app.add_handler(CommandHandler("apvmaterial", apvmaterial_list))
    app.add_handler(CommandHandler("recent", recent_cmd))
    app.add_handler(CommandHandler("allhistory", allhistory_cmd))

    app.add_handler(add_feature_conv)
    app.add_handler(edit_feature_conv)
    app.add_handler(quiz_conv)
    app.add_handler(mat_conv)
    app.add_handler(req_conv)
    app.add_handler(reset_conv)

    app.add_handler(CallbackQueryHandler(start_buttons_callback, pattern="^btn_"))
    app.add_handler(CallbackQueryHandler(channel_callback_handler, pattern="^remch_"))
    app.add_handler(CallbackQueryHandler(materials_callback_handler, pattern="^(getmat|deltitle)_"))
    app.add_handler(CallbackQueryHandler(apvmaterial_callback_handler, pattern="^(viewreq|apvreq|rejreq)_"))
    app.add_handler(CallbackQueryHandler(force_declare_callback, pattern="^force_dec_"))
    app.add_handler(CallbackQueryHandler(handle_quiz_clicks, pattern="^(mcq|multi|num)#"))

    app.add_error_handler(silent_error_handler)
    print("Bot starting polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
