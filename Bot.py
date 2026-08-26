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

# Logging Setup
logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

TOKEN = "8932328515:AAEj0Mt9dtdZmOhel6dH9EHCNWxatQgdwNc"
OWNERS = [7943423987, 8441919637]

# Conversation States
MEDIA_TEXT, OPTION_COUNT, OPTION_DETAILS, CORRECT_ANS, TIMER_INPUT, CHANNEL_SELECT = range(6)
MAT_TITLE, MAT_FILES = range(6, 8)
REQ_TITLE, REQ_FILES = range(8, 10)
RESET_PASSWD = range(10, 11)
ADD_FEATURE, EDIT_FEATURE_SELECT, EDIT_FEATURE_CODE = range(11, 14)

# Database Initialization
conn = sqlite3.connect("quiz_bot.db", check_same_thread=False)
cursor = conn.cursor()

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

COMMAND_CODE_REGISTRY = {
    "start": "async def start(update, context):\n    user = update.effective_user\n    cursor.execute('INSERT OR IGNORE INTO users VALUES (?, ?, ?, 0, 0, 0, 0, 0)', (user.id, user.username or 'NoUsername', user.full_name))\n    conn.commit()\n    await update.message.reply_text(f'Welcome {user.first_name}!')",
    "help": "async def help_command(update, context):\n    await update.message.reply_text('Help menu content loaded dynamically!')",
    "newquiz": "async def new_quiz_start(update, context):\n    context.user_data['quiz'] = {}\n    await update.message.reply_text('Send photo or text for question:')\n    return MEDIA_TEXT",
}

async def setup_bot_commands(app: Application):
    member_commands = [
        BotCommand("start", "🌟 Start bot / Main Menu"),
        BotCommand("help", "❓ Help & Command List"),
        BotCommand("myprofile", "👤 View your profile stats"),
        BotCommand("top", "🏆 Leaderboard Top 30"),
    ]

    admin_commands = member_commands + [
        BotCommand("newquiz", "📝 Create Question"),
        BotCommand("cancel", "🚫 Cancel active process"),
    ]

    owner_commands = admin_commands + [
        BotCommand("addfeature", "⚡ Add custom feature code"),
        BotCommand("editfeature", "🛠 Edit command code"),
        BotCommand("recover", "🔄 Smart recover users to DB"),
        BotCommand("postchannel", "📢 Manage posting channels"),
    ]

    await app.bot.set_my_commands(member_commands, scope=BotCommandScopeDefault())
    for owner_id in OWNERS:
        try:
            await app.bot.set_my_commands(owner_commands, scope=BotCommandScopeChat(chat_id=owner_id))
        except Exception:
            pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uname = user.username if user.username else "NoUsername"
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)", (user.id, uname, user.full_name))
    cursor.execute("UPDATE users SET username = ?, full_name = ? WHERE user_id = ?", (uname, user.full_name, user.id))
    conn.commit()

    welcome_text = f"👋 **Welcome {safe_name(user.first_name)}!**\nReady to boost your learning?"
    buttons = [
        [InlineKeyboardButton("📚 Study Materials", callback_data="btn_materials"), InlineKeyboardButton("🏆 Leaderboard", callback_data="btn_top")],
        [InlineKeyboardButton("❓ Help", callback_data="btn_help")]
    ]
    await update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    menu_text = "📖 **Available Commands:**\n\n"
    if is_owner(user_id):
        menu_text += "👑 **Owner Controls:** /addfeature, /editfeature, /recover, /postchannel\n\n"
    if is_admin(user_id):
        menu_text += "🛠 **Admin Controls:** /newquiz, /cancel\n\n"
    menu_text += "👤 **Member Controls:** /start, /help, /top"
    
    if update.callback_query:
        await update.callback_query.message.reply_text(menu_text, parse_mode="Markdown")
    else:
        await update.message.reply_text(menu_text, parse_mode="Markdown")

# Main Menu Buttons Callback Handler
async def main_menu_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    if query.data == "btn_help":
        await help_command(update, context)
    elif query.data == "btn_top":
        cursor.execute("SELECT full_name, points FROM users ORDER BY points DESC LIMIT 30")
        top_users = cursor.fetchall()
        res = "🏆 **Leaderboard Top 30:**\n\n" + "\n".join([f"{i+1}. {safe_name(u[0])} - {u[1]} pts" for i, u in enumerate(top_users)])
        await query.message.reply_text(res, parse_mode="Markdown")

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
        await update.message.reply_text("⚠️ **Usage:** Reply to text/list or send text with `/recover`.", parse_mode="Markdown")
        return

    matches = re.findall(r'(\d{7,12})\s*[-:|~]*\s*@?([a-zA-Z0-9_]+)?', text)
    if not matches:
        await update.message.reply_text("❌ No valid User IDs found.")
        return

    added_count = 0
    for uid_str, uname in matches:
        uid = int(uid_str)
        clean_uname = uname if uname and not uname.isdigit() else "NoUsername"
        fname = f"Recovered User ({uid})"
        cursor.execute("INSERT OR REPLACE INTO users (user_id, username, full_name) VALUES (?, ?, ?)", (uid, clean_uname, fname))
        added_count += 1

    conn.commit()
    await update.message.reply_text(f"✅ **Recovery Complete!** Added `{added_count}` users.", parse_mode="Markdown")

