# Code Snippets - בוטים וזרימות טלגרם

ספריית סניפטים מקוריים לפי קוד מהריפו. כל סניפט כולל שם ברור, הסבר קצר, וקוד ליבה.

---

## 29. In-Memory Store עם TTL וגבלת גודל

**למה זה שימושי:**
מאחסן נתונים זמניים (כמו תוצאות inline) עם ניקוי אוטומטי לפי זמן וגודל מקסימלי. מונע דליפת זיכרון.

```python
import time

INLINE_EXEC_STORE = {}
INLINE_EXEC_TTL = 180  # 3 דקות
INLINE_EXEC_MAX = 5000  # מקסימום רשומות

def prune_inline_exec_store(now_ts: float | None = None) -> tuple[int, int]:
    """מסיר רשומות פגות תוקף ומגביל גודל מקסימלי.
    מחזיר (expired_removed, trimmed_removed)."""
    now = now_ts if now_ts else time.time()

    # הסרת פגי תוקף
    expired_keys = [
        k for k, v in INLINE_EXEC_STORE.items()
        if now - float(v.get("ts", 0)) > INLINE_EXEC_TTL
    ]
    for k in expired_keys:
        INLINE_EXEC_STORE.pop(k, None)

    # הגבלת גודל - מחיקה לפי הישן ביותר
    trimmed = 0
    if len(INLINE_EXEC_STORE) > INLINE_EXEC_MAX:
        by_age = sorted(INLINE_EXEC_STORE.items(),
                        key=lambda kv: float(kv[1].get("ts", 0)))
        overflow = len(INLINE_EXEC_STORE) - INLINE_EXEC_MAX
        for i in range(overflow):
            INLINE_EXEC_STORE.pop(by_age[i][0], None)
            trimmed += 1

    return (len(expired_keys), trimmed)
```

---

## 30. הזרקת פרמטרים דינמית לפי חתימת פונקציה

**למה זה שימושי:**
מזהה אוטומטית אם פונקציה מצפה ל-update/context ומזריק אותם. מאפשר קריאה לפונקציות בסגנון PTB בצורה גמישה.

```python
import inspect

def inject_telegram_params(func, update, context, raw_args):
    """מזהה פרמטרים בחתימה ומזריק update/context לפי הצורך."""
    sig = inspect.signature(func)
    param_names = list(sig.parameters.keys())

    kw_args = {}

    # הזרקת update אם נדרש
    if "update" in param_names:
        kw_args["update"] = update

    # הזרקת context לפי שמות נפוצים
    for name in ("context", "ctx"):
        if name in param_names:
            kw_args[name] = context
            break

    # העברת ארגומנטים דרך context.args (סגנון PTB)
    if "update" in param_names or "context" in param_names:
        prev_args = getattr(context, "args", None)
        try:
            context.args = [str(a) for a in raw_args]
            return func(**kw_args), prev_args
        finally:
            context.args = prev_args

    return func(*raw_args), None
```

---

## 31. התקנת חבילות חסרות אוטומטית

**למה זה שימושי:**
כשהקוד נכשל בגלל מודול חסר, מנסה להתקין אותו עם pip ולהריץ שוב. מאפשר למשתמשים להריץ קוד עם תלויות חדשות.

```python
import re
import asyncio
import subprocess
import sys

SAFE_PIP_NAME_RE = re.compile(r'^(?![.-])[a-zA-Z0-9_.-]+$')

def is_safe_pip_name(name: str) -> bool:
    """בדיקה שהשם בטוח להתקנה עם pip."""
    return bool(SAFE_PIP_NAME_RE.match(name))

async def run_with_auto_install(code, exec_func, update, max_attempts=3):
    """מריץ קוד עם התקנה אוטומטית של מודולים חסרים."""
    attempts = 0
    out, err, tb = await exec_func(code)

    while tb and "ModuleNotFoundError" in tb and attempts < max_attempts:
        match = re.search(r"ModuleNotFoundError: No module named '([^']+)'", tb)
        if not match:
            break

        missing = match.group(1)
        if not is_safe_pip_name(missing):
            await update.message.reply_text(f"❌ שם מודול לא תקין: '{missing}'")
            break

        await update.message.reply_text(f"📦 מתקין '{missing}'...")
        try:
            await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-m", "pip", "install", missing],
                check=True, timeout=120
            )
            await update.message.reply_text(f"✅ הותקן. מריץ שוב...")
        except Exception as e:
            await update.message.reply_text(f"❌ כשל בהתקנה: {e}")
            break

        attempts += 1
        out, err, tb = await exec_func(code)

    return out, err, tb
```

