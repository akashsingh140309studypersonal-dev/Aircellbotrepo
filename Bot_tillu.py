import logging
import os
import sqlite3
import datetime
from datetime import timezone, timedelta
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

TOKEN = "8932328515:AAEk49CjT_P54lDAnlfjg1lmAS35CzYPkEk"
OWNERS = [7943423987, 8441919637]

# IST Timezone Config (+5:30)
IST = timezone(timedelta(hours=5, minutes=30))

def get_ist_now():
    return datetime.datetime.now(IST)

def format_ist_str(dt):
    if isinstance(dt, str):
        return dt[:19]
    return dt.strftime("%Y-%m-%d %H:%M:%S")

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
    declared_at TIMESTAMP,
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

def execute_custom_code(app: Application = None):
    if os.path.exists("custom_features.py"):
        try:
            with open("custom_features.py", "r", encoding="utf-8") as f:
                code = f.read()
            local_scope = {"app": app, "Application": Application}
            exec(code, globals(), local_scope)
        except Exception as e:
            print(f"Error loading custom features: {e}")

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
        BotCommand("users", "📊 Total registered members"),
        BotCommand("newquiz", "📝 Create Question"),
        BotCommand("track", "📍 Track Question Analytics (48h IST)"),
        BotCommand("upmaterials", "📤 Upload materials"),
        BotCommand("apvmaterial", "✅ Review requested materials"),
        BotCommand("removematerial", "🗑 Remove material"),
        BotCommand("cancel", "🚫 Cancel active process"),
    ]

    owner_commands = admin_commands + [
        BotCommand("addfeature", "⚡ Add custom feature code"),
        BotCommand("editfeature", "🛠 Edit custom code"),
        BotCommand("botchannels", "📢 Check bot channels & rights"),
        BotCommand("recover", "🔄 Smart recover users to DB"),
        BotCommand("giveowner", "👑 Grant Owner rights"),
        BotCommand("removeowner", "❌ Remove Owner rights"),
        BotCommand("listowner", "📜 View all Bot Owners"),
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

async def recover_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    
    reply = update.message.reply_to_message
    text = reply.text or reply.caption or "" if reply else update.message.text.replace("/recover", "")

    if not text.strip():
        await update.message.reply_text("⚠️ **Smart Recover Usage:** Reply to text/list or send text with `/recover`.", parse_mode="Markdown")
        return

    matches = re.findall(r'(\d{7,12})\s*[-:|~]*\s*@?([a-zA-Z0-9_]+)?', text)
    if not matches:
        await update.message.reply_text("❌ No valid User IDs found.")
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
    response = f"✅ **Smart Recovery Complete!**\nTotal `{added_count}` saved:\n\n" + "\n".join(added_list[:30])
    await update.message.reply_text(response, parse_mode="Markdown")

async def addfeature_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    await update.message.reply_text("📝 **Code Addition Mode**\nSend the raw Python code to add:\n(Type /cancel to abort)", parse_mode="Markdown")
    return ADD_FEATURE

async def addfeature_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text
    with open("custom_features.py", "a", encoding="utf-8") as f:
        f.write("\n" + code + "\n")
    execute_custom_code(context.application)
    await update.message.reply_text("✅ Dynamic feature code added successfully!", parse_mode="Markdown")
    return ConversationHandler.END

async def editfeature_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    if not os.path.exists("custom_features.py"):
        await update.message.reply_text("❌ No custom features found.", parse_mode="Markdown")
        return ConversationHandler.END

    with open("custom_features.py", "r", encoding="utf-8") as f:
        code_snippet = f.read()

    await update.message.reply_text(f"📜 **Current Code:**\n```python\n{code_snippet[:3500]}\n```\n📌 Send NEW code:", parse_mode="Markdown")
    return EDIT_FEATURE_CODE

async def editfeature_save_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text
    with open("custom_features.py", "w", encoding="utf-8") as f:
        f.write(code + "\n")
    execute_custom_code(context.application)
    await update.message.reply_text("✅ Code updated successfully!", parse_mode="Markdown")
    return ConversationHandler.END

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uname = user.username if user.username else "NoUsername"
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)", (user.id, uname, user.full_name))
    cursor.execute("UPDATE users SET username = ?, full_name = ? WHERE user_id = ?", (uname, user.full_name, user.id))
    conn.commit()

    welcome_text = (
        f"👋 **Welcome, {safe_name(user.first_name)}!**\n\n"
        f"🎯 Click **❓ Help Menu** below to view options."
    )

    buttons = [
        [InlineKeyboardButton("📚 Materials", callback_data="btn_materials"), InlineKeyboardButton("🏆 Leaderboard", callback_data="btn_top")],
        [InlineKeyboardButton("👤 Profile", callback_data="btn_profile"), InlineKeyboardButton("❓ Help Menu", callback_data="btn_help")]
    ]
    await update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

