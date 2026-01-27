#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import shlex
import json
import time
import socket
import asyncio
import textwrap
import subprocess
import zipfile  # נשאר אם תרצה להשתמש בהמשך
import io
import traceback
import contextlib
import re
import hashlib
import secrets
import random
import inspect
import ast

from activity_reporter import create_reporter
from telegram import Update, InlineQueryResultArticle, InputTextMessageContent, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, InlineQueryHandler, CallbackQueryHandler, ChosenInlineResultHandler, MessageHandler, filters
from telegram.error import NetworkError, TimedOut, Conflict, BadRequest

# Import shared utilities
from shared_utils import (
    DEFAULT_OWNER_ID,
    parse_owner_ids,
    load_allowed_cmds,
    save_allowed_cmds,
    normalize_code,
    truncate as _truncate_base,
    is_safe_pip_name,
    exec_python_in_context,
    run_js_blocking,
    run_java_blocking,
    run_shell_blocking,
    resolve_path,
    handle_builtins,
)

# ==== תצורה ====
OWNER_IDS = parse_owner_ids(os.getenv("OWNER_ID", DEFAULT_OWNER_ID))
TIMEOUT = int(os.getenv("CMD_TIMEOUT", "60"))
PIP_TIMEOUT = int(os.getenv("PIP_TIMEOUT", "120"))
MAX_OUTPUT = int(os.getenv("MAX_OUTPUT", "10000"))
TG_MAX_MESSAGE = int(os.getenv("TG_MAX_MESSAGE", "4000"))
RESTART_NOTIFY_PATH = os.getenv("RESTART_NOTIFY_PATH", "/tmp/bot_restart_notify.json")

# In-memory allowlist - loaded from shared_utils
ALLOWED_CMDS = load_allowed_cmds()

ALLOW_ALL_COMMANDS = os.getenv("ALLOW_ALL_COMMANDS", "").lower() in ("1", "true", "yes", "on")
SHELL_EXECUTABLE = os.getenv("SHELL_EXECUTABLE") or ("/bin/bash" if os.path.exists("/bin/bash") else None)


def load_allowed_cmds_from_file() -> None:
    """Load allowed commands from file if it exists; otherwise keep current (env/default)."""
    global ALLOWED_CMDS
    ALLOWED_CMDS = load_allowed_cmds()


def save_allowed_cmds_to_file() -> None:
    """Save allowed commands to file."""
    save_allowed_cmds(ALLOWED_CMDS)

# ==== Reporter ====
reporter = create_reporter(
    mongodb_uri="mongodb+srv://mumin:M43M2TFgLfGvhBwY@muminai.tm6x81b.mongodb.net/?retryWrites=true&w=majority&appName=muminAI",
    service_id="srv-d3agim49c44c73dsq4j0",
    service_name="MyTerminalBot",
)

# ==== גלובלי לסשנים ====
sessions = {}

# ==== הקשר גלובלי לסשן פייתון מתמשך (לפי chat_id) ====
# מיפוי chat_id -> context dict לשמירת מצב פייתון לכל צ'אט בנפרד
PY_CONTEXT = {}

# ==== איסוף קוד רב-הודעות (/py_start … /py_run) ====
# מיפוי chat_id -> list[str] של הודעות שנאספו
PY_COLLECT: dict[int, list[str]] = {}

# ==== הרצה באינליין ====
INLINE_EXEC_STORE = {}
INLINE_SESSIONS = {}
INLINE_EXEC_TTL = int(os.getenv("INLINE_EXEC_TTL", "180"))
INLINE_EXEC_MAX = int(os.getenv("INLINE_EXEC_MAX", "5000"))
INLINE_EXEC_SWEEP_SEC = int(os.getenv("INLINE_EXEC_SWEEP_SEC", "300"))

# דגל דיבוג: ניתן להדליק/לכבות עם ENV או פקודות /debug_on /debug_off
INLINE_DEBUG_FLAG = os.getenv("INLINE_DEBUG", "").lower() in ("1", "true", "yes", "on")
INLINE_DEBUG_SENT = False
INLINE_PREVIEW_MAX = int(os.getenv("INLINE_PREVIEW_MAX", "800"))


def _shorten(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _get_inline_session(session_key: str):
    sess = INLINE_SESSIONS.get(session_key)
    if not sess:
        sess = {
            "cwd": os.getcwd(),
            "env": dict(os.environ),
        }
        INLINE_SESSIONS[session_key] = sess
    return sess


def exec_python_in_shared_context(src: str, context_key: int):
    """הרצת קוד פייתון בהקשר משותף לפי מזהה (למשל user_id).
    מחזיר (stdout, stderr, traceback_text | None)
    Uses shared_utils.exec_python_in_context internally.
    """
    global PY_CONTEXT
    ctx = PY_CONTEXT.get(context_key)
    if ctx is None:
        ctx = {"__builtins__": __builtins__, "__name__": "__main__"}
        PY_CONTEXT[context_key] = ctx
    return exec_python_in_context(src, ctx)


def _trim_for_message(text: str) -> str:
    text = truncate(text or "(no output)")
    if len(text) > TG_MAX_MESSAGE:
        return text[:TG_MAX_MESSAGE]
    return text


def _make_refresh_markup(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 רענון", callback_data=f"refresh:{token}")]
    ])


def _split_to_chunks_by_lines(text: str, max_len: int) -> list[str]:
    """מפצל טקסט לפי שורות כדי לא לחרוג מהמגבלה. דואג שלפחות חתיכה אחת תוחזר."""
    text = text or ""
    hard_cap = max(1, min(max_len, TG_MAX_MESSAGE - 100))
    if hard_cap <= 0:
        return [text]
    lines = text.splitlines()
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for ln in lines:
        add_len = len(ln) + (1 if current else 0)
        if current_len + add_len > hard_cap and current:
            chunks.append("\n".join(current))
            current = [ln]
            current_len = len(ln)
        else:
            current.append(ln)
            current_len += add_len
    if current or not chunks:
        chunks.append("\n".join(current))
    return chunks


def _make_before_run_markup(token: str, total_pages: int, page_idx: int) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    nav_row: list[InlineKeyboardButton] = []
    if page_idx > 0:
        nav_row.append(InlineKeyboardButton("⬅️ הקודם", callback_data=f"page:{token}:{page_idx-1}"))
    if page_idx + 1 < total_pages:
        nav_row.append(InlineKeyboardButton("הבא ➡️", callback_data=f"page:{token}:{page_idx+1}"))
    if nav_row:
        buttons.append(nav_row)
    buttons.append([
        InlineKeyboardButton("🔄 רענון", callback_data=f"refresh:{token}"),
        InlineKeyboardButton("📄 שלח קוד מלא", callback_data=f"sendfull:{token}"),
    ])
    return InlineKeyboardMarkup(buttons)


def prune_inline_exec_store(now_ts: float | None = None) -> tuple[int, int]:
    """מסיר רשומות פגות תוקף, ומגביל גודל מקסימלי ע"פ זמן ישן ביותר.
    מחזיר (expired_removed, trimmed_removed).
    """
    try:
        now = float(now_ts) if now_ts is not None else time.time()
        # הסרת פגי תוקף
        expired_keys = [k for k, v in INLINE_EXEC_STORE.items() if now - float(v.get("ts", 0)) > INLINE_EXEC_TTL]
        for k in expired_keys:
            INLINE_EXEC_STORE.pop(k, None)
        trimmed = 0
        # הגבלת גודל
        if len(INLINE_EXEC_STORE) > INLINE_EXEC_MAX:
            # מחיקה לפי הישן ביותר
            by_age = sorted(INLINE_EXEC_STORE.items(), key=lambda kv: float(kv[1].get("ts", 0)))
            overflow = len(INLINE_EXEC_STORE) - INLINE_EXEC_MAX
            for i in range(overflow):
                INLINE_EXEC_STORE.pop(by_age[i][0], None)
                trimmed += 1
        return (len(expired_keys), trimmed)
    except Exception:
        return (0, 0)


# ==== עזר ====
def allowed(u: Update) -> bool:
    return bool(u.effective_user and u.effective_user.id in OWNER_IDS)