---

## 32. הערכת ביטוי אחרון (כמו REPL)

**למה זה שימושי:**
אם הקוד לא מדפיס פלט, מעריך את הביטוי האחרון ומציג את התוצאה - בדיוק כמו Python REPL.

```python
import ast
import inspect

async def eval_last_expression(code: str, context: dict) -> str | None:
    """מעריך את הביטוי האחרון בקוד אם אין פלט."""
    try:
        mod = ast.parse(code, mode="exec")
        if not getattr(mod, "body", None):
            return None

        last = mod.body[-1]
        if not isinstance(last, ast.Expr):
            return None

        expr_code = compile(ast.Expression(last.value),
                           filename="<repl>", mode="eval")
        result = eval(expr_code, context, context)

        # תמיכה ב-async
        if inspect.isawaitable(result):
            result = await result

        return str(result) if result is not None else None
    except Exception:
        return None
```

---

## 33. פאגינציה ל-Inline Query עם טוקנים

**למה זה שימושי:**
מנהל פאגינציה לתוצאות inline עם טוקנים ייחודיים לכל תוצאה. מאפשר תפריטים גדולים ומעקב אחר בחירות.

```python
import hashlib
import secrets
from telegram import InlineQueryResultArticle, InputTextMessageContent

PAGE_SIZE = 10
EXEC_STORE = {}  # אחסון טוקנים

async def handle_inline_query(update, context):
    query = update.inline_query.query or ""
    offset = int(update.inline_query.offset or 0)
    user_id = update.inline_query.from_user.id

    # סינון תוצאות
    all_items = get_available_commands()  # רשימת הפריטים
    if query:
        all_items = [c for c in all_items if query.lower() in c.lower()]

    # חיתוך לעמוד הנוכחי
    page = all_items[offset:offset + PAGE_SIZE]
    qhash = hashlib.sha1(query.encode()).hexdigest()[:12]

    results = []
    for item in page:
        # יצירת טוקן ייחודי לכל תוצאה
        token = secrets.token_urlsafe(8)
        EXEC_STORE[token] = {
            "type": "command",
            "item": item,
            "user_id": user_id,
            "ts": time.time()
        }

        results.append(InlineQueryResultArticle(
            id=f"run:{token}:{offset}",
            title=f"הרץ: {item}",
            input_message_content=InputTextMessageContent(f"/sh {item}")
        ))

    # חישוב offset הבא
    next_offset = str(offset + PAGE_SIZE) if offset + PAGE_SIZE < len(all_items) else ""

    await update.inline_query.answer(
        results,
        cache_time=0,
        is_personal=True,
        next_offset=next_offset
    )
```

---

## 34. הרצת Java עם זיהוי שם מחלקה

**למה זה שימושי:**
מזהה אוטומטית את שם ה-public class בקוד Java ויוצר קובץ עם השם הנכון. מאפשר הרצת קוד Java שרירותי.

```python
import os
import re
import shutil
import tempfile
import subprocess

def run_java_blocking(src: str, cwd: str, env: dict, timeout: int):
    """מריץ קוד Java עם זיהוי אוטומטי של שם המחלקה."""
    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp()

        # זיהוי שם המחלקה הציבורית
        class_name = "Main"
        match = re.search(
            r'public\s+(?:final\s+|abstract\s+|static\s+)*class\s+(\w+)',
            src
        )
        if match:
            class_name = match.group(1)

        # כתיבת קובץ עם השם הנכון
        java_file = os.path.join(tmp_dir, f"{class_name}.java")
        with open(java_file, "w", encoding="utf-8") as f:
            f.write(src)

        # קומפילציה
        compile_result = subprocess.run(
            ["javac", java_file],
            capture_output=True, text=True,
            timeout=timeout, cwd=tmp_dir, env=env
        )

        if compile_result.returncode != 0:
            return compile_result  # שגיאת קומפילציה

        # הרצה
        return subprocess.run(
            ["java", class_name],
            capture_output=True, text=True,
            timeout=timeout, cwd=tmp_dir, env=env
        )
    finally:
        if tmp_dir and os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir)
```

