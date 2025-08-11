import os, shlex, subprocess, tempfile, textwrap, time, socket, traceback, atexit, signal, uuid

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import NetworkError, TimedOut

# ========== הגדרות ==========
OWNER_ID = int(os.getenv("OWNER_ID", "6865105071"))  # אפשר להגדיר ב-ENV
TIMEOUT = int(os.getenv("CMD_TIMEOUT", "8"))
MAX_OUTPUT = int(os.getenv("MAX_OUTPUT", "3500"))
ALLOWED_CMDS = set((os.getenv("ALLOWED_CMDS") or
    "echo,date,uname,uptime,ls,pwd,whoami,df,free,id,ps"
).split(","))

# ========== נעילה כפולה ==========
LOCK_FILE = os.getenv("LOCK_FILE", "/tmp/bot_terminal.lock")
LOCK_KEY  = os.getenv("LOCK_KEY", "terminal-bot-lock")
LOCK_TTL  = int(os.getenv("LOCK_TTL", "90"))
INSTANCE_ID = str(uuid.uuid4())

_mongo_client = None
_mongo_lock_coll = None
_file_fd = None

def acquire_file_lock():
    """נעילת קובץ אטומית; אם קיים – יוצאים בשקט."""
    global _file_fd
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        _file_fd = os.open(LOCK_FILE, flags, 0o644)
        os.write(_file_fd, f"{os.getpid()}\n".encode())
        return True
    except FileExistsError:
        return False

def release_file_lock():
    global _file_fd
    try:
        if _file_fd is not None:
            os.close(_file_fd)
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception:
        pass

def acquire_mongo_lock():
    """
    נעילת MongoDB בעזרת unique index + TTL heartbeat.
    אם אין MONGO_URI – נחשב כהצליח (נשתמש רק בקובץ).
    """
    global _mongo_client, _mongo_lock_coll
    MONGO_URI = os.getenv("MONGO_URI")
    if not MONGO_URI:
        return True  # אין Mongo – הסתמכות על נעילת קובץ בלבד

    from pymongo import MongoClient, errors
    _mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=4000)
    dbname = os.getenv("MONGO_DB", "bot_terminal")
    coll = _mongo_client[dbname]["locks"]
    _mongo_lock_coll = coll

    try:
        coll.create_index("key", unique=True)
        coll.create_index("ts", expireAfterSeconds=LOCK_TTL)
    except Exception:
        pass

    doc = {"key": LOCK_KEY, "instance": INSTANCE_ID, "ts": time.time()}
    try:
        coll.insert_one(doc)
        return True
    except errors.DuplicateKeyError:
        existing = coll.find_one({"key": LOCK_KEY}) or {}
        old_ts = float(existing.get("ts", 0))
        if time.time() - old_ts > LOCK_TTL + 10:
            res = coll.find_one_and_update(
                {"key": LOCK_KEY, "ts": old_ts},
                {"$set": {"instance": INSTANCE_ID, "ts": time.time()}}
            )
            return res is not None
        return False

def heartbeat_mongo():
    if not _mongo_lock_coll:
        return
    try:
        _mongo_lock_coll.update_one(
            {"key": LOCK_KEY},
            {"$set": {"ts": time.time(), "instance": INSTANCE_ID}},
            upsert=True
        )
    except Exception:
        pass

def release_mongo_lock():
    try:
        if _mongo_lock_coll:
            _mongo_lock_coll.delete_one({"key": LOCK_KEY, "instance": INSTANCE_ID})
    except Exception:
        pass
    try:
        if _mongo_client:
            _mongo_client.close()
    except Exception:
        pass

def setup_locks_or_exit():
    if not acquire_file_lock():
        print("🔒 File lock present; exiting (another instance likely running).")
        raise SystemExit(0)

    if not acquire_mongo_lock():
        print("🔒 Mongo lock present; exiting (another instance likely running).")
        release_file_lock()
        raise SystemExit(0)

    def _cleanup(*_):
        release_mongo_lock()
        release_file_lock()
        raise SystemExit(0)

    atexit.register(_cleanup)
    signal.signal(signal.SIGINT, _cleanup)
    signal.signal(signal.SIGTERM, _cleanup)

    # Heartbeat רק אם Mongo פעיל
    if _mongo_lock_coll:
        import threading
        def _hb():
            while True:
                heartbeat_mongo()
                time.sleep(20)
        threading.Thread(target=_hb, daemon=True).start()