def report_nowait(user_id: int) -> None:
    """מריץ דיווח פעילות ברקע בלי לעכב את הטיפול באירוע."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(asyncio.to_thread(reporter.report_activity, user_id))
    except RuntimeError:
        # אין לולאה פעילה – נריץ בת'רד בלי להמתין
        try:
            import threading
            threading.Thread(target=reporter.report_activity, args=(user_id,), daemon=True).start()
        except Exception:
            pass

def truncate(s: str) -> str:
    """Truncate output to MAX_OUTPUT characters. Uses shared_utils."""
    return _truncate_base(s, MAX_OUTPUT)


# normalize_code is imported from shared_utils

# is_safe_pip_name is imported from shared_utils


def install_package(package: str):
    subprocess.run([sys.executable, "-m", "pip", "install", package], check=True)


def _chat_id(update: Update) -> int:
    return update.effective_chat.id if update.effective_chat else 0


def get_session(update: Update):
    chat_id = _chat_id(update)
    sess = sessions.get(chat_id)
    if not sess:
        sess = {
            "cwd": os.getcwd(),
            "env": dict(os.environ),
        }
        sessions[chat_id] = sess
    return sess


# _resolve_path is imported from shared_utils as resolve_path


async def send_output(update: Update, text: str, filename: str = "output.txt"):
    """שולח פלט כטקסט קצר. אם ארוך מ-4000 תווים:
    - שולח תצוגה מקדימה של השורות הראשונות + "(output truncated)"
    - מצרף קובץ עם הפלט המלא
    """
    text = text or "(no output)"
    if len(text) <= TG_MAX_MESSAGE:
        await update.message.reply_text(text)
        return

    # שליחת תצוגה מקדימה
    try:
        lines = text.splitlines()
        preview_lines = []
        current_len = 0
        limit = max(0, TG_MAX_MESSAGE - len("(output truncated)\n"))
        for ln in lines:
            add_len = len(ln) + (1 if preview_lines else 0)
            if current_len + add_len > limit:
                break
            preview_lines.append(ln)
            current_len += add_len
        preview = ("\n".join(preview_lines) + "\n(output truncated)") if preview_lines else "(output truncated)"
        await update.message.reply_text(preview[:TG_MAX_MESSAGE])
    except Exception:
        # אם נכשל יצירת פריוויו, נמשיך עם קובץ בלבד
        pass

    # קובץ מלא
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=os.path.splitext(filename)[1] or ".txt", encoding="utf-8") as tf:
            tf.write(text)
            tmp_path = tf.name
        with open(tmp_path, "rb") as fh:
            await update.message.reply_document(document=fh, filename=filename, caption="(full output)")
    finally:
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


# Load allowlist from file at import time (fallback to ENV/default already set)
load_allowed_cmds_from_file()

# handle_builtins is imported from shared_utils


# ==== lifecycle ====
async def on_post_init(app: Application) -> None:
    """נשלחת פעם אחת כשהבוט עלה. אם יש בקשת ריסטרט ממתינה, נודיע שהאתחול הסתיים."""
    try:
        if os.path.exists(RESTART_NOTIFY_PATH):
            with open(RESTART_NOTIFY_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            chat_id = data.get("chat_id")
            if chat_id:
                try:
                    await app.bot.send_message(chat_id=chat_id, text="✅ האתחול הסתיים")
                finally:
                    try:
                        os.remove(RESTART_NOTIFY_PATH)
                    except Exception:
                        pass
        # הודעת בדיקה לבעלים על אתחול
        if INLINE_DEBUG_FLAG:
            try:
                for oid in (OWNER_IDS or set()):
                    await app.bot.send_message(chat_id=oid, text="🟢 הבוט עלה (polling)")
            except Exception:
                pass
    except Exception:
        # לא מפיל את הבוט אם יש בעיות הרשאות/קובץ
        pass


# ==== פקודות ====
ROCKET_FRAMES = [
    "   🚀",
    "   🚀\n   🔥",
    "   🚀\n  🔥🔥",
    "   🚀\n 🔥🔥🔥",
    "   🚀\n🔥🔥🔥🔥",
]

HEARTS = [
    "❤️",
    "🧡",
    "💛",
    "💚",
    "💙",
    "💜",
    "🤍",
    "🤎",
    "🖤",
    "💖",
    "💗",
    "💘",
    "💝",
]

def _build_hearts_grid(rows: int = 8, cols: int = 12) -> str:
    rows = max(1, min(rows, 15))
    cols = max(5, min(cols, 30))
    return "\n".join("".join(random.choice(HEARTS) for _ in range(cols)) for _ in range(rows))

async def rocket(update: Update, _: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id if update.effective_chat else None
    if not chat_id:
        return
    msg = await _.bot.send_message(chat_id, "🚀")
    for frame in ROCKET_FRAMES:
        try:
            await _.bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text=frame)
            await asyncio.sleep(0.5)
        except Exception:
            # ננסה להמשיך גם אם עריכה מסוימת נכשלת
            pass
    await _.bot.send_message(chat_id, "🚀💨 טס לחלל!")

async def hearts(update: Update, _: ContextTypes.DEFAULT_TYPE):
    # פרמטרים אופציונליים: /hearts <rows> <cols>
    try:
        parts = (update.message.text or "").strip().split()
        r = int(parts[1]) if len(parts) >= 2 else 8
        c = int(parts[2]) if len(parts) >= 3 else 12
    except Exception:
        r, c = 8, 12
    await update.message.reply_text(_build_hearts_grid(r, c))

async def tasks(update: Update, _: ContextTypes.DEFAULT_TYPE):
    """מציג את פלט TaskManager בצ'אט, אם המודול קיים.
    ניתן להגדיר את שם המודול דרך ENV בשם TASK_MANAGER_MODULE (ברירת מחדל: task_manager).
    """
    module_name = os.getenv("TASK_MANAGER_MODULE", "task_manager")
    try:
        mod = __import__(module_name, fromlist=["TaskManager"])  # type: ignore[import]
        TaskManager = getattr(mod, "TaskManager", None)
        if TaskManager is None:
            return await update.message.reply_text("❗ לא נמצאה המחלקה TaskManager במודול")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            TaskManager().run()
        text = buf.getvalue() or "(no output)"
        # שליחה בחלקים בטוחים לאורך
        for chunk in _split_to_chunks_by_lines(text, TG_MAX_MESSAGE):
            if not chunk:
                continue
            await update.message.reply_text(chunk[:TG_MAX_MESSAGE])
    except ModuleNotFoundError:
        await update.message.reply_text(
            "❗ מודול TaskManager לא נמצא. הוסף 'task_manager.py' עם המחלקה או קבע TASK_MANAGER_MODULE"
        )
    except Exception as e:
        await update.message.reply_text(f"ERR:\n{e}")

async def start(update: Update, _: ContextTypes.DEFAULT_TYPE):
    report_nowait(update.effective_user.id if update.effective_user else 0)
    if not allowed(update):
        return await update.message.reply_text(
            "/sh <פקודת shell>\n"
            "/py <קוד פייתון>\n"
            "/py_start → התחלת איסוף קוד רב-הודעות\n"
            "/py_run → הרצת כל ההודעות שנאספו\n"
            "/js <קוד JS>\n"
            "/java <קוד Java>\n"
            "/webapp → פתיחת ממשק Web App\n"
            "/health\n/restart\n/env\n/reset\n/clear\n/allow,/deny,/list,/update (מנהלי הרשאות לבעלים בלבד)\n"
            "(תמיכה ב-cd/export/unset, ושמירת cwd/env לסשן)"
        )
    
    # הצגת כפתורים למשתמש מורשה
    buttons = _get_welcome_buttons()
    await update.message.reply_text(
        _get_welcome_text(),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None
    )


async def webapp_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    """פותח את ממשק ה-Web App."""
    report_nowait(update.effective_user.id if update.effective_user else 0)
    if not allowed(update):
        return await update.message.reply_text("❌ אין הרשאה")
    
    webapp_url = os.getenv("WEBAPP_URL", "")
    if not webapp_url:
        return await update.message.reply_text(
            "❗ Web App לא מוגדר.\n\n"
            "כדי להפעיל:\n"
            "1. הרץ את webapp_server.py\n"
            "2. קבע WEBAPP_URL למשל: https://your-domain.com\n"
            "3. הגדר את ה-Web App ב-@BotFather"
        )
    
    from telegram import WebAppInfo
    await update.message.reply_text(
        "🖥️ לחץ על הכפתור לפתיחת Terminal Web App:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("פתח Web App 🚀", web_app=WebAppInfo(url=webapp_url))]
        ])
    )


def _get_welcome_text() -> str:
    """מחזיר את טקסט הפתיחה."""
    return (
        "🤖 <b>Terminal Bot</b>\n\n"
        "פקודות זמינות:\n"
        "• /sh <פקודה> - הרצת Shell\n"
        "• /py <קוד> - הרצת Python\n"
        "• /js <קוד> - הרצת JavaScript\n"
        "• /java <קוד> - הרצת Java\n"
        "• /webapp - פתיחת ממשק גרפי\n\n"
        "לעזרה נוספת: /help"
    )


def _get_welcome_buttons() -> list:
    """מחזיר את כפתורי הפתיחה."""
    from telegram import WebAppInfo
    webapp_url = os.getenv("WEBAPP_URL", "")
    buttons = []
    
    if webapp_url:
        buttons.append([InlineKeyboardButton("🖥️ פתח Web App", web_app=WebAppInfo(url=webapp_url))])
    
    buttons.append([InlineKeyboardButton("📋 רשימת פקודות", callback_data="show_commands")])
    return buttons


async def show_commands_callback(update: Update, _: ContextTypes.DEFAULT_TYPE):
    """מציג רשימת פקודות מפורטת."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📋 <b>רשימת פקודות מלאה:</b>\n\n"
        "<b>הרצת קוד:</b>\n"
        "• /sh <פקודה> - Shell/Bash\n"
        "• /py <קוד> - Python\n"
        "• /js <קוד> - JavaScript (Node.js)\n"
        "• /java <קוד> - Java\n"
        "• /call <פונקציה> - קריאה לפונקציה מוגדרת\n\n"
        "<b>קוד רב-שורות:</b>\n"
        "• /py_start - התחלת איסוף\n"
        "• /py_run - הרצת הקוד שנאסף\n\n"
        "<b>ניהול סשן:</b>\n"
        "• /env - הצגת משתני סביבה\n"
        "• /reset - איפוס cwd/env\n"
        "• /clear - ניקוי מלא של הסשן\n\n"
        "<b>ניהול הרשאות (בעלים בלבד):</b>\n"
        "• /list - רשימת פקודות מאושרות\n"
        "• /allow <cmd> - הוספת פקודה\n"
        "• /deny <cmd> - הסרת פקודה\n"
        "• /update <cmds> - עדכון הרשימה\n\n"
        "<b>אחר:</b>\n"
        "• /webapp - ממשק גרפי\n"
        "• /health - בדיקת תקינות\n"
        "• /whoami - הצגת ה-ID שלך\n"
        "• /restart - הפעלה מחדש",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("חזרה ◀️", callback_data="back_to_start")]
        ])
    )