---

## 35. מטפל בפקודות Shell מובנות (cd, export, unset)

**למה זה שימושי:**
מאפשר שמירת מצב סשן (ספריה נוכחית, משתני סביבה) בין פקודות. תומך ב-cd -, export ו-unset.

```python
import os
import shlex

def handle_builtins(session: dict, cmdline: str) -> str | None:
    """מטפל בפקודות מובנות של shell. מחזיר תשובה או None אם לא מובנית."""
    # לא מטפל בפקודות מורכבות
    if any(x in cmdline for x in (";", "&&", "||", "|", "\n")):
        return None

    try:
        parts = shlex.split(cmdline, posix=True)
    except ValueError:
        return "❗ שגיאת פרסינג"

    if not parts:
        return "❗ אין פקודה"

    cmd = parts[0]

    if cmd == "cd":
        target = parts[1] if len(parts) > 1 else os.path.expanduser("~")

        # תמיכה ב-cd -
        if target == "-":
            target = session.get("prev_cwd", session.get("cwd", os.getcwd()))

        new_path = os.path.abspath(
            os.path.join(session.get("cwd", os.getcwd()),
                        os.path.expanduser(target))
        )

        if os.path.isdir(new_path):
            session["prev_cwd"] = session.get("cwd")
            session["cwd"] = new_path
            return f"📁 cwd: {new_path}"
        return f"❌ לא נמצא: {target}"

    if cmd == "export":
        env = session.setdefault("env", {})
        if len(parts) == 1:
            return "\n".join(f"{k}={v}" for k, v in sorted(env.items()))

        results = []
        for tok in parts[1:]:
            if "=" in tok:
                k, v = tok.split("=", 1)
                env[k] = v
                results.append(f"set {k}={v}")
            else:
                results.append(f"{tok}={env.get(tok, '')}")
        return "\n".join(results)

    if cmd == "unset":
        env = session.setdefault("env", {})
        for var in parts[1:]:
            env.pop(var, None)
        return f"unset: {', '.join(parts[1:])}"

    return None  # לא פקודה מובנית
```

---

## 36. יצירת PTY Session עם ניקוי בטוח

**למה זה שימושי:**
יוצר טרמינל אינטראקטיבי אמיתי (PTY) עם ניקוי מסודר של משאבים בכל מצב כשלון.

```python
import os
import pty
import fcntl
import signal
import termios
import uuid

pty_sessions = {}

def create_pty_session(user_id: int, shell: str = "/bin/bash") -> dict:
    """יוצר PTY session חדש עם ניקוי בטוח."""
    master_fd = slave_fd = child_pid = None

    try:
        master_fd, slave_fd = pty.openpty()
        pid = os.fork()

        if pid == 0:  # תהליך בן
            try:
                os.close(master_fd)
                os.setsid()
                fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
                os.dup2(slave_fd, 0)
                os.dup2(slave_fd, 1)
                os.dup2(slave_fd, 2)
                if slave_fd > 2:
                    os.close(slave_fd)

                env = os.environ.copy()
                env["TERM"] = "xterm-256color"
                os.execvpe(shell, [shell], env)
            except Exception:
                os._exit(1)

        else:  # תהליך אב
            child_pid = pid
            os.close(slave_fd)
            slave_fd = None

            session = {
                "session_id": str(uuid.uuid4()),
                "user_id": user_id,
                "master_fd": master_fd,
                "pid": pid,
            }
            pty_sessions[user_id] = session
            return session

    except Exception:
        # ניקוי במקרה כשלון
        if master_fd:
            os.close(master_fd)
        if slave_fd:
            os.close(slave_fd)
        if child_pid:
            os.kill(child_pid, signal.SIGKILL)
            os.waitpid(child_pid, os.WNOHANG)
        raise

def close_pty_session(session: dict):
    """סוגר PTY session בבטחה."""
    try:
        os.close(session["master_fd"])
    except:
        pass
    try:
        os.kill(session["pid"], signal.SIGTERM)
        import time
        time.sleep(0.1)
        os.kill(session["pid"], signal.SIGKILL)
    except:
        pass
    try:
        os.waitpid(session["pid"], os.WNOHANG)
    except:
        pass
```