# ========== עזר לבוט ==========
def allowed(update: Update) -> bool:
    return update.effective_user and update.effective_user.id == OWNER_ID

def truncate(s: str) -> str:
    s = (s or "").strip() or "(no output)"
    return s if len(s) <= MAX_OUTPUT else s[:MAX_OUTPUT] + "\n…(truncated)"

async def notify_owner(app: Application, message: str):
    try:
        await app.bot.send_message(chat_id=OWNER_ID, text=f"⚠️ התראת שגיאה:\n{message}")
    except Exception:
        pass

# ========== פקודות ==========
async def start(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return await update.message.reply_text("⛔ אין הרשאה.")
    await update.message.reply_text(
        "פקודות:\n"
        "/sh <פקודה> – פקודת shell מותרת\n"
        "/py <קוד> – קוד Python\n"
        "/health – בדיקת חיבור\n"
        "/restart – הפעלה מחדש"
    )

async def sh_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return await update.message.reply_text("⛔ אין הרשאה.")
    cmdline = update.message.text.partition(" ")[2].strip()
    if not cmdline:
        return await update.message.reply_text("שימוש: /sh <פקודה>")
    parts = shlex.split(cmdline)
    if not parts:
        return await update.message.reply_text("❗ תן פקודה אחרי /sh")
    if parts[0] not in ALLOWED_CMDS:
        return await update.message.reply_text(
            f"❗ '{parts[0]}' לא מאושר. מותר: {', '.join(sorted(ALLOWED_CMDS))}"
        )
    try:
        proc = subprocess.run(parts, capture_output=True, text=True, timeout=TIMEOUT)
        out = proc.stdout or ""
        err = proc.stderr
        resp = f"$ {' '.join(parts)}\n\n{out}"
        if err:
            resp += "\nERR:\n" + err
        await update.message.reply_text(truncate(resp))
    except subprocess.TimeoutExpired:
        await update.message.reply_text("⏱️ נגמר הזמן (timeout).")
    except Exception as e:
        await notify_owner(_.application, traceback.format_exc())
        await update.message.reply_text(f"❌ שגיאה: {e}")

async def py_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return await update.message.reply_text("⛔ אין הרשאה.")
    code = update.message.text.partition(" ")[2]
    if not code.strip():
        return await update.message.reply_text("שימוש: /py <קוד פייתון>")
    cleaned = textwrap.dedent(code).strip() + "\n"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tf:
            tf.write(cleaned); tmp_path = tf.name
        proc = subprocess.run(
            ["python", "-I", "-S", tmp_path],
            capture_output=True, text=True, timeout=TIMEOUT,
            env={"PYTHONUNBUFFERED": "1"}
        )
        out = proc.stdout or "(no output)"
        if proc.stderr:
            out += "\nERR:\n" + proc.stderr
        await update.message.reply_text(truncate(out))
    except Exception as e:
        await notify_owner(_.application, traceback.format_exc())
        await update.message.reply_text(f"❌ שגיאה: {e}")
    finally:
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass

async def health_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    try:
        socket.create_connection(("api.telegram.org", 443), timeout=3)
        await update.message.reply_text("✅ יש חיבור לאינטרנט ולטלגרם")
    except OSError as e:
        await update.message.reply_text(f"❌ אין חיבור: {e}")

async def restart_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.message.reply_text("🔄 מבצע הפעלה מחדש…")
    time.sleep(1)
    os._exit(0)  # Render/Worker ירים מחדש

# ========== main ==========
def main():
    # נועל לפני שמתחילים Polling
    setup_locks_or_exit()

    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("Please set BOT_TOKEN env var")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("sh", sh_cmd))
    app.add_handler(CommandHandler("py", py_cmd))
    app.add_handler(CommandHandler("health", health_cmd))
    app.add_handler(CommandHandler("restart", restart_cmd))

    while True:
        try:
            app.run_polling(drop_pending_updates=True, poll_interval=1.5, timeout=10)
        except (NetworkError, TimedOut) as e:
            print(f"⚠️ בעיית רשת: {e}. מנסה שוב בעוד 10 שנ׳…")
            time.sleep(10)
        except Exception:
            try:
                app.bot.send_message(chat_id=OWNER_ID, text=f"⚠️ קריסה:\n{traceback.format_exc()}")
            except Exception:
                pass
            time.sleep(10)

if __name__ == "__main__":
    main()
