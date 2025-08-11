# bot.py (Render-ready)
# pip install -r requirements.txt
import os, shlex, subprocess, tempfile, textwrap, time
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import NetworkError, TimedOut

# ===== הגדרות =====
ALLOWED_USER_IDS = {6865105071}  # רק אתה
TIMEOUT = 8          # שניות לכל הרצה
MAX_OUTPUT = 3500    # חיתוך פלט כדי לא לעבור מגבלת טלגרם
ALLOWED_CMDS = {     # פקודות shell מותרות
    "echo", "date", "uname", "uptime", "ls", "pwd", "whoami", "df", "free", "id", "ps"
}

def allowed(update: Update) -> bool:
    u = update.effective_user.id if update.effective_user else None
    return u in ALLOWED_USER_IDS

def truncate(s: str) -> str:
    s = (s or "").strip() or "(no output)"
    return s if len(s) <= MAX_OUTPUT else s[:MAX_OUTPUT] + "\n…(truncated)"

async def start(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        await update.message.reply_text("⛔ אין הרשאה.")
        return
    await update.message.reply_text(
        "זמין:\n"
        "/sh <פקודה>  – מריץ פקודת shell מותרת\n"
        "/py <קוד>     – מריץ קוד Python בסאב־פרוסס"
    )

async def sh_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        await update.message.reply_text("⛔ אין הרשאה.")
        return
    cmdline = update.message.text.partition(" ")[2].strip()
    if not cmdline:
        await update.message.reply_text("שימוש: /sh <פקודה>")
        return

    parts = shlex.split(cmdline)
    if not parts:
        await update.message.reply_text("❗ תן פקודה אחרי /sh")
        return
    if parts[0] not in ALLOWED_CMDS:
        await update.message.reply_text(
            f"❗ פקודה לא מאושרת: {parts[0]}\nמותר: {', '.join(sorted(ALLOWED_CMDS))}"
        )
        return

    try:
        proc = subprocess.run(
            parts,
            capture_output=True,
            text=True,
            timeout=TIMEOUT
        )
        out = (proc.stdout or "")
        err = proc.stderr
        resp = f"$ {' '.join(parts)}\n\n{out}"
        if err:
            resp += ("\nERR:\n" + err)
        await update.message.reply_text(truncate(resp))
    except subprocess.TimeoutExpired:
        await update.message.reply_text("⏱️ נגמר הזמן (timeout).")
    except Exception as e:
        await update.message.reply_text(f"❌ שגיאה: {e}")

async def py_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        await update.message.reply_text("⛔ אין הרשאה.")
        return
    code = update.message.text.partition(" ")[2]
    if not code.strip():
        await update.message.reply_text("שימוש: /py <קוד פייתון>")
        return

    cleaned = textwrap.dedent(code).strip() + "\n"
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tf:
            tf.write(cleaned)
            tmp_path = tf.name

        proc = subprocess.run(
            ["python", "-I", "-S", tmp_path],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            env={"PYTHONUNBUFFERED": "1"}
        )
        out = (proc.stdout or "")
        err = proc.stderr
        resp = out if out else "(no output)"
        if err:
            resp += ("\nERR:\n" + err)
        await update.message.reply_text(truncate(resp))
    except subprocess.TimeoutExpired:
        await update.message.reply_text("⏱️ נגמר הזמן (timeout).")
    except Exception as e:
        await update.message.reply_text(f"❌ שגיאה: {e}")
    finally:
        try:
            if 'tmp_path' in locals() and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except:
            pass

def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("Please set BOT_TOKEN env var")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("sh", sh_cmd))
    app.add_handler(CommandHandler("py", py_cmd))

    # לולאת ריסטארט במקרה של נפילות רשת
    while True:
        try:
            app.run_polling(drop_pending_updates=True, poll_interval=1.5, timeout=10)
        except (NetworkError, TimedOut) as e:
            print(f"⚠️ בעיית רשת: {e}. מנסה שוב בעוד 10 שנ׳…")
            time.sleep(10)
        except Exception as e:
            print(f"❌ שגיאה לא צפויה: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