---

## 37. דיווח פעילות ברקע ללא חסימה

**למה זה שימושי:**
שולח דיווח פעילות למסד נתונים מבלי לחסום את הטיפול בהודעה. משתמש ב-asyncio.create_task או thread כ-fallback.

```python
import asyncio
import threading

def report_nowait(reporter, user_id: int) -> None:
    """דיווח פעילות ברקע ללא עיכוב."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            asyncio.to_thread(reporter.report_activity, user_id)
        )
    except RuntimeError:
        # אין event loop פעיל - שימוש ב-thread
        threading.Thread(
            target=reporter.report_activity,
            args=(user_id,),
            daemon=True
        ).start()
```

---

## 38. נרמול קוד מקלט משתמש

**למה זה שימושי:**
מנקה תווים מוסתרים, גרשיים חכמים, ו-code fences שמגיעים מהעתקה מאפליקציות שונות. מונע שגיאות פרסינג.

```python
import re
import unicodedata

def normalize_code(text: str) -> str:
    """מנקה קוד מתווים בעייתיים."""
    if not text:
        return ""

    # נרמול Unicode
    text = unicodedata.normalize("NFKC", text)

    # גרשיים חכמים → רגילים
    text = text.replace(""", '"').replace(""", '"').replace("„", '"')
    text = text.replace("'", "'").replace("'", "'")

    # רווחים מיוחדים
    text = text.replace("\u00A0", " ")  # NBSP
    text = text.replace("\u202F", " ")  # Narrow NBSP

    # סימני כיוון
    text = text.replace("\u200E", "")  # LTR mark
    text = text.replace("\u200F", "")  # RTL mark

    # מקף רך
    text = text.replace("\u00AD", "")

    # שורות חדשות
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # הסרת code fences
    text = re.sub(r"(?m)^\s*```[a-zA-Z0-9_+\-]*\s*$", "", text)
    text = re.sub(r"(?m)^\s*```\s*$", "", text)

    return text
```

---

## 39. ארכיטקטורת Multi-Thread (בוט + שרת)

**למה זה שימושי:**
מריץ שרת Web ובוט טלגרם במקביל. הבוט חייב להיות ב-main thread בשביל signal handlers.

```python
import os
import sys
import time
import threading

def run_webapp_in_thread():
    """מריץ Flask ב-thread רקע."""
    import webapp_server
    host = os.getenv("WEBAPP_HOST", "0.0.0.0")
    port = int(os.getenv("WEBAPP_PORT", "8080"))
    webapp_server.app.run(
        host=host, port=port,
        debug=False, threaded=True, use_reloader=False
    )

def run_bot_in_main():
    """מריץ את הבוט ב-main thread (חובה ל-signal handlers)."""
    import bot
    bot.main()

def main():
    # שרת Web ב-daemon thread
    web_thread = threading.Thread(
        target=run_webapp_in_thread,
        daemon=True,
        name="webapp"
    )
    web_thread.start()

    # המתנה קצרה לאתחול
    time.sleep(1)

    if web_thread.is_alive():
        print(f"✅ Web App: http://0.0.0.0:{os.getenv('WEBAPP_PORT', '8080')}")

    # בוט ב-main thread
    try:
        run_bot_in_main()
    except KeyboardInterrupt:
        print("\n⏹️ Shutting down...")
        sys.exit(0)
```

---

## 40. אימות נתוני WebApp מטלגרם

**למה זה שימושי:**
מאמת שה-init_data הגיע באמת מטלגרם (HMAC). חובה לכל Web App שרוצה לדעת מי המשתמש.

```python
import hmac
import json
import time
import hashlib
from urllib.parse import parse_qsl, unquote