async def back_to_start_callback(update: Update, _: ContextTypes.DEFAULT_TYPE):
    """חזרה למסך הפתיחה."""
    query = update.callback_query
    await query.answer()
    buttons = _get_welcome_buttons()
    await query.edit_message_text(
        _get_welcome_text(),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None
    )


async def inline_query(update: Update, _: ContextTypes.DEFAULT_TYPE):
    """תמיכה במצב אינליין: מציע תוצאות מסוג InlineQueryResultArticle.
    - מסנן מתוך ALLOWED_CMDS לפי הטקסט שהוקלד
    - מחזיר פאגינציה בעזרת next_offset
    - מוסיף קיצורי דרך להרצת /sh או /py עם הטקסט המלא
    """
    try:
        user_id = update.inline_query.from_user.id if update.inline_query and update.inline_query.from_user else 0
    except Exception:
        user_id = 0
    report_nowait(user_id)
    # הודעת דיבוג חד פעמית לבעלים כדי לוודא שאינליין מגיע
    if INLINE_DEBUG_FLAG:
        global INLINE_DEBUG_SENT
        if not INLINE_DEBUG_SENT and OWNER_IDS:
            INLINE_DEBUG_SENT = True
            try:
                for oid in OWNER_IDS:
                    await _.bot.send_message(chat_id=oid, text=f"ℹ️ התקבלה inline_query מ-{user_id} עם '{(update.inline_query.query or '').strip()}'")
            except Exception:
                pass

    q = (update.inline_query.query or "").strip() if update.inline_query else ""
    offset_text = update.inline_query.offset if update.inline_query else ""
    try:
        current_offset = int(offset_text) if offset_text else 0
    except ValueError:
        current_offset = 0

    PAGE_SIZE = 10
    # ניקוי קל לפני יצירת טוקנים חדשים
    prune_inline_exec_store()
    results = []
    is_owner = allowed(update)
    qhash = hashlib.sha1(q.encode("utf-8")).hexdigest()[:12] if q else "noq"

    # קיצורי דרך: חזרה למצב פשוט – כרטיסי הרצה עם כפתור רענון
    if q and current_offset == 0 and is_owner:
        token = secrets.token_urlsafe(8)
        INLINE_EXEC_STORE[token] = {"type": "sh", "q": q, "user_id": user_id, "ts": time.time()}
        results.append(
            InlineQueryResultArticle(
                id=f"run:{token}:sh:{current_offset}",
                title=_shorten(f"להריץ ב-/sh: {q}", 64),
                description=_shorten("יופיע 'מריץ…' ואז לחיצה על הכפתור תריץ", 120),
                input_message_content=InputTextMessageContent("⏳ מריץ…"),
                reply_markup=_make_refresh_markup(token),
            )
        )
        token_py = secrets.token_urlsafe(8)
        INLINE_EXEC_STORE[token_py] = {"type": "py", "q": q, "user_id": user_id, "ts": time.time()}
        results.append(
            InlineQueryResultArticle(
                id=f"run:{token_py}:py:{current_offset}",
                title=_shorten("להריץ ב-/py (בלוק קוד)", 64),
                description=_shorten("יופיע 'מריץ…' ואז לחיצה על הכפתור תריץ", 120),
                input_message_content=InputTextMessageContent("⏳ מריץ…"),
                reply_markup=_make_refresh_markup(token_py),
            )
        )
        token_js = secrets.token_urlsafe(8)
        INLINE_EXEC_STORE[token_js] = {"type": "js", "q": q, "user_id": user_id, "ts": time.time()}
        results.append(
            InlineQueryResultArticle(
                id=f"run:{token_js}:js:{current_offset}",
                title=_shorten("להריץ ב-/js (בלוק JS)", 64),
                description=_shorten("יופיע 'מריץ…' ואז לחיצה על הכפתור תריץ", 120),
                input_message_content=InputTextMessageContent("⏳ מריץ…"),
                reply_markup=_make_refresh_markup(token_js),
            )
        )
        token_java = secrets.token_urlsafe(8)
        INLINE_EXEC_STORE[token_java] = {"type": "java", "q": q, "user_id": user_id, "ts": time.time()}
        results.append(
            InlineQueryResultArticle(
                id=f"run:{token_java}:java:{current_offset}",
                title=_shorten("להריץ ב-/java (בלוק Java)", 64),
                description=_shorten("יופיע 'מריץ…' ואז לחיצה על הכפתור תריץ", 120),
                input_message_content=InputTextMessageContent("⏳ מריץ…"),
                reply_markup=_make_refresh_markup(token_java),
            )
        )

    # הצעות מתוך רשימת הפקודות המותרות, עם פאגינציה
    candidates = []
    if is_owner:
        candidates = sorted(ALLOWED_CMDS)
        if q:
            ql = q.lower()
            candidates = [c for c in candidates if ql in c.lower()]

    total = len(candidates)
    page_slice = candidates[current_offset: current_offset + PAGE_SIZE]
    for cmd in page_slice:
        results.append(
            InlineQueryResultArticle(
                id=f"cmd:{qhash}:{current_offset}:{cmd}",
                title=f"/sh {cmd}",
                description="לחיצה תכין הודעת /sh עם הפקודה",
                input_message_content=InputTextMessageContent(f"/sh {cmd}")
            )
        )

    next_offset = str(current_offset + PAGE_SIZE) if (current_offset + PAGE_SIZE) < total else ""

    if not results and current_offset == 0:
        results.append(
            InlineQueryResultArticle(
                id=f"help:{qhash}:{current_offset}",
                title="איך משתמשים באינליין?",
                description="כתבו @שם_הבוט ואז טקסט לחיפוש, למשל 'curl'",
                input_message_content=InputTextMessageContent("כדי להריץ פקודות: כתבו /sh <פקודה> או /py <קוד> או /js <קוד JS> או /java <קוד Java>")
            )
        )

    num_results = len(results) or 0
    # Fallback אם משום מה אין תוצאות (לא אמור לקרות)
    if num_results == 0:
        results.append(
            InlineQueryResultArticle(
                id=f"fallback:{qhash}:{current_offset}",
                title="אין תוצאות (לחץ ל-/help)",
                description="לחיצה תשלח עזרה",
                input_message_content=InputTextMessageContent("כדי להריץ פקודות: כתבו /sh <פקודה> או /py <קוד> או /js <קוד JS> או /java <קוד Java>")
            )
        )
        num_results = 1

    try:
        await update.inline_query.answer(results, cache_time=0, is_personal=True, next_offset=next_offset)
        # דיבוג: דו"ח כמה תוצאות נשלחו
        if INLINE_DEBUG_FLAG and OWNER_IDS:
            try:
                for oid in OWNER_IDS:
                    await _.bot.send_message(
                    chat_id=oid,
                    text=(
                        f"✅ inline: נשלחו {num_results} תוצאות "
                        f"(owner={'yes' if is_owner else 'no'}, q='{q}', total_candidates={total}, "
                        f"offset={current_offset}, next='{next_offset or '-'}')"
                    ),
                )
            except Exception:
                pass
    except BadRequest as e:
        # נסה ללא next_offset
        try:
            await update.inline_query.answer(results, cache_time=0, is_personal=True)
            if INLINE_DEBUG_FLAG and OWNER_IDS:
                try:
                    for oid in OWNER_IDS:
                        await _.bot.send_message(chat_id=oid, text=f"⚠️ inline עם שגיאה קלה (retry): {e}")
                except Exception:
                    pass
        except Exception as ex:
            if INLINE_DEBUG_FLAG and OWNER_IDS:
                try:
                    for oid in OWNER_IDS:
                        await _.bot.send_message(chat_id=oid, text=f"❌ inline נכשל: {ex}")
                except Exception:
                    pass