async def get_help_text(user_id, user_name):
    menu_text = f"👋 Welcome {safe_name(user_name)}!\n\n"
    if is_owner(user_id):
        menu_text += "👑 **OWNER:** /addfeature, /editfeature, /botchannels, /recover, /giveowner, /removeowner, /listowner, /givepts, /removepts, /admins, /giveadmin, /removeadmin, /postchannel, /recent, /allhistory, /resetall\n\n"
    if is_admin(user_id):
        menu_text += "🛠 **ADMIN:** /users, /newquiz, /track, /upmaterials, /apvmaterial, /removematerial, /cancel\n\n"
    menu_text += "👤 **MEMBER:** /myprofile, /profile, /top, /materials, /reqmaterial"
    return menu_text

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    menu_text = await get_help_text(user_id, user_name)
    if update.callback_query:
        await update.callback_query.edit_message_text(menu_text, parse_mode="Markdown")
    else:
        await update.message.reply_text(menu_text, parse_mode="Markdown")

async def give_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id) or not context.args:
        return
    try:
        new_owner = int(context.args[0])
        if new_owner not in OWNERS:
            OWNERS.append(new_owner)
            cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (new_owner,))
            conn.commit()
            await update.message.reply_text(f"👑 Added `{new_owner}` as Owner!", parse_mode="Markdown")
    except ValueError:
        pass

async def remove_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id) or not context.args:
        return
    try:
        target = int(context.args[0])
        if target in OWNERS and len(OWNERS) > 1:
            OWNERS.remove(target)
            await update.message.reply_text(f"❌ Revoked Owner status from `{target}`.", parse_mode="Markdown")
    except ValueError:
        pass

async def list_owners(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    msg = f"👑 **Owners List ({len(OWNERS)}):**\n" + "\n".join([f"• `{oid}`" for oid in OWNERS])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def check_bot_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    channels = get_all_channels()
    if not channels:
        await update.message.reply_text("📢 No channels registered.")
        return

    res_text = "📢 **Channels Status:**\n\n"
    for ch_id, ch_title in channels:
        try:
            chat = await context.bot.get_chat(ch_id)
            bot_member = await context.bot.get_chat_member(ch_id, context.bot.id)
            res_text += f"🔹 **{safe_name(chat.title)}** (`{ch_id}`) - `{bot_member.status.upper()}`\n"
        except Exception as e:
            res_text += f"❌ **{safe_name(ch_title)}** (`{ch_id}`) - Error: `{e}`\n"

    await update.message.reply_text(res_text, parse_mode="Markdown")

async def start_buttons_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data.split("_")[1] if "_" in query.data else query.data
    if action == "materials":
        await materials_list(update, context)
    elif action == "top":
        await top_leaderboard(update, context)
    elif action == "profile":
        await view_profile(update, context)
    elif action == "help":
        await help_command(update, context)

async def users_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    cursor.execute("SELECT user_id, full_name, username FROM users")
    all_users = cursor.fetchall()
    msg = f"📊 **Registered Members ({len(all_users)}):**\n\n" + "\n".join([f"• {safe_name(u[1])} ({format_username(u[2])}) - `{u[0]}`" for u in all_users])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def post_channel_manager(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if len(context.args) >= 1:
        ch_id = context.args[0]
        title = " ".join(context.args[1:]) if len(context.args) > 1 else ch_id
        cursor.execute("INSERT OR REPLACE INTO channels (channel_id, title) VALUES (?, ?)", (ch_id, title))
        conn.commit()
        await update.message.reply_text(f"✅ Added channel `{ch_id}`!", parse_mode="Markdown")
        return
    
    channels = get_all_channels()
    buttons = [[InlineKeyboardButton(f"🗑 Remove {title}", callback_data=f"remch_{ch_id}")] for ch_id, title in channels]
    await update.message.reply_text("📢 **Channels:**", reply_markup=InlineKeyboardMarkup(buttons) if buttons else None, parse_mode="Markdown")

async def channel_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if is_owner(query.from_user.id) and query.data.startswith("remch_"):
        ch_id = query.data.replace("remch_", "")
        cursor.execute("DELETE FROM channels WHERE channel_id = ?", (ch_id,))
        conn.commit()
        await query.edit_message_text(f"🗑 Channel `{ch_id}` removed.", parse_mode="Markdown")

async def give_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_owner(update.effective_user.id) and len(context.args) >= 2:
        uid, name = find_user(context.args[0])
        if uid:
            pts = int(context.args[1])
            cursor.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (pts, uid))
            conn.commit()
            await update.message.reply_text(f"✅ Added `{pts}` Pts to **{safe_name(name)}**.", parse_mode="Markdown")

async def remove_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_owner(update.effective_user.id) and len(context.args) >= 2:
        uid, name = find_user(context.args[0])
        if uid:
            pts = int(context.args[1])
            cursor.execute("UPDATE users SET points = MAX(0, points - ?) WHERE user_id = ?", (pts, uid))
            conn.commit()
            await update.message.reply_text(f"🗑 Deducted `{pts}` Pts from **{safe_name(name)}**.", parse_mode="Markdown")

async def admins_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    cursor.execute("SELECT user_id FROM admins")
    admins = cursor.fetchall()
    msg = "🛡️ **Admins:**\n" + "\n".join([f"• `{a[0]}`" for a in admins])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def give_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_owner(update.effective_user.id) and context.args:
        uid, name = find_user(context.args[0])
        if uid:
            cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (uid,))
            conn.commit()
            await update.message.reply_text(f"✅ Promoted **{safe_name(name)}** to Admin.", parse_mode="Markdown")