def validate_telegram_webapp_data(init_data: str, bot_token: str) -> dict | None:
    """מאמת נתוני WebApp מטלגרם. מחזיר dict או None אם לא תקין."""
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        if "hash" not in parsed:
            return None

        received_hash = parsed.pop("hash")

        # יצירת data-check-string
        data_check_string = "\n".join(
            f"{k}={parsed[k]}" for k in sorted(parsed.keys())
        )

        # חישוב hash
        secret_key = hmac.new(
            b"WebAppData",
            bot_token.encode(),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            return None

        # בדיקת תוקף (24 שעות)
        auth_date = int(parsed.get("auth_date", 0))
        if time.time() - auth_date > 86400:
            return None

        # פענוח user
        user_data = parsed.get("user", "{}")
        parsed["user"] = json.loads(unquote(user_data))

        return parsed
    except Exception:
        return None
```

---

## 41. עריכת הודעת Inline עם Fallback להודעה פרטית

**למה זה שימושי:**
מנסה לערוך הודעה שנשלחה מ-inline query. אם נכשל (אין הרשאה), שולח את התוצאה בפרטי.

```python
import secrets

PREVIEW_MAX = 800
EXEC_STORE = {}

async def update_inline_result(bot, inline_msg_id, user_id, result, run_type, query):
    """מעדכן תוצאת inline או שולח בפרטי אם נכשל."""

    # קיצור לתצוגה
    display = result if len(result) <= PREVIEW_MAX else (
        result[:PREVIEW_MAX] + "\n\n…(שאר נשלח בפרטי)"
    )

    # יצירת טוקן לרענון
    token = secrets.token_urlsafe(8)
    EXEC_STORE[token] = {
        "type": run_type,
        "q": query,
        "user_id": user_id
    }

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 רענון", callback_data=f"refresh:{token}")]
    ])

    if inline_msg_id:
        try:
            await bot.edit_message_text(
                inline_message_id=inline_msg_id,
                text=display,
                reply_markup=markup
            )
            return True
        except Exception:
            pass  # נכשל - ננסה fallback

    # Fallback: הודעה פרטית
    try:
        await bot.send_message(
            chat_id=user_id,
            text=result,  # שולחים את המלא
            reply_markup=markup
        )
        return True
    except Exception:
        return False
```

---

## 42. ניהול רשימת פקודות מותרות עם Persistence

**למה זה שימושי:**
שומר רשימת פקודות מאושרות לקובץ. תומך בטעינה מ-ENV, קובץ, או ברירת מחדל.

```python
import os

ALLOWED_CMDS_FILE = "allowed_cmds.txt"
DEFAULT_CMDS = {"ls", "pwd", "cat", "echo", "python", "pip", "git"}

def parse_cmds_string(value: str) -> set:
    """מפרסר רשימת פקודות מופרדות בפסיק/שורה."""
    if not value:
        return set()
    tokens = []
    for part in value.replace("\r", "").replace("\n", ",").split(","):
        tok = part.strip()
        if tok:
            tokens.append(tok)
    return set(tokens)

def load_allowed_cmds() -> set:
    """טוען פקודות מותרות לפי סדר עדיפות: ENV > קובץ > ברירת מחדל."""
    # קודם ENV
    env_cmds = os.getenv("ALLOWED_CMDS", "")
    if env_cmds:
        return parse_cmds_string(env_cmds)

    # אחר כך קובץ
    try:
        if os.path.exists(ALLOWED_CMDS_FILE):
            with open(ALLOWED_CMDS_FILE, "r") as f:
                parsed = parse_cmds_string(f.read())
                if parsed:
                    return parsed
    except Exception:
        pass

    return set(DEFAULT_CMDS)

def save_allowed_cmds(cmds: set) -> None:
    """שומר פקודות מותרות לקובץ."""
    try:
        with open(ALLOWED_CMDS_FILE, "w") as f:
            f.write("\n".join(sorted(cmds)))
    except Exception:
        pass