async def on_chosen_inline_result(update: Update, _: ContextTypes.DEFAULT_TYPE):
    """כאשר המשתמש בוחר תוצאת אינליין, נזהה אם זו תוצאת 'run:' שלנו ונריץ בפועל.
    נחזיר טקסט קצר כי לא ניתן לערוך את ההודעה שנשלחה כבר; במקום זה נשלח למשתמש הודעה אישית.
    """
    token = None
    try:
        chosen = update.chosen_inline_result
        if not chosen:
            return
        result_id = chosen.result_id or ""
        parts = result_id.split(":")
        if len(parts) < 4 or parts[0] != "run":
            return
        token = parts[1]
        run_type = parts[2]
        inline_msg_id = getattr(chosen, "inline_message_id", None)
        if INLINE_DEBUG_FLAG and OWNER_IDS:
            try:
                for oid in OWNER_IDS:
                    await _.bot.send_message(
                    chat_id=oid,
                    text=(
                        f"🔎 chosen_inline: result_id='{result_id}', type={run_type}, "
                        f"has_inline_message_id={'yes' if bool(inline_msg_id) else 'no'}"
                    ),
                )
            except Exception:
                pass
        data = INLINE_EXEC_STORE.get(token)
        if not data:
            return
        # ניקוי רשומה אם ישנה זמן רב
        if time.time() - float(data.get("ts", 0)) > INLINE_EXEC_TTL:
            INLINE_EXEC_STORE.pop(token, None)
            prune_inline_exec_store()
            return

        user_id = chosen.from_user.id if chosen.from_user else 0
        report_nowait(user_id)
        if user_id not in OWNER_IDS:
            # אם מי שבחר אינו הבעלים – נשלח לו הודעה פרטית עם הנחיות וה-ID שלו
            try:
                await _.bot.send_message(
                    chat_id=user_id,
                    text=(
                        "⛔ אין לך הרשאה להריץ מהאינליין.\n"
                        f"ה־ID שלך: {user_id}\n"
                        "אם זה הבוט שלך, קבע OWNER_ID לערך הזה (או הוסף לרשימה) והפעל מחדש."
                    ),
                )
            except Exception:
                pass
            return

        q = normalize_code(str(data.get("q", ""))).strip()
        if not q:
            return

        # נערוך הרצה בהתאם לסוג
        text_out = ""
        if run_type == "sh":
            # אימות פקודה ראשונה אם צריך
            allow = True
            if not ALLOW_ALL_COMMANDS:
                try:
                    parts = shlex.split(q, posix=True)
                except ValueError:
                    parts = []
                if not parts:
                    allow = False
                else:
                    first_tok = parts[0].strip()
                    allow = first_tok in ALLOWED_CMDS if first_tok else False
            if not allow:
                text_out = f"❗ פקודה לא מאושרת"
            else:
                sess = _get_inline_session(str(user_id))
                try:
                    shell_exec = SHELL_EXECUTABLE or "/bin/bash"
                    p = subprocess.run(
                        [shell_exec, "-c", q],
                        capture_output=True,
                        text=True,
                        timeout=TIMEOUT,
                        cwd=sess["cwd"],
                        env=sess["env"],
                    )
                    out = p.stdout or ""
                    err = p.stderr or ""
                    resp = f"$ {q}\n\n{out}"
                    if err:
                        resp += "\nERR:\n" + err
                    text_out = resp
                except subprocess.TimeoutExpired:
                    text_out = f"$ {q}\n\n⏱️ Timeout"
                except Exception as e:
                    text_out = f"$ {q}\n\nERR:\n{e}"

        elif run_type == "py":
            cleaned = textwrap.dedent(q)
            cleaned = normalize_code(cleaned).strip("\n") + "\n"
            try:
                out, err, tb_text = await asyncio.wait_for(asyncio.to_thread(exec_python_in_shared_context, cleaned, int(user_id)), timeout=TIMEOUT)
                parts_out = [cleaned.rstrip() + "\n\n"]
                if out.strip():
                    parts_out.append(out.rstrip())
                if err.strip():
                    parts_out.append("STDERR:\n" + err.rstrip())
                if tb_text and tb_text.strip():
                    parts_out.append(tb_text.rstrip())
                text_out = "\n".join(parts_out).strip() or "(no output)"
            except asyncio.TimeoutError:
                text_out = cleaned.rstrip() + "\n\n⏱️ Timeout"
            except Exception as e:
                text_out = cleaned.rstrip() + f"\n\nERR:\n{e}"
        elif run_type == "js":
            cleaned = textwrap.dedent(q)
            cleaned = normalize_code(cleaned).strip("\n") + "\n"
            try:
                sess = _get_inline_session(str(user_id))
                p = await asyncio.to_thread(run_js_blocking, cleaned, sess["cwd"], sess["env"], TIMEOUT)
                out = (p.stdout or "").rstrip()
                err = (p.stderr or "").rstrip()
                parts_out = [cleaned.rstrip() + "\n\n"]
                if out:
                    parts_out.append(out)
                if err:
                    parts_out.append("STDERR:\n" + err)
                text_out = "\n".join(parts_out).strip() or "(no output)"
            except subprocess.TimeoutExpired:
                text_out = cleaned.rstrip() + "\n\n⏱️ Timeout"
            except FileNotFoundError:
                text_out = cleaned.rstrip() + "\n\n❌ node לא נמצא במערכת"
            except Exception as e:
                text_out = cleaned.rstrip() + f"\n\nERR:\n{e}"
        elif run_type == "java":
            cleaned = textwrap.dedent(q)
            cleaned = normalize_code(cleaned).strip("\n") + "\n"
            try:
                sess = _get_inline_session(str(user_id))
                p = await asyncio.to_thread(run_java_blocking, cleaned, sess["cwd"], sess["env"], TIMEOUT)
                out = (p.stdout or "").rstrip()
                err = (p.stderr or "").rstrip()
                parts_out = [cleaned.rstrip() + "\n\n"]
                if out:
                    parts_out.append(out)
                if err:
                    parts_out.append("STDERR:\n" + err)
                text_out = "\n".join(parts_out).strip() or "(no output)"
            except subprocess.TimeoutExpired:
                text_out = cleaned.rstrip() + "\n\n⏱️ Timeout"
            except FileNotFoundError:
                text_out = cleaned.rstrip() + "\n\n❌ javac/java לא נמצאו במערכת"
            except Exception as e:
                text_out = cleaned.rstrip() + f"\n\nERR:\n{e}"
        else:
            return

        text_out = _trim_for_message(text_out)

        # אם יש inline_message_id – נערוך את הודעת האינליין בצ'אט היעד
        if inline_msg_id:
            try:
                full_text = text_out
                display_text = full_text if len(full_text) <= INLINE_PREVIEW_MAX else (full_text[:INLINE_PREVIEW_MAX] + "\n\n…(נשלח קובץ מלא בפרטי)")
                # סיבוב טוקן חדש כדי לאפשר רענון נוסף
                new_token = secrets.token_urlsafe(8)
                INLINE_EXEC_STORE[new_token] = {"type": run_type, "q": q, "user_id": user_id, "ts": time.time()}
                prune_inline_exec_store()
                await _.bot.edit_message_text(inline_message_id=inline_msg_id, text=display_text, reply_markup=_make_refresh_markup(new_token))
                if INLINE_DEBUG_FLAG and OWNER_IDS:
                    try:
                        for oid in OWNER_IDS:
                            await _.bot.send_message(chat_id=oid, text="✏️ inline_message נערך בהצלחה")
                    except Exception:
                        pass
            except Exception:
                # נפילה חכמה: שליחת הודעה פרטית לבעלים
                try:
                    new_token = secrets.token_urlsafe(8)
                    INLINE_EXEC_STORE[new_token] = {"type": run_type, "q": q, "user_id": user_id, "ts": time.time()}
                    prune_inline_exec_store()
                    await _.bot.send_message(chat_id=user_id, text=display_text, reply_markup=_make_refresh_markup(new_token))
                    if INLINE_DEBUG_FLAG and OWNER_IDS:
                        try:
                            for oid in OWNER_IDS:
                                await _.bot.send_message(chat_id=oid, text="⚠️ עריכה נכשלה – נשלחה הודעה פרטית")
                        except Exception:
                            pass
                except Exception:
                    pass
        else:
            # אין מזהה הודעת אינליין – שליחה פרטית לבעלים
            try:
                full_text = text_out
                display_text = full_text if len(full_text) <= INLINE_PREVIEW_MAX else (full_text[:INLINE_PREVIEW_MAX] + "\n\n…(נשלח קובץ מלא בפרטי)")
                new_token = secrets.token_urlsafe(8)
                INLINE_EXEC_STORE[new_token] = {"type": run_type, "q": q, "user_id": user_id, "ts": time.time()}
                prune_inline_exec_store()
                await _.bot.send_message(chat_id=user_id, text=display_text, reply_markup=_make_refresh_markup(new_token))
                if INLINE_DEBUG_FLAG and OWNER_IDS:
                    try:
                        for oid in OWNER_IDS:
                            await _.bot.send_message(chat_id=oid, text="ℹ️ אין inline_message_id – נשלחה הודעה פרטית")
                    except Exception:
                        pass
            except Exception:
                pass
    finally:
        # מחיקה רכה של הטוקן
        try:
            if token:
                INLINE_EXEC_STORE.pop(token, None)
            prune_inline_exec_store()
        except Exception:
            pass