async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_owner(update.effective_user.id) and context.args:
        uid, name = find_user(context.args[0])
        if uid and uid not in OWNERS:
            cursor.execute("DELETE FROM admins WHERE user_id = ?", (uid,))
            conn.commit()
            await update.message.reply_text(f"🗑 Revoked Admin from **{safe_name(name)}**.", parse_mode="Markdown")

async def resetall_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    await update.message.reply_text("⚠️ Enter Admin Password to confirm reset:", parse_mode="Markdown")
    return RESET_PASSWD

async def resetall_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() == "2kAircell":
        cursor.execute("UPDATE users SET points = 0, diamonds = 0, attempts = 0, correct = 0, incorrect = 0")
        conn.commit()
        await update.message.reply_text("✅ **ALL STATS RESET TO ZERO!**", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Password incorrect.", parse_mode="Markdown")
    return ConversationHandler.END

async def view_profile(update: Update, context: ContextTypes.DEFAULT_TYPE, target_uid=None, target_uname=None):
    if target_uname:
        cursor.execute("SELECT * FROM users WHERE username = ?", (target_uname,))
    elif target_uid:
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (target_uid,))
    else:
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (update.effective_user.id,))

    user_data = cursor.fetchone()
    if not user_data:
        await update.message.reply_text("❌ Profile not found.")
        return

    uid, uname, fname, pts, dia, att, corr, incorr = user_data
    acc = (corr / att * 100) if att > 0 else 0.0

    profile_text = (
        f"👤 **PROFILE:** {safe_name(fname)}\n"
        f"🆔 ID: `{uid}` | Username: {format_username(uname)}\n\n"
        f"🏆 Points: `{pts}` Pts\n🎯 Accuracy: `{acc:.1f}%`\n"
        f"📝 Attempts: `{att}` | ✅ Correct: `{corr}` | ❌ Incorrect: `{incorr}`"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(profile_text, parse_mode="Markdown")
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
            await update.message.reply_text("❌ Invalid ID or Username.")

async def top_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cursor.execute("SELECT full_name, username, points FROM users ORDER BY points DESC LIMIT 30")
    top_users = cursor.fetchall()
    msg_text = "🏆 **Top 30 Leaderboard:**\n\n"
    for idx, (fname, uname, pts) in enumerate(top_users, 1):
        msg_text += f"{idx}. **{safe_name(fname)}** - `{pts}` pts\n"

    if update.callback_query:
        await update.callback_query.edit_message_text(msg_text, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg_text, parse_mode="Markdown")

async def reqmaterials_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['req'] = {'title': '', 'files': []}
    await update.message.reply_text("📝 Enter Request Title:", parse_mode="Markdown")
    return REQ_TITLE

async def reqmaterials_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['req']['title'] = update.message.text
    await update.message.reply_text("📥 Send files/photos. Write **/done** when finished.", parse_mode="Markdown")
    return REQ_FILES

async def reqmaterials_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    req = context.user_data['req']
    msg = update.message
    file_id = msg.document.file_id if msg.document else (msg.photo[-1].file_id if msg.photo else (msg.video.file_id if msg.video else None))
    file_type = "document" if msg.document else ("photo" if msg.photo else ("video" if msg.video else "text"))
    req['files'].append({'file_id': file_id, 'file_type': file_type, 'text_content': msg.text or msg.caption or ""})
    await update.message.reply_text("✅ File added. Send more or write **/done**.", parse_mode="Markdown")
    return REQ_FILES

async def reqmaterials_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    req = context.user_data.get('req', {})
    title, files = req.get('title'), req.get('files', [])
    for item in files:
        cursor.execute("INSERT INTO requested_materials (user_id, title, file_id, file_type, text_content) VALUES (?, ?, ?, ?, ?)",
                       (update.effective_user.id, title, item['file_id'], item['file_type'], item['text_content']))
    conn.commit()
    await update.message.reply_text(f"🎉 Request submitted!", parse_mode="Markdown")
    return ConversationHandler.END

async def apvmaterial_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    cursor.execute("SELECT DISTINCT title FROM requested_materials WHERE status = 'pending'")
    rows = cursor.fetchall()
    buttons = [[InlineKeyboardButton(f"📄 {t[0]}", callback_data=f"viewreq_{t[0][:20]}"),
                InlineKeyboardButton("✅", callback_data=f"apvreq_{t[0][:20]}"),
                InlineKeyboardButton("❌", callback_data=f"rejreq_{t[0][:20]}")] for t in rows]
    await update.message.reply_text("📝 Pending Requests:", reply_markup=InlineKeyboardMarkup(buttons) if buttons else None, parse_mode="Markdown")

async def apvmaterial_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    t_key = data.split("_", 1)[1]
    if data.startswith("apvreq_"):
        cursor.execute("SELECT title, file_id, file_type, text_content FROM requested_materials WHERE title LIKE ? AND status='pending'", (f"{t_key}%",))
        for t, fid, ft, txt in cursor.fetchall():
            cursor.execute("INSERT INTO materials (title, file_id, file_type, text_content) VALUES (?, ?, ?, ?)", (t, fid, ft, txt))
        cursor.execute("UPDATE requested_materials SET status='approved' WHERE title LIKE ?", (f"{t_key}%",))
        conn.commit()
        await query.edit_message_text("✅ Approved & Published!")
    elif data.startswith("rejreq_"):
        cursor.execute("UPDATE requested_materials SET status='rejected' WHERE title LIKE ?", (f"{t_key}%",))
        conn.commit()
        await query.edit_message_text("❌ Rejected.")

async def upmaterials_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    context.user_data['mat'] = {'title': '', 'files': []}
    await update.message.reply_text("📚 Enter Title:", parse_mode="Markdown")
    return MAT_TITLE

async def upmaterials_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['mat']['title'] = update.message.text
    await update.message.reply_text("📥 Send files. Write **/done** when finished.", parse_mode="Markdown")
    return MAT_FILES

async def upmaterials_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mat = context.user_data['mat']
    msg = update.message
    file_id = msg.document.file_id if msg.document else (msg.photo[-1].file_id if msg.photo else (msg.video.file_id if msg.video else None))
    file_type = "document" if msg.document else ("photo" if msg.photo else ("video" if msg.video else "text"))
    mat['files'].append({'file_id': file_id, 'file_type': file_type, 'text_content': msg.text or msg.caption or ""})
    await update.message.reply_text("✅ File received! Send more or write **/done**.", parse_mode="Markdown")
    return MAT_FILES

async def upmaterials_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mat = context.user_data.get('mat', {})
    for item in mat.get('files', []):
        cursor.execute("INSERT INTO materials (title, file_id, file_type, text_content) VALUES (?, ?, ?, ?)",
                       (mat.get('title'), item['file_id'], item['file_type'], item['text_content']))
    conn.commit()
    await update.message.reply_text("🎉 Material Saved!", parse_mode="Markdown")
    return ConversationHandler.END

async def remove_material_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_admin(update.effective_user.id) and context.args:
        target = " ".join(context.args)
        cursor.execute("DELETE FROM materials WHERE title LIKE ?", (f"%{target}%",))
        conn.commit()
        await update.message.reply_text("🗑️ Material removed.", parse_mode="Markdown")

async def materials_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cursor.execute("SELECT DISTINCT title FROM materials")
    rows = cursor.fetchall()
    buttons = [[InlineKeyboardButton(f"📄 {t[0]}", callback_data=f"getmat_{t[0][:30]}")] for t in rows]
    markup = InlineKeyboardMarkup(buttons) if buttons else None
    if update.callback_query:
        await update.callback_query.edit_message_text("📚 **Materials:**", reply_markup=markup, parse_mode="Markdown")
    else:
        await update.message.reply_text("📚 **Materials:**", reply_markup=markup, parse_mode="Markdown")

async def materials_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("getmat_"):
        t_key = query.data.replace("getmat_", "")
        cursor.execute("SELECT title, file_id, file_type, text_content FROM materials WHERE title LIKE ?", (f"{t_key}%",))
        for t, fid, ft, txt in cursor.fetchall():
            caption = f"📚 **{safe_name(t)}**\n\n{txt}"
            if ft == "document":
                await context.bot.send_document(query.message.chat_id, fid, caption=caption, parse_mode="Markdown")
            elif ft == "photo":
                await context.bot.send_photo(query.message.chat_id, fid, caption=caption, parse_mode="Markdown")
            else:
                await context.bot.send_message(query.message.chat_id, caption, parse_mode="Markdown")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("🚫 Cancelled.")
    return ConversationHandler.END

# Quiz Creation Flow
async def new_quiz_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    context.user_data['quiz'] = {}
    await update.message.reply_text("📸 Send Photo or Text for Question:")
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
        await query.message.reply_text("Enter correct answer:")
        return CORRECT_ANS
    elif query.data in ["type_mcq", "type_mcq_multi"]:
        quiz['type'] = "MCQ" if query.data == "type_mcq" else "MCQ_MULTI"
        await query.message.reply_text("Enter total options required (2 to 10):")
        return OPTION_COUNT

async def process_option_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quiz = context.user_data['quiz']
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
    quiz['options'][labels[idx]] = f"Option {labels[idx]}" if user_input.lower() == "null" else user_input
    quiz['current_opt'] += 1

    if quiz['current_opt'] < quiz['max_options']:
        await update.message.reply_text(f"Enter option **{labels[quiz['current_opt']]}** (or type 'null'):", parse_mode="Markdown")
        return OPTION_DETAILS
    else:
        msg = "Which options are correct? (e.g. `AB`):" if quiz['type'] == "MCQ_MULTI" else "Which option is correct? (e.g. A):"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return CORRECT_ANS

async def process_correct_ans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quiz = context.user_data['quiz']
    ans = update.message.text.strip().upper()
    quiz['correct_ans'] = "".join(sorted(list(set(re.findall(r'[A-J]', ans))))) if quiz['type'] in ["MCQ", "MCQ_MULTI"] else ans
    await update.message.reply_text("⏱ Enter ranking timer (e.g. `10s`, `2m`, `1h`) OR write **`skip`**:", parse_mode="Markdown")
    return TIMER_INPUT

async def select_posting_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quiz = context.user_data['quiz']
    time_res = parse_time_to_seconds(update.message.text)
    if time_res is None:
        await update.message.reply_text("❌ Invalid format! Example: `30s`, `5m` or `skip`")
        return TIMER_INPUT

    quiz['skip_lb'] = 1 if time_res == "skip" else 0
    quiz['seconds'] = time_res if not quiz['skip_lb'] else 0

    channels = get_all_channels()
    if not channels:
        quiz['target_channels'] = [update.effective_chat.id]
        return await publish_quiz(update, context)

    buttons = [[InlineKeyboardButton(f"📢 {t}", callback_data=f"pubch_{cid}")] for cid, t in channels]
    if len(channels) > 1:
        buttons.append([InlineKeyboardButton("🌐 Post in ALL Channels", callback_data="pubch_ALL")])

    await update.message.reply_text("📢 **Select target channel:**", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    return CHANNEL_SELECT

async def publish_quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ch_data = query.data.replace("pubch_", "")
    quiz = context.user_data['quiz']
    quiz['target_channels'] = [ch[0] for ch in get_all_channels()] if ch_data == "ALL" else [ch_data]
    return await publish_quiz(update, context)

def build_quiz_markup(quiz_id, q_type, options):
    buttons = []
    row = []
    if q_type in ["MCQ", "MCQ_MULTI"]:
        cb_prefix = "mcq" if q_type == "MCQ" else "multi"
        for k, v in options.items():
            row.append(InlineKeyboardButton(f"{k}: {v}", callback_data=f"{cb_prefix}#{quiz_id}#{k}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        if q_type == "MCQ_MULTI":
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
        except Exception:
            continue

        now_time_ist = get_ist_now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            INSERT INTO quiz_history (quiz_id, admin_id, admin_name, chat_id, text_content, posted_at, status, skip_leaderboard)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (quiz_id, user.id, user.full_name, str(target_chat), text_to_send[:50], now_time_ist, 'pending', skip_lb))
        conn.commit()

        active_quizzes[quiz_id] = {
            'message_id': msg.message_id,
            'chat_id': target_chat,
            'admin_id': user.id,
            'type': quiz['type'],
            'options': quiz.get('options', {}),
            'correct_ans': quiz['correct_ans'],
            'posted_at': now_time_ist,
            'responses': {},
            'temp_inputs': {},
            'temp_multi': {},
            'skip_lb': skip_lb
        }

        # FIXED TIMER EXECUTION
        if not skip_lb and seconds > 0:
            context.job_queue.run_once(
                declare_leaderboard,
                when=seconds,
                data={'quiz_id': quiz_id},
                job_kwargs={'misfire_grace_time': 3600}
            )

    await update.effective_message.reply_text("🚀 Question posted successfully!")
    context.user_data.clear()
    return ConversationHandler.END

async def handle_quiz_clicks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = query.data

    uname = user.username if user.username else "NoUsername"
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)", (user.id, uname, user.full_name))
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
        pts, corr, incorr = (10, 1, 0) if is_correct else (-5, 0, 1)

        cursor.execute("UPDATE users SET points = points + ?, attempts = attempts + 1, correct = correct + ?, incorrect = incorrect + ? WHERE user_id = ?", (pts, corr, incorr, user.id))
        conn.commit()

        q_data['responses'][user.id] = {'ans': action, 'timestamp': get_ist_now(), 'name': user.full_name}
        msg = "✅ Right! (+10 Pts)" if is_correct else f"❌ Wrong! (-5 Pts)\nCorrect: {correct_ans}"
        await query.answer(msg, show_alert=True)

    elif prefix == "multi":
        if user.id in q_data['responses']:
            await query.answer("You have already submitted your answer!", show_alert=True)
            return

        user_selections = q_data['temp_multi'].get(user.id, [])

        if action == "submit":
            if not user_selections:
                await query.answer("Select at least one option!", show_alert=True)
                return

            final_user_ans = "".join(sorted(user_selections))
            is_correct = (final_user_ans == correct_ans)
            pts, corr, incorr = (10, 1, 0) if is_correct else (-5, 0, 1)

            cursor.execute("UPDATE users SET points = points + ?, attempts = attempts + 1, correct = correct + ?, incorrect = incorrect + ? WHERE user_id = ?", (pts, corr, incorr, user.id))
            conn.commit()

            q_data['responses'][user.id] = {'ans': final_user_ans, 'timestamp': get_ist_now(), 'name': user.full_name}
            msg = "✅ Correct! (+10 Pts)" if is_correct else f"❌ Wrong! (-5 Pts)\nCorrect: {correct_ans}"
            await query.answer(msg, show_alert=True)
        else:
            if action in user_selections:
                user_selections.remove(action)
                await query.answer(f"Deselected {action}.")
            else:
                user_selections.append(action)
                await query.answer(f"Selected {action}.")
            q_data['temp_multi'][user.id] = user_selections

    elif prefix == "num":
        if user.id in q_data['responses']:
            await query.answer("You have already submitted!", show_alert=True)
            return

        current_val = q_data['temp_inputs'].get(user.id, "")
        if action in "0123456789.":
            current_val += action
            q_data['temp_inputs'][user.id] = current_val
            await query.answer(f"Entered: {current_val}")
        elif action == "del":
            current_val = current_val[:-1]
            q_data['temp_inputs'][user.id] = current_val
            await query.answer(f"Current: {current_val if current_val else 'Empty'}")
        elif action == "sub":
            if not current_val:
                await query.answer("Enter answer first!", show_alert=True)
                return

            is_correct = (current_val == correct_ans)
            pts, corr, incorr = (10, 1, 0) if is_correct else (-5, 0, 1)

            cursor.execute("UPDATE users SET points = points + ?, attempts = attempts + 1, correct = correct + ?, incorrect = incorrect + ? WHERE user_id = ?", (pts, corr, incorr, user.id))
            conn.commit()

            q_data['responses'][user.id] = {'ans': current_val, 'timestamp': get_ist_now(), 'name': user.full_name}
            msg = "✅ Correct! (+10 Pts)" if is_correct else f"❌ Wrong! (-5 Pts)\nCorrect: {correct_ans}"
            await query.answer(msg, show_alert=True)

# AUTO LEADERBOARD FUNCTION FIXED
async def declare_leaderboard(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    quiz_id = job_data['quiz_id']

    if quiz_id not in active_quizzes:
        return

    q_data = active_quizzes[quiz_id]
    dec_time_ist = get_ist_now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("UPDATE quiz_history SET status='declared', declared_at=? WHERE quiz_id=?", (dec_time_ist, quiz_id))
    conn.commit()

    if q_data.get('skip_lb'):
        q_data['status'] = 'declared'
        return

    responses = q_data['responses']
    correct_ans = str(q_data['correct_ans'])

    correct_submissions = []
    total_correct = 0
    total_incorrect = 0

    for uid, resp in responses.items():
        if str(resp['ans']) == correct_ans:
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
    rank_text += f"✅ **Total Correct Answers:** {total_correct}\n"
    rank_text += f"❌ **Total Incorrect Answers:** {total_incorrect}\n"
    rank_text += f"🎯 **Correct Answer:** {correct_ans}"

    await context.bot.send_message(
        chat_id=q_data['chat_id'],
        text=rank_text,
        reply_to_message_id=q_data['message_id'],
        parse_mode="Markdown"
    )

    q_data['status'] = 'declared'

# TRACK QUESTION ANALYTICS FIXED
async def track_questions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return

    cursor.execute("SELECT quiz_id, posted_at, status FROM quiz_history ORDER BY posted_at DESC LIMIT 20")
    rows = cursor.fetchall()
    if not rows:
        await update.message.reply_text("📌 No question posted yet.")
        return

    buttons = []
    for qid, posted_at, status in rows:
        status_tag = "🔴 [Live]" if status == 'pending' else "✅ [Declared]"
        buttons.append([InlineKeyboardButton(f"⏱ {format_ist_str(posted_at)} {status_tag}", callback_data=f"trdet_{qid}")])

    await update.message.reply_text("📊 **Select Posted Question to view Analytics (IST):**", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

async def track_details_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if not is_admin(user_id):
        return

    qid = query.data.replace("trdet_", "")
    cursor.execute("SELECT quiz_id, admin_id, admin_name, text_content, posted_at, declared_at, status FROM quiz_history WHERE quiz_id = ?", (qid,))
    row = cursor.fetchone()

    if not row:
        await query.edit_message_text("❌ Question tracking data not found.")
        return

    quiz_id, admin_id, admin_name, text_content, posted_at, declared_at, status = row
    correct_list, incorrect_list = [], []
    correct_ans = "N/A"

    if qid in active_quizzes:
        q_data = active_quizzes[qid]
        correct_ans = str(q_data['correct_ans'])
        for uid, resp in q_data['responses'].items():
            if str(resp['ans']) == correct_ans:
                correct_list.append(resp['name'])
            else:
                incorrect_list.append(resp['name'])

    report = (
        f"📊 **QUESTION TRACKING REPORT (IST)**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 **Posted By Admin:** {safe_name(admin_name)}\n"
        f"📝 **Content Preview:** {text_content}\n"
        f"⏱ **Posted Time:** `{format_ist_str(posted_at)} IST`\n"
        f"📌 **Status:** `{status.upper()}`\n"
        f"🎯 **Correct Answer:** `{correct_ans}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ **Correct Answer Users ({len(correct_list)}):**\n"
    )
    report += "\n".join([f"• {safe_name(n)}" for n in correct_list[:25]]) if correct_list else "• None"
    report += f"\n\n❌ **Incorrect Answer Users ({len(incorrect_list)}):**\n"
    report += "\n".join([f"• {safe_name(n)}" for n in incorrect_list[:25]]) if incorrect_list else "• None"

    await query.message.reply_text(report, parse_mode="Markdown")

# HISTORY AUDIT FIXED (REMOVED FORCE DECLARE OPTION)
async def history_list(update: Update, context: ContextTypes.DEFAULT_TYPE, limit=None):
    if not is_owner(update.effective_user.id):
        return
    query = "SELECT quiz_id, admin_name, posted_at, status FROM quiz_history ORDER BY posted_at DESC"
    if limit:
        query += f" LIMIT {limit}"

    cursor.execute(query)
    rows = cursor.fetchall()

    if not rows:
        await update.message.reply_text("📜 No quiz history found.")
        return

    msg = f"📜 **Quiz Audit History ({'Recent 10' if limit else 'All'}):**\n\n"
    for qid, aname, pat, st in rows:
        msg += f"• `{qid}` | Posted By: **{safe_name(aname)}** | Status: `{st.upper()}` | Time: `{format_ist_str(pat)}`\n"

    await update.message.reply_text(msg, parse_mode="Markdown")

async def recent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await history_list(update, context, limit=10)

async def allhistory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await history_list(update, context, limit=None)

async def silent_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    pass

def main():
    app = Application.builder().token(TOKEN).post_init(setup_bot_commands).read_timeout(60).write_timeout(60).connect_timeout(60).build()
    execute_custom_code(app)

    add_feature_conv = ConversationHandler(
        per_message=False,
        entry_points=[CommandHandler("addfeature", addfeature_start)],
        states={ADD_FEATURE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addfeature_save)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    edit_feature_conv = ConversationHandler(
        per_message=False,
        entry_points=[CommandHandler("editfeature", editfeature_start)],
        states={EDIT_FEATURE_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, editfeature_save_code)]},
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
            MAT_FILES: [CommandHandler("done", upmaterials_done), MessageHandler(filters.ALL & ~filters.COMMAND, upmaterials_files)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    req_conv = ConversationHandler(
        per_message=False,
        entry_points=[CommandHandler("reqmaterial", reqmaterials_start)],
        states={
            REQ_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reqmaterials_title)],
            REQ_FILES: [CommandHandler("done", reqmaterials_done), MessageHandler(filters.ALL & ~filters.COMMAND, reqmaterials_files)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    reset_conv = ConversationHandler(
        per_message=False,
        entry_points=[CommandHandler("resetall", resetall_start)],
        states={RESET_PASSWD: [MessageHandler(filters.TEXT & ~filters.COMMAND, resetall_confirm)]},
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("recover", recover_user))
    app.add_handler(CommandHandler("giveowner", give_owner))
    app.add_handler(CommandHandler("removeowner", remove_owner))
    app.add_handler(CommandHandler("listowner", list_owners))
    app.add_handler(CommandHandler("botchannels", check_bot_channels))
    app.add_handler(CommandHandler("givepts", give_points))
    app.add_handler(CommandHandler("removepts", remove_points))
    app.add_handler(CommandHandler("users", users_list))
    app.add_handler(CommandHandler("track", track_questions))
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
    app.add_handler(CallbackQueryHandler(track_details_callback, pattern="^trdet_"))  # REGISTERED CALLBACK PATTERN
    app.add_handler(CallbackQueryHandler(handle_quiz_clicks, pattern="^(mcq|multi|num)#"))

    app.add_error_handler(silent_error_handler)
    print("Bot starting polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