# שימוש בפקודות /allow, /deny:
async def allow_cmd(update, context):
    args = update.message.text.partition(" ")[2]
    to_add = parse_cmds_string(args)
    if not to_add:
        return await update.message.reply_text("שימוש: /allow cmd1,cmd2")

    ALLOWED_CMDS.update(to_add)
    save_allowed_cmds(ALLOWED_CMDS)
    await update.message.reply_text(f"נוספו: {', '.join(sorted(to_add))}")
```

---

## 43. דקורטור אימות Flask עם Dev Mode

**למה זה שימושי:**
מאמת משתמשים ב-production, מדלג על אימות ב-dev mode. קל לפיתוח מקומי ובטוח בפרודקשן.

```python
import os
from functools import wraps
from flask import request, jsonify

DEV_MODE = os.getenv("WEBAPP_DEV_MODE", "").lower() in ("1", "true")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_IDS = {123456789}  # IDs מורשים

def require_auth(f):
    """דקורטור אימות עם תמיכה ב-dev mode."""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Dev mode - דילוג על אימות
        if DEV_MODE:
            request.user_id = 0
            request.user_data = {"dev_mode": True}
            return f(*args, **kwargs)

        # Production - נדרש BOT_TOKEN
        if not BOT_TOKEN:
            return jsonify({
                "error": "Server misconfigured",
                "message": "BOT_TOKEN not set"
            }), 503

        init_data = request.headers.get("X-Telegram-Init-Data", "")
        data = validate_telegram_webapp_data(init_data, BOT_TOKEN)

        if not data:
            return jsonify({"error": "Unauthorized"}), 401

        user = data.get("user", {})
        user_id = user.get("id", 0)

        if user_id not in OWNER_IDS:
            return jsonify({"error": "Forbidden"}), 403

        request.user_id = user_id
        request.user_data = user
        return f(*args, **kwargs)

    return decorated

# שימוש:
@app.route("/api/execute", methods=["POST"])
@require_auth
def execute():
    user_id = request.user_id
    # ... לוגיקה
```

---

## 44. דיווח פעילות פשוט למסד נתונים

**למה זה שימושי:**
מדווח על פעילות משתמש למסד נתונים (MongoDB) בצורה שקטה - לא מפיל את הבוט גם אם יש שגיאה.

```python
from pymongo import MongoClient
from datetime import datetime, timezone

class SimpleActivityReporter:
    def __init__(self, mongodb_uri: str, service_id: str, service_name: str = None):
        try:
            self.client = MongoClient(mongodb_uri)
            self.db = self.client["bot_monitor"]
            self.service_id = service_id
            self.service_name = service_name or service_id
            self.connected = True
        except:
            self.connected = False

    def report_activity(self, user_id: int):
        """דיווח פעילות - שקט בכישלון."""
        if not self.connected:
            return

        try:
            now = datetime.now(timezone.utc)

            # עדכון אינטראקציית משתמש
            self.db.user_interactions.update_one(
                {"service_id": self.service_id, "user_id": user_id},
                {
                    "$set": {"last_interaction": now},
                    "$inc": {"interaction_count": 1},
                    "$setOnInsert": {"created_at": now}
                },
                upsert=True
            )

            # עדכון פעילות שירות
            self.db.service_activity.update_one(
                {"_id": self.service_id},
                {
                    "$set": {
                        "last_user_activity": now,
                        "service_name": self.service_name
                    },
                    "$setOnInsert": {"created_at": now}
                },
                upsert=True
            )
        except:
            pass  # שקט - לא להפיל את הבוט

# שימוש:
reporter = SimpleActivityReporter(
    mongodb_uri="mongodb://...",
    service_id="my-bot",
    service_name="MyTelegramBot"
)
reporter.report_activity(user_id=123456)
```

---

## 45. חלוקת טקסט לפי שורות עם מגבלת גודל

**למה זה שימושי:**
מחלק הודעות ארוכות לחתיכות תוך שמירה על שורות שלמות (לא חותך באמצע שורה). מתאים להודעות טלגרם.

```python
TG_MAX_MESSAGE = 4000

