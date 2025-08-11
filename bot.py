import os, shlex, subprocess, tempfile, textwrap, time, socket, asyncio, atexit, signal

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import NetworkError, TimedOut, Conflict

# ===== תצורה =====
OWNER_ID   = int(os.getenv("OWNER_ID", "6865105071"))
TIMEOUT    = int(os.getenv("CMD_TIMEOUT", "8"))
MAX_OUTPUT = int(os.getenv("MAX_OUTPUT", "3500"))
ALLOWED_CMDS = set((os.getenv("ALLOWED_CMDS") or
    "echo,date,uname,uptime,ls,pwd,whoami,df,free,id,ps"
).split(","))

# ===== נעילת קובץ פשוטה כדי למנוע כפילויות (אופציונלי) =====
LOCK_FILE = os.getenv("LOCK_FILE", "/tmp/bot_terminal.lock")
_file_fd = None
def acquire_file_lock():
    global _file_fd
    try:
        _file_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
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

# ===== כלים =====
def allowed(u: Update) -> bool:
    return u.effective_user and u.effective_user.id == OWNER_ID

def truncate(s: str) -> str:
    s = (s or "").strip() or "(no output)"
    return s if len(s) <= MAX_OUTPUT else s[:MAX_OUTPUT] + "\n…(truncated)"

# ===== פקודות =====
async def start(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    await update.message.reply_text(
        "/sh <פקודה>\n/py <קוד>\n/health\n/restart"
    )

async def sh_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    cmdline = update.message.text.partition(" ")[2].strip()
    if not cmdline:
        return await update.message.reply_text("שימוש: /sh <פקודה>")
    parts = shlex.split(cmdline)
    if not parts:
        return await update.message.reply_text("❗ אין פקודה")
    if parts[0] not in ALLOWED_CMDS:
        return await update.message.reply_text(f"❗ '{parts[0]}' לא מאושר")
    try:
        proc = subprocess.run(parts, capture_output=True, text=True, timeout=TIMEOUT)
        out = (proc.stdout or "")
        err = proc.stderr
        resp = f"$ {' '.join(parts)}\n\n{out}"
        if err: resp += "\nERR:\n" + err
        await update.message.reply_text(truncate(resp))
    except subprocess.TimeoutExpired:
        await update.message.reply_text("⏱️ Timeout")

async def py_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    code = update.message.text.partition(" ")[2]
    if not code.strip():
        return await update.message.reply_text("שימוש: /py <קוד>")
    cleaned = textwrap.dedent(code).strip() + "\n"
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tf:
            tf.write(cleaned); tmp = tf.name
        proc = subprocess.run(["python", "-I", "-S", tmp],
                              capture_output=True, text=True, timeout=TIMEOUT,
                              env={"PYTHONUNBUFFERED": "1"})
        out = proc.stdout or "(no output)"
        if proc.stderr: out += "\nERR:\n" + proc.stderr
        await update.message.reply_text(truncate(out))
    finally:
        try:
            if tmp and os.path.exists(tmp): os.remove(tmp)
        except: pass

async def health_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    try:
        socket.create_connection(("api.telegram.org", 443), timeout=3).close()
        await update.message.reply_text("✅ OK")
    except OSError:
        await update.message.reply_text("❌ אין חיבור")

async def restart_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    await update.message.reply_text("🔄 Restart…")
    time.sleep(1)
    os._exit(0)

# ===== main =====
def main():
    # לוגים מינימליים
    import logging
    for name in ("telegram", "telegram.ext", "httpx"):
        logging.getLogger(name).setLevel(logging.ERROR)
    logging.basicConfig(level=logging.ERROR)

    # נעילה – אם יש כבר אינסטנס: יוצאים בשקט (בלי ספאם)
    if not acquire_file_lock():
        return
    atexit.register(release_file_lock)
    signal.signal(signal.SIGINT,  lambda *_: (release_file_lock(), os._exit(0)))
    signal.signal(signal.SIGTERM, lambda *_: (release_file_lock(), os._exit(0)))

    token = os.getenv("BOT_TOKEN")
    if not token:
        return  # אין טוקן – יוצאים בשקט

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("sh",      sh_cmd))
    app.add_handler(CommandHandler("py",      py_cmd))
    app.add_handler(CommandHandler("health",  health_cmd))
    app.add_handler(CommandHandler("restart", restart_cmd))

    # מבטלים webhook לפני polling כדי להימנע מקונפליקטים
    async def _preflight():
        try: await app.bot.delete_webhook(drop_pending_updates=True)
        except: pass
    asyncio.run(_preflight())

    # לולאת ריצה – שקטה: Conflict → יציאה; NetworkError → המתנה קצרה וחזרה
    while True:
        try:
            app.run_polling(drop_pending_updates=True, poll_interval=1.5, timeout=10)
        except Conflict:
            # יש אינסטנס אחר → יוצאים נקי בלי רעש
            break
        except (NetworkError, TimedOut):
            time.sleep(8)  # רשת חלשה – נחכה וננסה שוב
            continue
        except Exception:
            # כל דבר אחר – המתנה קצרה וניסיון שוב, בלי לוגים
            time.sleep(8)
            continue

if __name__ == "__main__":
    main()