async def post_channel_manager(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return
    if context.args:
        ch_id = context.args[0]
        title = " ".join(context.args[1:]) if len(context.args) > 1 else ch_id
        cursor.execute("INSERT OR REPLACE INTO channels (channel_id, title) VALUES (?, ?)", (ch_id, title))
        conn.commit()
        await update.message.reply_text(f"✅ Added channel `{title}` (`{ch_id}`)!", parse_mode="Markdown")
        return
    
    channels = get_all_channels()
    msg = "📢 **Registered Channels:**\n\n"
    buttons = []
    if not channels:
        msg += "No channels found.\nUsage: `/postchannel <channel_id> [Title]`"
    else:
        for ch_id, title in channels:
            msg += f"• **{title}** (`{ch_id}`)\n"
            buttons.append([InlineKeyboardButton(f"🗑 Remove {title}", callback_data=f"remch_{ch_id}")])
    
    markup = InlineKeyboardMarkup(buttons) if buttons else None
    await update.message.reply_text(msg, reply_markup=markup, parse_mode="Markdown")

async def channel_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
        
    if not is_owner(query.from_user.id):
        return
    if query.data.startswith("remch_"):
        ch_id = query.data.replace("remch_", "")
        cursor.execute("DELETE FROM channels WHERE channel_id = ?", (ch_id,))
        conn.commit()
        await query.edit_message_text(f"🗑 Channel `{ch_id}` removed.", parse_mode="Markdown")

# Quiz Creation Flow
async def new_quiz_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    context.user_data['quiz'] = {}
    await update.message.reply_text("📸 Send Photo or Text for Question:\n(Type /cancel to abort)")
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
        [InlineKeyboardButton("☑️ Multiple Choice MCQ", callback_data="type_mcq_multi")],
        [InlineKeyboardButton("🔢 Integer/Decimal Numpad", callback_data="type_num")]
    ]
    await update.message.reply_text("Select Question Type:", reply_markup=InlineKeyboardMarkup(buttons))
    return OPTION_COUNT

async def set_type_or_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    quiz = context.user_data['quiz']
    
    if query.data == "type_num":
        quiz['type'] = "NUMERIC"
        await query.message.reply_text("Enter correct answer (e.g. 100 or 1.15):")
        return CORRECT_ANS
    elif query.data in ["type_mcq", "type_mcq_multi"]:
        quiz['type'] = "MCQ" if query.data == "type_mcq" else "MCQ_MULTI"
        await query.message.reply_text("Enter total options count (2 to 10):")
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
        await update.message.reply_text("Enter a valid number between 2 and 10.")
        return OPTION_COUNT

async def process_option_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quiz = context.user_data['quiz']
    labels = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J']
    idx = quiz['current_opt']

    user_input = update.message.text.strip()
    quiz['options'][labels[idx]] = f"Option {labels[idx]}" if user_input.lower() == "null" else user_input
    quiz['current_opt'] += 1

    if quiz['current_opt'] < quiz['max_options']:
        next_label = labels[quiz['current_opt']]
        await update.message.reply_text(f"Enter option **{next_label}** (or type 'null'):", parse_mode="Markdown")
        return OPTION_DETAILS
    else:
        msg = "Which options are correct? (e.g., AB or A,C):" if quiz['type'] == "MCQ_MULTI" else "Which option is correct? (e.g. A or B):"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return CORRECT_ANS

async def process_correct_ans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quiz = context.user_data['quiz']
    ans = update.message.text.strip().upper()

    if quiz['type'] in ["MCQ", "MCQ_MULTI"]:
        clean_ans = "".join(sorted(list(set(re.findall(r'[A-J]', ans)))))
        quiz['correct_ans'] = clean_ans
    else:
        quiz['correct_ans'] = ans

    await update.message.reply_text("⏱ Enter timer (e.g. `10s`, `2m`, `1h`) or **`skip`**:", parse_mode="Markdown")
    return TIMER_INPUT