def split_by_lines(text: str, max_len: int = TG_MAX_MESSAGE) -> list[str]:
    """מפצל טקסט לחתיכות תוך שמירה על שורות שלמות."""
    text = text or ""
    hard_cap = min(max_len, TG_MAX_MESSAGE - 100)

    if len(text) <= hard_cap:
        return [text]

    lines = text.splitlines()
    chunks = []
    current = []
    current_len = 0

    for line in lines:
        line_len = len(line) + (1 if current else 0)  # +1 לשורה חדשה

        if current_len + line_len > hard_cap and current:
            # שומרים את החתיכה הנוכחית ומתחילים חדשה
            chunks.append("\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += line_len

    # שארית
    if current:
        chunks.append("\n".join(current))

    return chunks if chunks else [""]

# שימוש:
async def send_long_message(update, text):
    for chunk in split_by_lines(text):
        await update.message.reply_text(chunk)
```

---

## 46. שינוי גודל חלון PTY דרך WebSocket

**למה זה שימושי:**
מאפשר לטרמינל להתאים לגודל החלון של המשתמש. חשוב ל-TUI applications כמו vim, htop.

```python
import struct
import fcntl
import termios

def resize_pty(master_fd: int, rows: int, cols: int):
    """משנה גודל חלון PTY."""
    try:
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
    except Exception:
        pass

# שימוש ב-WebSocket handler:
@sock.route("/ws/terminal")
def ws_terminal(ws):
    session = create_pty_session(user_id)

    while True:
        msg = ws.receive()
        if not msg:
            break

        data = json.loads(msg)

        if data.get("type") == "resize":
            resize_pty(
                session["master_fd"],
                rows=data.get("rows", 24),
                cols=data.get("cols", 80)
            )
        elif data.get("type") == "input":
            os.write(session["master_fd"], data["data"].encode())
```

---

## 47. כפתור WebApp עם קישור דינמי

**למה זה שימושי:**
יוצר כפתור שפותח את ה-WebApp רק אם הוא מוגדר. מציג הוראות אם לא.

```python
import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

async def webapp_cmd(update, context):
    """פותח את ה-WebApp או מציג הוראות הגדרה."""
    webapp_url = os.getenv("WEBAPP_URL", "")

    if not webapp_url:
        return await update.message.reply_text(
            "❗ Web App לא מוגדר.\n\n"
            "כדי להפעיל:\n"
            "1. הרץ את webapp_server.py\n"
            "2. קבע WEBAPP_URL (למשל: https://your-domain.com)\n"
            "3. הגדר את ה-WebApp ב-@BotFather"
        )

    await update.message.reply_text(
        "🖥️ לחץ על הכפתור לפתיחת הטרמינל:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "פתח Web App 🚀",
                web_app=WebAppInfo(url=webapp_url)
            )]
        ])
    )
```

---

## 48. אימות WebSocket עם Telegram Init Data

**למה זה שימושי:**
מאמת משתמש ב-WebSocket connection לפני מתן גישה לטרמינל. שומר על אבטחה גם בחיבורי WS.

```python
import json

def validate_ws_auth(ws, bot_token: str, owner_ids: set) -> int | None:
    """מאמת WebSocket connection. מחזיר user_id או None."""
    try:
        # ממתין להודעת auth (ראשונה)
        auth_msg = ws.receive(timeout=5)
        if not auth_msg:
            return None

        auth_data = json.loads(auth_msg)
        if auth_data.get("type") != "auth":
            return None

        init_data = auth_data.get("init_data", "")
        data = validate_telegram_webapp_data(init_data, bot_token)

        if not data:
            return None

        user = data.get("user", {})
        user_id = user.get("id", 0)

        if user_id not in owner_ids:
            return None

        return user_id
    except Exception:
        return None

# שימוש:
@sock.route("/ws/terminal")
def ws_terminal(ws):
    user_id = validate_ws_auth(ws, BOT_TOKEN, OWNER_IDS)

    if user_id is None:
        ws.send(json.dumps({
            "type": "error",
            "message": "Authentication failed"
        }))
        return

    # ... המשך הלוגיקה
```
