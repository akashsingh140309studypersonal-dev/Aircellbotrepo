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
            with open("custom_features.py", "r") as f:
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
        BotCommand("users", "📊 Total registered members"),
        BotCommand("upmaterials", "📤 Upload materials"),
        BotCommand("apvmaterial", "✅ Review requested materials"),
        BotCommand("removematerial", "🗑 Remove material"),
        BotCommand("cancel", "🚫 Cancel active process"),
    ]

    owner_commands = admin_commands + [
        BotCommand("giveowner", "👑 Grant Owner rights"),
        BotCommand("addfeature", "⚡ Add custom feature code"),
        BotCommand("editfeature", "🛠 Edit existing command code"),
        BotCommand("recover", "🔄 Smart recover users to DB"),
        BotCommand("givepts", "➕ Give Points"),
        BotCommand("removepts", "➖ Remove Points"),
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

    # Extract ID and optional Username or Name from text automatically
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

# Edit Feature & Add Feature Dynamic System
async def addfeature_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    await update.message.reply_text("📝 **Code Addition Mode**\nSend the raw Python code to add to custom dynamic features:\n(Type /cancel to abort)", parse_mode="Markdown")
    return ADD_FEATURE

async def addfeature_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text
    with open("custom_features.py", "a") as f:
        f.write("\n" + code + "\n")
    execute_custom_code()
    await update.message.reply_text("✅ Dynamic feature code added and executed successfully!", parse_mode="Markdown")
    return ConversationHandler.END

COMMAND_MODULES = ["/start", "/newquiz", "/materials", "/myprofile", "/top", "/recover", "/postchannel", "/help"]

async def editfeature_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    
    buttons = []
    for cmd in COMMAND_MODULES:
        buttons.append([InlineKeyboardButton(f"⚙️ Edit {cmd}", callback_data=f"editcmd_{cmd.replace('/', '')}")])
    
    await update.message.reply_text("🛠 **Select Command Module to Edit:**\nChoose which command code logic you want to view and replace:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    return EDIT_FEATURE_SELECT

async def editfeature_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cmd = query.data.replace("editcmd_", "")
    context.user_data['editing_cmd'] = cmd

    code_snippet = f"# Code module for /{cmd}\n# Write python override function for /{cmd} below:\n"
    if os.path.exists("custom_features.py"):
        with open("custom_features.py", "r") as f:
            code_snippet = f.read()

    await query.message.reply_text(f"📜 **Current Code Logic for `/{cmd}`:**\n```python\n{code_snippet[:1500]}\n
