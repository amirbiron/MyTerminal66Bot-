import os, shlex, subprocess, tempfile, textwrap, time, socket, traceback
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import NetworkError, TimedOut

# ===== הגדרות =====
OWNER_ID = 6865105071  # ה־Telegram ID שלך
TIMEOUT = 8
MAX_OUTPUT = 3500
ALLOWED_CMDS = {
    "echo", "date", "uname", "uptime", "ls", "pwd", "whoami", "df", "free", "id", "ps"
}

# פונקציות עזר
def allowed(update: Update) -> bool:
    return update.effective_user and update.effective_user.id == OWNER_ID

def truncate(s: str) -> str:
    s = (s or "").strip() or "(no output)"
    return s if len(s) <= MAX_OUTPUT else s[:MAX_OUTPUT] + "\n…(truncated)"

async def notify_owner(app: Application, message: str):
    """שולח הודעה לבעלים במקרה של שגיאה"""
    try:
        await app.bot.send_message(chat_id=OWNER_ID, text=f"⚠️ התראת שגיאה:\n{message}")
    except:
        pass

# פקודות
async def start(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return await update.message.reply_text("⛔ אין הרשאה.")
    await update.message.reply_text(
        "פקודות זמינות:\n"
        "/sh <פקודה> – פקודת shell מותרת\n"
        "/py <קוד> – קוד Python\n"
        "/health – בדיקת חיבור\n"
        "/sysinfo – מידע על השרת\n"
        "/logs – לוגים אחרונים\n"
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
        return await update.message.reply_text(f"❗ '{parts[0]}' לא מאושר. מותר: {', '.join(sorted(ALLOWED_CMDS))}")
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
        tb = traceback.format_exc()
        await notify_owner(_.application, tb)
        await update.message.reply_text(f"❌ שגיאה: {e}")

async def py_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return await update.message.reply_text("⛔ אין הרשאה.")
    code = update.message.text.partition(" ")[2]
    if not code.strip():
        return await update.message.reply_text("שימוש: /py <קוד פייתון>")
    cleaned = textwrap.dedent(code).strip() + "\n"
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tf:
            tf.write(cleaned)
            tmp_path = tf.name
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
        tb = traceback.format_exc()
        await notify_owner(_.application, tb)
        await update.message.reply_text(f"❌ שגיאה: {e}")
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except:
            pass

async def health_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    try:
        socket.create_connection(("api.telegram.org", 443), timeout=3)
        await update.message.reply_text("✅ יש חיבור לאינטרנט ולטלגרם")
    except OSError as e:
        await update.message.reply_text(f"❌ אין חיבור: {e}")

async def sysinfo_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    try:
        proc = subprocess.run(["uname", "-a"], capture_output=True, text=True)
        info = proc.stdout.strip()
        await update.message.reply_text(f"🖥️ מידע מערכת:\n{info}")
    except Exception as e:
        await update.message.reply_text(f"❌ שגיאה: {e}")

async def logs_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.message.reply_text("📌 הלוגים זמינים ב־Render Dashboard (לשונית Logs)")

async def restart_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.message.reply_text("🔄 מבצע הפעלה מחדש…")
    time.sleep(1)
    os._exit(0)  # Render ירים את הבוט מחדש

# הפעלת הבוט
def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("Please set BOT_TOKEN env var")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("sh", sh_cmd))
    app.add_handler(CommandHandler("py", py_cmd))
    app.add_handler(CommandHandler("health", health_cmd))
    app.add_handler(CommandHandler("sysinfo", sysinfo_cmd))
    app.add_handler(CommandHandler("logs", logs_cmd))
    app.add_handler(CommandHandler("restart", restart_cmd))

    while True:
        try:
            app.run_polling(drop_pending_updates=True, poll_interval=1.5, timeout=10)
        except (NetworkError, TimedOut) as e:
            print(f"⚠️ בעיית רשת: {e}. מנסה שוב בעוד 10 שנ׳…")
            time.sleep(10)
        except Exception as e:
            tb = traceback.format_exc()
            try:
                app.bot.send_message(chat_id=OWNER_ID, text=f"⚠️ קריסה:\n{tb}")
            except:
                pass
            time.sleep(10)

if __name__ == "__main__":
    main()