async def handle_refresh_callback(update: Update, _: ContextTypes.DEFAULT_TYPE):
    """כפתור רענון: מריץ שוב לפי הטוקן ושומר טוקן חדש, מעדכן את ההודעה במקום."""
    query = update.callback_query
    if not query:
        return
    data = query.data or ""
    if not (data.startswith("refresh:") or data.startswith("page:") or data.startswith("sendfull:")):
        return
    # שליחת קוד מלא בפרטי
    if data.startswith("sendfull:"):
        token = data.split(":", 1)[1]
        rec = INLINE_EXEC_STORE.get(token)
        if not rec:
            return
        if query.from_user and rec.get("user_id") != query.from_user.id:
            return
        q = str(rec.get("q", ""))
        try:
            bio = io.BytesIO(q.encode("utf-8"))
            rtype = rec.get("type")
            if rtype == "py":
                bio.name = "inline-code.py"
            elif rtype == "js":
                bio.name = "inline-code.js"
            elif rtype == "java":
                bio.name = "inline-code.java"
            elif rtype == "sh":
                bio.name = "inline-code.sh"
            else:
                bio.name = "inline-code.txt"
            # ננסה לשלוח בפרטי. אם אין צ'אט פרטי פתוח, נבקש מהמשתמש להתחיל שיחה עם הבוט
            try:
                await _.bot.send_document(chat_id=query.from_user.id, document=bio, caption="(full code)")
                await query.answer("נשלח אליך בפרטי", show_alert=False)
            except Exception:
                await query.answer("פתח שיחה פרטית עם הבוט ואז נסה שוב", show_alert=True)
        except Exception:
            pass
        return

    # דפדוף עמודים לפני הרצה
    if data.startswith("page:"):
        try:
            _, token, page_str = data.split(":", 2)
            page_idx = int(page_str)
        except Exception:
            return
        rec = INLINE_EXEC_STORE.get(token)
        if not rec:
            return
        if query.from_user and rec.get("user_id") != query.from_user.id:
            return
        q = str(rec.get("q", ""))
        run_type = rec.get("type")
        pages = _split_to_chunks_by_lines((f"$ {q}" if run_type == "sh" else q), INLINE_PREVIEW_MAX)
        page_idx = max(0, min(page_idx, max(0, len(pages) - 1)))
        rec["page"] = page_idx
        try:
            await query.edit_message_text(text=f"⏳ מריץ…\n\n{pages[page_idx]}", reply_markup=_make_before_run_markup(token, len(pages), page_idx))
            await query.answer()
        except Exception:
            pass
        return

    token = data.split(":", 1)[1]
    rec = INLINE_EXEC_STORE.get(token)
    if not rec:
        try:
            await query.answer(text="⛔ אין נתוני רענון (פג תוקף)", show_alert=False)
        except Exception:
            pass
        return

    user_id = query.from_user.id if query.from_user else 0
    if user_id not in OWNER_IDS:
        try:
            await query.answer(text="⛔ אין הרשאה", show_alert=False)
        except Exception:
            pass
        return

    if time.time() - float(rec.get("ts", 0)) > INLINE_EXEC_TTL:
        INLINE_EXEC_STORE.pop(token, None)
        prune_inline_exec_store()
        try:
            await query.answer(text="⛔ פג תוקף, נסה שוב מהאינליין", show_alert=False)
        except Exception:
            pass
        return

    run_type = rec.get("type")
    q = normalize_code(str(rec.get("q", ""))).strip()
    text_out = ""
    if run_type == "sh":
        allow = True
        if not ALLOW_ALL_COMMANDS:
            try:
                parts = shlex.split(q, posix=True)
            except ValueError:
                parts = []
            if not parts:
                allow = False
            else:
                first_tok = parts[0].strip()
                allow = first_tok in ALLOWED_CMDS if first_tok else False
        if not allow:
            text_out = "❗ פקודה לא מאושרת"
        else:
            sess = _get_inline_session(str(user_id))
            try:
                shell_exec = SHELL_EXECUTABLE or "/bin/bash"
                p = await asyncio.to_thread(run_shell_blocking, shell_exec, q, sess["cwd"], sess["env"], TIMEOUT)
                out = p.stdout or ""
                err = p.stderr or ""
                header = f"$ {q}\n\n"
                resp = header + out
                if err:
                    resp += "\nERR:\n" + err
                text_out = resp
            except subprocess.TimeoutExpired:
                text_out = f"$ {q}\n\n⏱️ Timeout"
            except Exception as e:
                text_out = f"$ {q}\n\nERR:\n{e}"
    elif run_type == "py":
        cleaned = textwrap.dedent(q)
        cleaned = normalize_code(cleaned).strip("\n") + "\n"
        try:
            out, err, tb_text = await asyncio.wait_for(asyncio.to_thread(exec_python_in_shared_context, cleaned, int(user_id)), timeout=TIMEOUT)
            parts_out = [cleaned.rstrip() + "\n\n"]
            if out.strip():
                parts_out.append(out.rstrip())
            if err.strip():
                parts_out.append("STDERR:\n" + err.rstrip())
            if tb_text and tb_text.strip():
                parts_out.append(tb_text.rstrip())
            text_out = "\n".join(parts_out).strip() or "(no output)"
        except asyncio.TimeoutError:
            text_out = cleaned.rstrip() + "\n\n⏱️ Timeout"
        except Exception as e:
            text_out = cleaned.rstrip() + f"\n\nERR:\n{e}"
    elif run_type == "js":
        cleaned = textwrap.dedent(q)
        cleaned = normalize_code(cleaned).strip("\n") + "\n"
        try:
            sess = _get_inline_session(str(user_id))
            p = await asyncio.to_thread(run_js_blocking, cleaned, sess["cwd"], sess["env"], TIMEOUT)
            out = (p.stdout or "").rstrip()
            err = (p.stderr or "").rstrip()
            parts_out = [cleaned.rstrip() + "\n\n"]
            if out:
                parts_out.append(out)
            if err:
                parts_out.append("STDERR:\n" + err)
            text_out = "\n".join(parts_out).strip() or "(no output)"
        except subprocess.TimeoutExpired:
            text_out = cleaned.rstrip() + "\n\n⏱️ Timeout"
        except FileNotFoundError:
            text_out = cleaned.rstrip() + "\n\n❌ node לא נמצא במערכת"
        except Exception as e:
            text_out = cleaned.rstrip() + f"\n\nERR:\n{e}"
    elif run_type == "java":
        cleaned = textwrap.dedent(q)
        cleaned = normalize_code(cleaned).strip("\n") + "\n"
        try:
            sess = _get_inline_session(str(user_id))
            p = await asyncio.to_thread(run_java_blocking, cleaned, sess["cwd"], sess["env"], TIMEOUT)
            out = (p.stdout or "").rstrip()
            err = (p.stderr or "").rstrip()
            parts_out = [cleaned.rstrip() + "\n\n"]
            if out:
                parts_out.append(out)
            if err:
                parts_out.append("STDERR:\n" + err)
            text_out = "\n".join(parts_out).strip() or "(no output)"
        except subprocess.TimeoutExpired:
            text_out = cleaned.rstrip() + "\n\n⏱️ Timeout"
        except FileNotFoundError:
            text_out = cleaned.rstrip() + "\n\n❌ javac/java לא נמצאו במערכת"
        except Exception as e:
            text_out = cleaned.rstrip() + f"\n\nERR:\n{e}"
    else:
        try:
            await query.answer(text="⛔ סוג לא נתמך", show_alert=False)
        except Exception:
            pass
        return

    full_text = text_out
    display_text = full_text if len(full_text) <= INLINE_PREVIEW_MAX else (full_text[:INLINE_PREVIEW_MAX] + "\n\n…(נשלח קובץ מלא בפרטי)")

    try:
        # אחרי ריצה, נמשיך לאפשר רענון נוסף עם טוקן חדש
        new_token = secrets.token_urlsafe(8)
        INLINE_EXEC_STORE[new_token] = {"type": run_type, "q": q, "user_id": user_id, "ts": time.time()}
        prune_inline_exec_store()
        await query.edit_message_text(text=display_text, reply_markup=_make_refresh_markup(new_token))
        await query.answer()
    except Exception:
        try:
            await _.bot.send_message(chat_id=user_id, text=display_text)
            await query.answer()
        except Exception:
            pass

    # נקה טוקן ישן
    try:
        INLINE_EXEC_STORE.pop(token, None)
        prune_inline_exec_store()
    except Exception:
        pass

    # אם קיצרנו – נשלח קובץ מלא בפרטי
    try:
        if len(full_text) > INLINE_PREVIEW_MAX and user_id:
            bio = io.BytesIO(full_text.encode("utf-8"))
            bio.name = "inline-output.txt"
            await _.bot.send_document(chat_id=user_id, document=bio, caption="(full output)")
    except Exception:
        pass