async def select_posting_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quiz = context.user_data['quiz']
    time_res = parse_time_to_seconds(update.message.text)

    if time_res is None:
        await update.message.reply_text("❌ Invalid format! Try `30s`, `5m`, or `skip`.")
        return TIMER_INPUT

    quiz['skip_lb'] = 1 if time_res == "skip" else 0
    quiz['seconds'] = time_res if not quiz['skip_lb'] else 0

    channels = get_all_channels()
    if not channels:
        quiz['target_channels'] = [update.effective_chat.id]
        return await publish_quiz(update, context)

    buttons = []
    for ch_id, title in channels:
        buttons.append([InlineKeyboardButton(f"📢 {title}", callback_data=f"pubch_{ch_id}")])
    
    if len(channels) > 1:
        buttons.append([InlineKeyboardButton("🌐 Post in ALL Channels", callback_data="pubch_ALL")])

    await update.message.reply_text("📢 **Select Target Channel:**", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    return CHANNEL_SELECT

async def publish_quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    ch_data = query.data.replace("pubch_", "")
    quiz = context.user_data['quiz']

    if ch_data == "ALL":
        quiz['target_channels'] = [ch[0] for ch in get_all_channels()]
    else:
        quiz['target_channels'] = [ch_data]

    await publish_quiz(update, context)

def build_quiz_markup(quiz_id, q_type, options, selections=None):
    selections = selections or []
    buttons = []
    row = []

    if q_type == "MCQ":
        for k, v in options.items():
            row.append(InlineKeyboardButton(f"{k}: {v}", callback_data=f"mcq_{quiz_id}_{k}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
    elif q_type == "MCQ_MULTI":
        for k, v in options.items():
            icon = "☑️" if k in selections else "⬜"
            row.append(InlineKeyboardButton(f"{icon} {k}: {v}", callback_data=f"multi_{quiz_id}_{k}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("📥 Submit Answer", callback_data=f"multi_{quiz_id}_sub")])
    elif q_type == "NUMERIC":
        buttons = [
            [InlineKeyboardButton("1", callback_data=f"num_{quiz_id}_1"), InlineKeyboardButton("2", callback_data=f"num_{quiz_id}_2"), InlineKeyboardButton("3", callback_data=f"num_{quiz_id}_3")],
            [InlineKeyboardButton("4", callback_data=f"num_{quiz_id}_4"), InlineKeyboardButton("5", callback_data=f"num_{quiz_id}_5"), InlineKeyboardButton("6", callback_data=f"num_{quiz_id}_6")],
            [InlineKeyboardButton("7", callback_data=f"num_{quiz_id}_7"), InlineKeyboardButton("8", callback_data=f"num_{quiz_id}_8"), InlineKeyboardButton("9", callback_data=f"num_{quiz_id}_9")],
            [InlineKeyboardButton(".", callback_data=f"num_{quiz_id}_."), InlineKeyboardButton("0", callback_data=f"num_{quiz_id}_0"), InlineKeyboardButton("❌", callback_data=f"num_{quiz_id}_del")],
            [InlineKeyboardButton("📥 Submit", callback_data=f"num_{quiz_id}_sub")]
        ]
    return InlineKeyboardMarkup(buttons)

async def publish_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quiz = context.user_data['quiz']
    target_channels = quiz.get('target_channels', [update.effective_chat.id])
    seconds = quiz.get('seconds', 0)
    skip_lb = quiz.get('skip_lb', 0)
    user = update.effective_user

    for target_chat in target_channels:
        ts_clean = str(datetime.datetime.now().timestamp()).replace('.', '')
        quiz_id = f"q{ts_clean}"
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
            'correct_ans': str(quiz['correct_ans']),
            'responses': {},
            'temp_inputs': {},
            'temp_multi': {},
            'skip_lb': skip_lb
        }

        if not skip_lb and seconds > 0:
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

# Fixed Inline Button Click Handler
async def handle_quiz_clicks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    # Fast Telegram acknowledgment
    try:
        await query.answer()
    except Exception:
        pass

    user = query.from_user
    data = query.data

    uname = user.username if user.username else "NoUsername"
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)", (user.id, uname, user.full_name))
    cursor.execute("UPDATE users SET username = ?, full_name = ? WHERE user_id = ?", (uname, user.full_name, user.id))
    conn.commit()

    parts = data.split("_", 2)
    if len(parts) < 3:
        await query.answer("⚠️ Invalid Action.", show_alert=True)
        return

    prefix, quiz_id, action = parts[0], parts[1], parts[2]

    if quiz_id not in active_quizzes:
        await query.answer("⚠️ Quiz expired or finished.", show_alert=True)
        return

    q_data = active_quizzes[quiz_id]
    correct_ans = str(q_data['correct_ans'])

    if prefix == "mcq":
        if user.id in q_data['responses']:
            await query.answer("You have already answered this question!", show_alert=True)
            return

        is_correct = (action == correct_ans)
        pts = 10 if is_correct else -5

        cursor.execute("""
            UPDATE users SET points = points + ?, attempts = attempts + 1, correct = correct + ?, incorrect = incorrect + ?
            WHERE user_id = ?
        """, (pts, 1 if is_correct else 0, 0 if is_correct else 1, user.id))
        conn.commit()

        q_data['responses'][user.id] = {'ans': action, 'timestamp': datetime.datetime.now(), 'name': user.full_name}

        if is_correct:
            await query.answer("✅ Excellent! Correct Answer! (+10 Pts)", show_alert=True)
        else:
            await query.answer(f"❌ Incorrect! (-5 Pts)\n\n🎯 Correct Answer was: {correct_ans}", show_alert=True)

    elif prefix == "multi":
        if user.id in q_data['responses']:
            await query.answer("You have already submitted your answer!", show_alert=True)
            return

        user_selections = q_data['temp_multi'].get(user.id, [])

        if action == "sub":
            if not user_selections:
                await query.answer("Select at least one option before submitting!", show_alert=True)
                return

            final_user_ans = "".join(sorted(user_selections))
            is_correct = (final_user_ans == correct_ans)
            pts = 10 if is_correct else -5

            cursor.execute("""
                UPDATE users SET points = points + ?, attempts = attempts + 1, correct = correct + ?, incorrect = incorrect + ?
                WHERE user_id = ?
            """, (pts, 1 if is_correct else 0, 0 if is_correct else 1, user.id))
            conn.commit()

            q_data['responses'][user.id] = {'ans': final_user_ans, 'timestamp': datetime.datetime.now(), 'name': user.full_name}

            if is_correct:
                await query.answer("✅ Brilliant! All selections are Correct! (+10 Pts)", show_alert=True)
            else:
                await query.answer(f"❌ Incorrect! (-5 Pts)\n\n🎯 Correct Option: {correct_ans}", show_alert=True)
        else:
            if action in user_selections:
                user_selections.remove(action)
            else:
                user_selections.append(action)
            q_data['temp_multi'][user.id] = user_selections
            markup = build_quiz_markup(quiz_id, q_data['type'], q_data['options'], selections=user_selections)
            await query.edit_message_reply_markup(reply_markup=markup)

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
            await query.answer("Deleted last input.")
        elif action == "sub":
            if not current_val:
                await query.answer("Enter answer first!", show_alert=True)
                return

            is_correct = (current_val == correct_ans)
            pts = 10 if is_correct else -5

            cursor.execute("""
                UPDATE users SET points = points + ?, attempts = attempts + 1, correct = correct + ?, incorrect = incorrect + ?
                WHERE user_id = ?
            """, (pts, 1 if is_correct else 0, 0 if is_correct else 1, user.id))
            conn.commit()

            q_data['responses'][user.id] = {'ans': current_val, 'timestamp': datetime.datetime.now(), 'name': user.full_name}

            if is_correct:
                await query.answer("✅ Correct Answer! (+10 Pts)", show_alert=True)
            else:
                await query.answer(f"❌ Incorrect! (-5 Pts)\n\n🎯 Correct Answer: {correct_ans}", show_alert=True)

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

    rank_text += f"\n📊 **Total Attempted:** {len(responses)}\n"
    rank_text += f"✅ **Correct Answers:** {total_correct}\n"
    rank_text += f"❌ **Incorrect Answers:** {total_incorrect}\n"
    rank_text += f"🎯 **Correct Answer:** {correct_ans}"

    await context.bot.send_message(
        chat_id=q_data['chat_id'],
        text=rank_text,
        reply_to_message_id=q_data['message_id'],
        parse_mode="Markdown"
    )

    del active_quizzes[quiz_id]

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("🚫 Process cancelled.")
    return ConversationHandler.END

def main():
    execute_custom_code()
    app = Application.builder().token(TOKEN).post_init(setup_bot_commands).read_timeout(60).write_timeout(60).connect_timeout(60).build()

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

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("recover", recover_user))
    app.add_handler(CommandHandler("postchannel", post_channel_manager))

    app.add_handler(quiz_conv)

    app.add_handler(CallbackQueryHandler(main_menu_callbacks, pattern="^btn_"))
    app.add_handler(CallbackQueryHandler(channel_callback_handler, pattern="^remch_"))
    app.add_handler(CallbackQueryHandler(handle_quiz_clicks, pattern="^(mcq|multi|num)_"))

    print("Bot is up and running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