async def sh_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    report_nowait(update.effective_user.id if update.effective_user else 0)
    if not allowed(update):
        return

    cmdline = update.message.text.partition(" ")[2].strip()
    cmdline = normalize_code(cmdline)
    if not cmdline:
        return await update.message.reply_text("שימוש: /sh <פקודה> | /py <קוד> | /js <קוד JS> | /java <קוד Java>")

    sess = get_session(update)

    # Builtins: cd/export/unset
    builtin_resp = handle_builtins(sess, cmdline)
    if builtin_resp is not None:
        return await send_output(update, builtin_resp, "builtin.txt")

    # אימות: אם ALLOW_ALL_COMMANDS פעיל – אין אימות. אחרת, תמיד מאמתים את הטוקן הראשון
    if not ALLOW_ALL_COMMANDS:
        try:
            parts = shlex.split(cmdline, posix=True)
        except ValueError:
            return await update.message.reply_text("❗ שגיאת פרסינג")
        if not parts:
            return await update.message.reply_text("❗ אין פקודה")
        first_token = parts[0].strip()
        if first_token and first_token not in ALLOWED_CMDS:
            return await update.message.reply_text(f"❗ פקודה לא מאושרת: {first_token}")

    # הרצה בשלם (תומך בצינורות/&&/;) בתוך shell שהוגדר (ברירת מחדל bash)
    try:
        shell_exec = SHELL_EXECUTABLE or "/bin/bash"
        p = subprocess.run(
            [shell_exec, "-c", cmdline],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            cwd=sess["cwd"],
            env=sess["env"],
        )
        out = p.stdout or ""
        err = p.stderr or ""
        resp = f"$ {cmdline}\n\n{out}"
        if err:
            resp += "\nERR:\n" + err
        resp = truncate(resp.strip() or "(no output)")
    except subprocess.TimeoutExpired:
        resp = f"$ {cmdline}\n\n⏱️ Timeout"
    except Exception as e:
        resp = truncate(f"$ {cmdline}\n\nERR:\n{e}")

    await send_output(update, resp, "output.txt")


def _parse_cmds_args(arg_text: str) -> set:
    return _parse_cmds_string(arg_text)


async def list_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    report_nowait(update.effective_user.id if update.effective_user else 0)
    if not allowed(update):
        return await update.message.reply_text("❌ אין הרשאה")
    if not ALLOWED_CMDS:
        return await update.message.reply_text("(רשימה ריקה)")
    await update.message.reply_text(",".join(sorted(ALLOWED_CMDS)))


async def allow_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    report_nowait(update.effective_user.id if update.effective_user else 0)
    if not allowed(update):
        return await update.message.reply_text("❌ אין הרשאה")
    args = update.message.text.partition(" ")[2]
    to_add = _parse_cmds_args(args)
    if not to_add:
        return await update.message.reply_text("שימוש: /allow cmd1,cmd2,...")
    before = set(ALLOWED_CMDS)
    ALLOWED_CMDS.update(to_add)
    if ALLOWED_CMDS != before:
        save_allowed_cmds_to_file()
    await update.message.reply_text("נוספו: " + ",".join(sorted(to_add)))


async def deny_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    report_nowait(update.effective_user.id if update.effective_user else 0)
    if not allowed(update):
        return await update.message.reply_text("❌ אין הרשאה")
    args = update.message.text.partition(" ")[2]
    to_remove = _parse_cmds_args(args)
    if not to_remove:
        return await update.message.reply_text("שימוש: /deny cmd1,cmd2,...")
    changed = False
    for c in to_remove:
        if c in ALLOWED_CMDS:
            ALLOWED_CMDS.discard(c)
            changed = True
    if changed:
        save_allowed_cmds_to_file()
    await update.message.reply_text("הוסרו: " + ",".join(sorted(to_remove)))


async def update_allow_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    report_nowait(update.effective_user.id if update.effective_user else 0)
    if not allowed(update):
        return await update.message.reply_text("❌ אין הרשאה")
    args = update.message.text.partition(" ")[2]
    new_set = _parse_cmds_args(args)
    if not new_set:
        return await update.message.reply_text("שימוש: /update cmd1,cmd2,...")
    global ALLOWED_CMDS
    ALLOWED_CMDS = set(new_set)
    save_allowed_cmds_to_file()
    await update.message.reply_text("עודכן. כעת מאושרות: " + ",".join(sorted(ALLOWED_CMDS)))


async def py_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    report_nowait(update.effective_user.id if update.effective_user else 0)
    if not allowed(update):
        return

    code = update.message.text.partition(" ")[2]
    if not code.strip():
        return await update.message.reply_text("שימוש: /py <קוד פייתון>")

    # ניקוי ופירמוט קוד
    cleaned = textwrap.dedent(code)
    cleaned = normalize_code(cleaned).strip("\n") + "\n"

    def _exec_in_context(src: str, chat_id: int, _update: Update, _context: ContextTypes.DEFAULT_TYPE):
        global PY_CONTEXT
        # אתחול ראשוני של הקשר ההרצה לצ'אט הנוכחי
        ctx = PY_CONTEXT.get(chat_id)
        if ctx is None:
            ctx = {"__builtins__": __builtins__, "__name__": "__main__"}
            PY_CONTEXT[chat_id] = ctx
        else:
            ctx.setdefault("__name__", "__main__")
        # חשיפת אובייקטי טלגרם כדי לאפשר שימוש ישיר בקוד
        ctx["update"] = _update
        ctx["context"] = _context
        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        tb_text = None
        try:
            with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
                exec(src, ctx, ctx)
        except Exception:
            tb_text = traceback.format_exc()
        return stdout_buffer.getvalue(), stderr_buffer.getvalue(), tb_text

    try:
        chat_id = _chat_id(update)
        out, err, tb_text = await asyncio.wait_for(
            asyncio.to_thread(_exec_in_context, cleaned, chat_id, update, _), timeout=TIMEOUT
        )

        # Attempt dynamic install on ModuleNotFoundError, up to 3 modules per run
        attempts = 0
        while tb_text and "ModuleNotFoundError" in tb_text and attempts < 3:
            m = re.search(r"ModuleNotFoundError: No module named '([^']+)'", tb_text)
            if not m:
                break
            missing_mod = m.group(1)
            if not is_safe_pip_name(missing_mod):
                await update.message.reply_text(f"❌ שם מודול לא תקין להתקנה: '{missing_mod}'")
                break
            try:
                await update.message.reply_text(f"📦 מתקין את '{missing_mod}' (pip)…")
                await asyncio.wait_for(asyncio.to_thread(install_package, missing_mod), timeout=PIP_TIMEOUT)
                await update.message.reply_text(f"✅ '{missing_mod}' הותקן. מריץ שוב…")
            except asyncio.TimeoutError:
                await update.message.reply_text(f"⏱️ Timeout בהתקנת '{missing_mod}' לאחר {PIP_TIMEOUT}s")
                break
            except subprocess.CalledProcessError as e:
                await update.message.reply_text(f"❌ כשל בהתקנת '{missing_mod}' (קוד {e.returncode})")
                break
            attempts += 1
            out, err, tb_text = await asyncio.wait_for(
                asyncio.to_thread(_exec_in_context, cleaned, chat_id, update, _),
                timeout=TIMEOUT,
            )

        parts = []
        if out.strip():
            parts.append(out.rstrip())
        if err.strip():
            parts.append("STDERR:\n" + err.rstrip())
        if tb_text and tb_text.strip():
            parts.append(tb_text.rstrip())

        resp = "\n".join(parts).strip()

        # אם אין פלט – ננסה להעריך את הביטוי האחרון (כמו REPL)
        if not resp:
            try:
                mod = ast.parse(cleaned, mode="exec")
                if getattr(mod, "body", None):
                    last = mod.body[-1]
                    if isinstance(last, ast.Expr):
                        expr_code = compile(ast.Expression(last.value), filename="<py>", mode="eval")
                        ctx = PY_CONTEXT.get(chat_id) or {}
                        result = eval(expr_code, ctx, ctx)
                        if inspect.isawaitable(result):
                            result = await result
                        if result is not None:
                            resp = str(result)
            except Exception:
                # אם נכשל – נשאיר resp ריק
                pass

        resp = resp or "(no output)"
        await send_output(update, truncate(resp), "py-output.txt")
    except asyncio.TimeoutError:
        await send_output(update, "⏱️ Timeout", "py-output.txt")
    except Exception as e:
        await send_output(update, f"ERR:\n{e}", "py-output.txt")


async def js_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    report_nowait(update.effective_user.id if update.effective_user else 0)
    if not allowed(update):
        return

    code = update.message.text.partition(" ")[2]
    if not code.strip():
        return await update.message.reply_text("שימוש: /js <קוד JS>")

    # ניקוי ופירמוט קוד
    cleaned = textwrap.dedent(code)
    cleaned = normalize_code(cleaned).strip("\n") + "\n"

    sess = get_session(update)

    try:
        p = await asyncio.to_thread(run_js_blocking, cleaned, sess["cwd"], sess["env"], TIMEOUT)
        out = (p.stdout or "").rstrip()
        err = (p.stderr or "").rstrip()
        parts = [cleaned.rstrip() + "\n\n"]
        if out:
            parts.append(out)
        if err:
            parts.append("STDERR:\n" + err)
        resp = "\n".join(parts).strip() or "(no output)"
        await send_output(update, truncate(resp), "js-output.txt")
    except subprocess.TimeoutExpired:
        await send_output(update, cleaned.rstrip() + "\n\n⏱️ Timeout", "js-output.txt")
    except FileNotFoundError:
        await send_output(update, cleaned.rstrip() + "\n\n❌ node לא נמצא במערכת", "js-output.txt")
    except Exception as e:
        await send_output(update, cleaned.rstrip() + f"\n\nERR:\n{e}", "js-output.txt")


async def java_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    report_nowait(update.effective_user.id if update.effective_user else 0)
    if not allowed(update):
        return

    code = update.message.text.partition(" ")[2]
    if not code.strip():
        return await update.message.reply_text("שימוש: /java <קוד Java>")

    # ניקוי ופירמוט קוד
    cleaned = textwrap.dedent(code)
    cleaned = normalize_code(cleaned).strip("\n") + "\n"

    sess = get_session(update)

    try:
        p = await asyncio.to_thread(run_java_blocking, cleaned, sess["cwd"], sess["env"], TIMEOUT)
        out = (p.stdout or "").rstrip()
        err = (p.stderr or "").rstrip()
        parts = [cleaned.rstrip() + "\n\n"]
        if out:
            parts.append(out)
        if err:
            parts.append("STDERR:\n" + err)
        resp = "\n".join(parts).strip() or "(no output)"
        await send_output(update, truncate(resp), "java-output.txt")
    except subprocess.TimeoutExpired:
        await send_output(update, cleaned.rstrip() + "\n\n⏱️ Timeout", "java-output.txt")
    except FileNotFoundError:
        await send_output(update, cleaned.rstrip() + "\n\n❌ javac/java לא נמצאו במערכת", "java-output.txt")
    except Exception as e:
        await send_output(update, cleaned.rstrip() + f"\n\nERR:\n{e}", "java-output.txt")


async def call_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    """קורא לפונקציה בשם נתון מתוך הקשר /py של הצ'אט.
    שימוש: /call func_name [args...]
    - מזהה אוטומטית פונקציות sync/async
    - אם החתימה כוללת פרמטר בשם 'update' – נעביר את אובייקט ה-Update
      ואם כוללת 'context' או 'ctx' – נעביר את אובייקט ה-Context
    - יתר הארגומנטים יועברו כמחרוזות/מספרים (ניסיון המרה ל-int/float)
    """
    report_nowait(update.effective_user.id if update.effective_user else 0)
    if not allowed(update):
        return

    cmdline = (update.message.text or "").partition(" ")[2].strip()
    if not cmdline:
        return await update.message.reply_text("שימוש: /call <שם_פונקציה> [ארגומנטים]")

    try:
        tokens = shlex.split(cmdline, posix=True)
    except ValueError:
        return await update.message.reply_text("❗ שגיאת פרסינג בארגומנטים")
    if not tokens:
        return await update.message.reply_text("❗ לא צוין שם פונקציה")

    func_name = tokens[0]
    raw_args = tokens[1:]

    chat_id = _chat_id(update)
    ctx = PY_CONTEXT.get(chat_id)
    if ctx is None or func_name not in ctx:
        return await update.message.reply_text(f"❗ הפונקציה '{func_name}' לא נמצאה בהקשר הנוכחי. הגדר אותה קודם עם /py.")

    func = ctx.get(func_name)
    if not callable(func):
        return await update.message.reply_text(f"❗ '{func_name}' אינה פונקציה")

    sig = None
    try:
        sig = inspect.signature(func)
    except Exception:
        sig = None

    # בניית ארגומנטים
    def _parse_arg(tok: str):
        try:
            if tok.lower() in ("true", "false"):
                return tok.lower() == "true"
            if tok.startswith("0x"):
                return int(tok, 16)
            if tok.isdigit() or (tok.startswith("-") and tok[1:].isdigit()):
                return int(tok)
            f = float(tok)
            return f
        except Exception:
            return tok

    pos_args = [_parse_arg(a) for a in raw_args]
    kw_args = {}

    has_update = False
    ctx_param_name = None
    if sig is not None:
        param_names = list(sig.parameters.keys())
        has_update = "update" in param_names
        # הזרקת update/context לפי שם פרמטר
        if has_update:
            kw_args["update"] = update
        for cand in ("context", "ctx"):
            if cand in param_names:
                kw_args[cand] = _
                ctx_param_name = cand
                break

    # אם זו פונקציית-בוט (עם update/context), נעדיף להעביר ארגומנטים דרך context.args כטקסטים
    # כדי לתמוך בקוד בסגנון PTB שמסתמך על context.args
    if has_update or ctx_param_name is not None:
        prev_args = getattr(_, "args", None)
        try:
            try:
                _.args = [str(a) for a in raw_args]
            except Exception:
                setattr(_, "args", [str(a) for a in raw_args])
            pos_for_call = []  # בלי ארגומנטים פוזיציוניים כדי לא להתנגש בחתימה
            buf = io.StringIO()

            async def _run_call():
                with contextlib.redirect_stdout(buf):
                    value = func(*pos_for_call, **kw_args)
                    if inspect.isawaitable(value):
                        value = await value
                    return value

            try:
                result = await asyncio.wait_for(_run_call(), timeout=TIMEOUT)
            finally:
                # שחזור args לקדמותו
                try:
                    _.args = prev_args
                except Exception:
                    pass
        except asyncio.TimeoutError:
            return await send_output(update, "⏱️ Timeout", "call-output.txt")
        except Exception as e:
            return await send_output(update, f"ERR:\n{e}", "call-output.txt")
    else:
        # פונקציה רגילה ללא update/context – נעביר ארגומנטים פוזיציוניים מומרי טיפוס
        buf = io.StringIO()

        async def _run_call():
            with contextlib.redirect_stdout(buf):
                value = func(*pos_args)
                if inspect.isawaitable(value):
                    value = await value
                return value

        try:
            result = await asyncio.wait_for(_run_call(), timeout=TIMEOUT)
        except asyncio.TimeoutError:
            return await send_output(update, "⏱️ Timeout", "call-output.txt")
        except Exception as e:
            return await send_output(update, f"ERR:\n{e}", "call-output.txt")

    out_text = buf.getvalue().strip()
    parts = []
    if out_text:
        parts.append(out_text)
    if result is not None and str(result).strip():
        parts.append(str(result))
    resp = "\n".join(parts).strip() or "✓ בוצע"
    await send_output(update, truncate(resp), "call-output.txt")

async def py_start_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    """מתחיל איסוף קוד רב-הודעות עבור הצ'אט הנוכחי."""
    report_nowait(update.effective_user.id if update.effective_user else 0)
    if not allowed(update):
        return
    chat_id = _chat_id(update)
    PY_COLLECT[chat_id] = []
    await update.message.reply_text("🧰 מתחיל איסוף קוד. שלחו הודעות טקסט. סיום עם /py_run")


async def py_run_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    """מאחד את כל ההודעות שנאספו ומריץ אותן כבלוק פייתון אחד."""
    report_nowait(update.effective_user.id if update.effective_user else 0)
    if not allowed(update):
        return
    chat_id = _chat_id(update)
    parts = PY_COLLECT.get(chat_id)
    if not parts:
        return await update.message.reply_text("❗ אין מה להריץ. השתמשו ב-/py_start ואז שלחו הודעות.")

    # איחוד + ניקוי
    code_joined = "\n".join(parts)
    cleaned = textwrap.dedent(code_joined)
    cleaned = normalize_code(cleaned).strip("\n") + "\n"

    def _exec_in_context(src: str, chat: int):
        global PY_CONTEXT
        ctx = PY_CONTEXT.get(chat)
        if ctx is None:
            ctx = {"__builtins__": __builtins__}
            PY_CONTEXT[chat] = ctx
        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        tb_text = None
        try:
            with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
                exec(src, ctx, ctx)
        except Exception:
            tb_text = traceback.format_exc()
        return stdout_buffer.getvalue(), stderr_buffer.getvalue(), tb_text

    try:
        out, err, tb_text = await asyncio.wait_for(asyncio.to_thread(_exec_in_context, cleaned, chat_id), timeout=TIMEOUT)

        # התקנה דינמית עבור ModuleNotFoundError (עד 3 ניסיונות)
        attempts = 0
        while tb_text and "ModuleNotFoundError" in tb_text and attempts < 3:
            m = re.search(r"ModuleNotFoundError: No module named '([^']+)'", tb_text)
            if not m:
                break
            missing_mod = m.group(1)
            if not is_safe_pip_name(missing_mod):
                await update.message.reply_text(f"❌ שם מודול לא תקין להתקנה: '{missing_mod}'")
                break
            try:
                await update.message.reply_text(f"📦 מתקין את '{missing_mod}' (pip)…")
                await asyncio.wait_for(asyncio.to_thread(install_package, missing_mod), timeout=PIP_TIMEOUT)
                await update.message.reply_text(f"✅ '{missing_mod}' הותקן. מריץ שוב…")
            except asyncio.TimeoutError:
                await update.message.reply_text(f"⏱️ Timeout בהתקנת '{missing_mod}' לאחר {PIP_TIMEOUT}s")
                break
            except subprocess.CalledProcessError as e:
                await update.message.reply_text(f"❌ כשל בהתקנת '{missing_mod}' (קוד {e.returncode})")
                break
            attempts += 1
            out, err, tb_text = await asyncio.wait_for(asyncio.to_thread(_exec_in_context, cleaned, chat_id), timeout=TIMEOUT)

        resp_parts = []
        if out.strip():
            resp_parts.append(out.rstrip())
        if err.strip():
            resp_parts.append("STDERR:\n" + err.rstrip())
        if tb_text and tb_text.strip():
            resp_parts.append(tb_text.rstrip())
        resp = "\n".join(resp_parts).strip() or "(no output)"
        await send_output(update, truncate(resp), "py-output.txt")
    except asyncio.TimeoutError:
        await send_output(update, "⏱️ Timeout", "py-output.txt")
    except Exception as e:
        await send_output(update, f"ERR:\n{e}", "py-output.txt")
    finally:
        # איפוס המאגר לאחר הרצה
        PY_COLLECT.pop(chat_id, None)


async def collect_text_handler(update: Update, _: ContextTypes.DEFAULT_TYPE):
    """אוסף הודעות טקסט לצורך /py_start → /py_run, ללא פקודות."""
    if not update or not update.message or not update.message.text:
        return
    if not allowed(update):
        return
    chat_id = _chat_id(update)
    if chat_id not in PY_COLLECT:
        return
    text = update.message.text
    # לא לאסוף הודעות שהן בעצם פקודות (גיבוי נוסף מעבר ל-filter)
    if text.strip().startswith("/"):
        return
    PY_COLLECT[chat_id].append(text)
    await update.message.reply_text(f"✅ נוספה. סה\"כ {len(PY_COLLECT[chat_id])} הודעות בקוד.")

async def env_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    report_nowait(update.effective_user.id if update.effective_user else 0)
    if not allowed(update):
        return
    sess = get_session(update)
    lines = [f"PWD={sess['cwd']}"] + [f"{k}={v}" for k, v in sorted(sess["env"].items())]
    await send_output(update, "\n".join(lines), "env.txt")


async def reset_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    report_nowait(update.effective_user.id if update.effective_user else 0)
    if not allowed(update):
        return
    chat_id = _chat_id(update)
    sessions.pop(chat_id, None)
    await update.message.reply_text("♻️ הסשן אופס (cwd/env הוחזרו לברירת מחדל)")


async def clear_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    """מנקה את כל מצב הסשן לצ'אט הנוכחי, כולל הקשר /py, איסוף /py_start,
    מצב inline למשתמש, וטוקנים רלוונטיים.
    """
    report_nowait(update.effective_user.id if update.effective_user else 0)
    if not allowed(update):
        return
    chat_id = _chat_id(update)
    user_id = update.effective_user.id if update.effective_user else 0

    cleared = {
        "shell_session": False,
        "py_context": False,
        "py_collect": False,
        "inline_session": False,
        "inline_tokens_removed": 0,
    }

    try:
        if chat_id in sessions:
            sessions.pop(chat_id, None)
            cleared["shell_session"] = True
    except Exception:
        pass

    try:
        if chat_id in PY_CONTEXT:
            PY_CONTEXT.pop(chat_id, None)
            cleared["py_context"] = True
    except Exception:
        pass

    try:
        if chat_id in PY_COLLECT:
            PY_COLLECT.pop(chat_id, None)
            cleared["py_collect"] = True
    except Exception:
        pass

    try:
        if user_id:
            if str(user_id) in INLINE_SESSIONS:
                INLINE_SESSIONS.pop(str(user_id), None)
                cleared["inline_session"] = True
            # ניקוי טוקנים של המשתמש מחנות ה-inline
            removed = 0
            for k in list(INLINE_EXEC_STORE.keys()):
                try:
                    if INLINE_EXEC_STORE.get(k, {}).get("user_id") == user_id:
                        INLINE_EXEC_STORE.pop(k, None)
                        removed += 1
                except Exception:
                    pass
            if removed:
                cleared["inline_tokens_removed"] = removed
            prune_inline_exec_store()
    except Exception:
        pass

    parts = ["✅ בוצע ניקוי:"]
    if cleared["shell_session"]:
        parts.append("- cwd/env לסשן הוחזרו לברירת מחדל")
    if cleared["py_context"]:
        parts.append("- הקשר /py לצ'אט אופס")
    if cleared["py_collect"]:
        parts.append("- איסוף /py_start נוקה")
    if cleared["inline_session"] or cleared["inline_tokens_removed"]:
        extra = []
        if cleared["inline_session"]:
            extra.append("inline session")
        if cleared["inline_tokens_removed"]:
            extra.append(f"{cleared['inline_tokens_removed']} טוקני inline")
        parts.append("- נוקה מצב אינליין למשתמש (" + ", ".join(extra) + ")")
    if len(parts) == 1:
        parts.append("(לא נמצא מה לנקות)")

    await update.message.reply_text("\n".join(parts))


async def health_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    report_nowait(update.effective_user.id if update.effective_user else 0)
    if not allowed(update):
        return
    try:
        socket.create_connection(("api.telegram.org", 443), timeout=3).close()
        await update.message.reply_text("✅ OK")
    except OSError:
        await update.message.reply_text("❌ אין חיבור")


async def whoami_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    """מציג את מזהה המשתמש שלך (user id) לנוחות הגדרת OWNER_ID."""
    uid = update.effective_user.id if update.effective_user else 0
    await update.message.reply_text(str(uid))


async def debug_on_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    global INLINE_DEBUG_FLAG, INLINE_DEBUG_SENT
    if not allowed(update):
        return
    INLINE_DEBUG_FLAG = True
    INLINE_DEBUG_SENT = False
    await update.message.reply_text("✅ Debug ON")


async def debug_off_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    global INLINE_DEBUG_FLAG
    if not allowed(update):
        return
    INLINE_DEBUG_FLAG = False
    await update.message.reply_text("✅ Debug OFF")


async def restart_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    report_nowait(update.effective_user.id if update.effective_user else 0)
    if not allowed(update):
        return
    try:
        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id:
            tmp_dir = os.path.dirname(RESTART_NOTIFY_PATH) or "."
            os.makedirs(tmp_dir, exist_ok=True)
            with open(RESTART_NOTIFY_PATH, "w", encoding="utf-8") as fh:
                json.dump({"chat_id": chat_id}, fh)
    except Exception:
        pass
    await update.message.reply_text("🔄 Restart…")
    time.sleep(1)
    os._exit(0)


# ==== main ====
def main():
    # לוגים שקטים (רק ERROR) כדי למנוע ספאם
    import logging
    logging.basicConfig(level=logging.CRITICAL)
    for n in ("telegram", "telegram.ext", "httpx"):
        logging.getLogger(n).setLevel(logging.CRITICAL)

    token = os.getenv("BOT_TOKEN")
    if not token:
        return

    while True:
        app = Application.builder().token(token).post_init(on_post_init).build()

        app.add_handler(InlineQueryHandler(inline_query))
        app.add_handler(ChosenInlineResultHandler(on_chosen_inline_result))
        app.add_handler(CallbackQueryHandler(handle_refresh_callback, pattern=r"^refresh:"))
        app.add_handler(CallbackQueryHandler(show_commands_callback, pattern=r"^show_commands$"))
        app.add_handler(CallbackQueryHandler(back_to_start_callback, pattern=r"^back_to_start$"))
        # קלטים בסיסיים
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("webapp", webapp_cmd))
        app.add_handler(CommandHandler("sh", sh_cmd))
        app.add_handler(CommandHandler("py", py_cmd))
        app.add_handler(CommandHandler("js", js_cmd))
        app.add_handler(CommandHandler("java", java_cmd))
        # פקודות כלליות בלבד; אין תלות בדוגמאות ספציפיות
        app.add_handler(CommandHandler("call", call_cmd))
        # איסוף קוד רב-הודעות
        app.add_handler(CommandHandler("py_start", py_start_cmd))
        app.add_handler(CommandHandler("py_run", py_run_cmd))
        # איסוף הודעות טקסט רגילות בין /py_start ל-/py_run
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, collect_text_handler))
        app.add_handler(CommandHandler("env", env_cmd))
        app.add_handler(CommandHandler("reset", reset_cmd))
        app.add_handler(CommandHandler("clear", clear_cmd))
        app.add_handler(CommandHandler("health", health_cmd))
        app.add_handler(CommandHandler("whoami", whoami_cmd))
        app.add_handler(CommandHandler("debug_on", debug_on_cmd))
        app.add_handler(CommandHandler("debug_off", debug_off_cmd))
        app.add_handler(CommandHandler("restart", restart_cmd))
        app.add_handler(CommandHandler("list", list_cmd))
        app.add_handler(CommandHandler("allow", allow_cmd))
        app.add_handler(CommandHandler("deny", deny_cmd))
        app.add_handler(CommandHandler("update", update_allow_cmd))

        try:
            app.run_polling(
                drop_pending_updates=True,
                poll_interval=1.5,
                timeout=10,
            )
        except Conflict:
            # אינסטנס אחר רץ – נחכה וננסה שוב
            time.sleep(int(os.getenv("CONFLICT_RETRY_DELAY", "120")))
            continue
        except (NetworkError, TimedOut):
            # בעיות רשת זמניות – נחכה מעט וננסה שוב
            time.sleep(5)
            continue
        break


if __name__ == "__main__":
    main()
