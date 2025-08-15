import os, shlex, subprocess, tempfile, textwrap, time, socket, asyncio
import zipfile
from activity_reporter import create_reporter

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import NetworkError, TimedOut, Conflict, BadRequest
import sys
import json

# ==== תצורה ====
OWNER_ID   = int(os.getenv("OWNER_ID", "6865105071"))
TIMEOUT    = int(os.getenv("CMD_TIMEOUT", "60"))
MAX_OUTPUT = int(os.getenv("MAX_OUTPUT", "10000"))
TG_MAX_MESSAGE = int(os.getenv("TG_MAX_MESSAGE", "4000"))
RESTART_NOTIFY_PATH = os.getenv("RESTART_NOTIFY_PATH", "/tmp/bot_restart_notify.json")
ALLOWED_CMDS = set((os.getenv("ALLOWED_CMDS") or
    "ls,pwd,cp,mv,rm,mkdir,rmdir,touch,ln,stat,du,df,find,realpath,readlink,file,tar,cat,tac,head,tail,cut,sort,uniq,wc,sed,awk,tr,paste,join,nl,rev,grep,curl,wget,ping,traceroute,dig,host,nslookup,ip,ss,nc,netstat,uname,uptime,date,whoami,id,who,w,hostname,lscpu,lsblk,free,nproc,ps,top,echo,env,git,python,python3,pip,pip3,poetry,uv,pytest,go,rustc,cargo,node,npm,npx,tsc,deno,zip,unzip,7z,tar,tee,yes,xargs,printf,kill,killall,bash,sh,chmod,chown,chgrp,df,du,make,gcc,g++,javac,java,ssh,scp"
).split(","))
ALLOW_ALL_COMMANDS = os.getenv("ALLOW_ALL_COMMANDS", "").lower() in ("1","true","yes","on")
SHELL_EXECUTABLE = os.getenv("SHELL_EXECUTABLE") or ("/bin/bash" if os.path.exists("/bin/bash") else None)

# ==== Reporter ====
reporter = create_reporter(
    mongodb_uri="mongodb+srv://mumin:M43M2TFgLfGvhBwY@muminai.tm6x81b.mongodb.net/?retryWrites=true&w=majority&appName=muminAI",
    service_id="srv-d2d0dnc9c44c73b5d6q0",
    service_name="MyTerminal66Bot"
)

# ==== עזר ====
def allowed(u: Update) -> bool:
    return u.effective_user and u.effective_user.id == OWNER_ID

def truncate(s: str) -> str:
    s = (s or "").strip() or "(no output)"
    return s if len(s) <= MAX_OUTPUT else s[:MAX_OUTPUT] + "\n…(truncated)"

async def send_output(update: Update, text: str, filename: str):
    """Send text safely. If it exceeds Telegram message limit or MAX_OUTPUT, send as a file."""
    text = (text or "").strip() or "(no output)"
    if len(text) <= min(MAX_OUTPUT, TG_MAX_MESSAGE):
        try:
            await update.message.reply_text(text)
            return
        except BadRequest:
            # Fall back to document if Telegram rejects the message (e.g., too long)
            pass
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf:
            tf.write(text)
            tmp_path = tf.name
        with open(tmp_path, "rb") as fh:
            await update.message.reply_document(fh, filename=filename, caption="📄 פלט גדול נשלח כקובץ")
    finally:
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except:
            pass

# ---- סשן טרמינל ----
sessions = {}

def _chat_id(update: Update) -> int:
    return (update.effective_chat.id if update.effective_chat else 0)

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

def _resolve_path(base_cwd: str, target: str) -> str:
    if target == "-":
        return base_cwd
    p = os.path.expanduser(target)
    if not os.path.isabs(p):
        p = os.path.abspath(os.path.join(base_cwd, p))
    return p

def handle_builtins(sess, cmdline: str):
    """Handle cd/export/unset builtins if the line is a single simple builtin.
    Returns a response string if handled, or None if not a builtin.
    """
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
        # cd [dir]
        target = parts[1] if len(parts) > 1 else (sess["env"].get("HOME") or os.path.expanduser("~"))
        new_path = _resolve_path(sess["cwd"], target)
        if os.path.isdir(new_path):
            sess["cwd"] = new_path
            return f"📁 cwd: {new_path}"
        return f"❌ תיקייה לא נמצאה: {target}"

    if cmd == "export":
        # export A=1 B=2
        if len(parts) == 1:
            # show all
            return "\n".join([f"PWD={sess['cwd']}"] + [f"{k}={v}" for k,v in sorted(sess["env"].items())])
        out_lines = []
        for tok in parts[1:]:
            if "=" not in tok:
                val = sess["env"].get(tok)
                out_lines.append(f"{tok}={val if val is not None else ''}")
                continue
            k, v = tok.split("=", 1)
            sess["env"][k] = v
            out_lines.append(f"set {k}={v}")
        return "\n".join(out_lines)

    if cmd == "unset":
        out_lines = []
        for tok in parts[1:]:
            if tok in sess["env"]:
                sess["env"].pop(tok, None)
                out_lines.append(f"unset {tok}")
            else:
                out_lines.append(f"{tok} לא מוגדר")
        return "\n".join(out_lines) if out_lines else "❗ שימוש: unset VAR [VAR2 ...]"

    return None

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
                    except:
                        pass
    except Exception:
        # אל נכשיל את ההרצה אם יש בעיית קובץ/הרשאות
        pass

# ==== פקודות ====
async def start(update: Update, _: ContextTypes.DEFAULT_TYPE):
    reporter.report_activity(update.effective_user.id)
    if not allowed(update): return
    await update.message.reply_text("/sh <פקודה>\n/py <קוד>\n/health\n/restart\n/env\n/reset\n(תמיכה ב-cd/export/unset, שמירת cwd/env לסשן)")

async def sh_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    reporter.report_activity(update.effective_user.id)
    if not allowed(update): return
    cmdline = update.message.text.partition(" ")[2].strip()
    if not cmdline: return await update.message.reply_text("שימוש: /sh <פקודה>")

    sess = get_session(update)

    # Builtins: cd/export/unset
    builtin_resp = handle_builtins(sess, cmdline)
    if builtin_resp is not None:
        return await send_output(update, builtin_resp, "builtin.txt")

    # Detect chaining; if multiple commands are present, skip ALLOWED_CMDS validation
    is_multi = (";" in cmdline) or ("&&" in cmdline) or ("\n" in cmdline) or ("|" in cmdline)

    if not is_multi and not ALLOW_ALL_COMMANDS:
        try:
            parts = shlex.split(cmdline, posix=True)
        except ValueError:
            return await update.message.reply_text("❗ שגיאת פרסינג")
        if not parts:
            return await update.message.reply_text("❗ אין פקודה")
        cmd_name = parts[0]
        if cmd_name not in ALLOWED_CMDS:
            return await update.message.reply_text(f"❗ פקודה לא מאושרת: {cmd_name}")

    # Execute the full line in a shell, so pipes/; && work as expected
    try:
        p = subprocess.run(
            cmdline,
            shell=True,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            cwd=sess["cwd"],
            env=sess["env"],
            executable=SHELL_EXECUTABLE
        )
        out = p.stdout or ""
        err = p.stderr or ""
        resp = f"$ {cmdline}\n\n{out}"
        if err:
            resp += "\nERR:\n" + err
        resp = resp.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        resp = f"$ {cmdline}\n\n⏱️ Timeout"

    await send_output(update, resp, "output.txt")

async def py_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    reporter.report_activity(update.effective_user.id)
    if not allowed(update): return
    code = update.message.text.partition(" ")[2]
    if not code.strip(): return await update.message.reply_text("שימוש: /py <קוד>")
    cleaned = textwrap.dedent(code).strip() + "\n"
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tf:
            tf.write(cleaned); tmp = tf.name
        # Prefer the current interpreter; fallback to 'python3'/'python'
        interpreter = sys.executable or "python3"
        try_cmd = [interpreter, "-I", "-S", tmp]
        try:
            p = subprocess.run(try_cmd,
                               capture_output=True, text=True, timeout=TIMEOUT,
                               env={"PYTHONUNBUFFERED":"1"})
        except FileNotFoundError:
            p = subprocess.run(["python", "-I", "-S", tmp],
                            capture_output=True, text=True, timeout=TIMEOUT,
                            env={"PYTHONUNBUFFERED":"1"})
        out = p.stdout or "(no output)"
        if p.stderr: out += "\nERR:\n" + p.stderr
        await send_output(update, out, "py-output.txt")
    finally:
        try:
            if tmp and os.path.exists(tmp): os.remove(tmp)
        except: pass

async def env_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    reporter.report_activity(update.effective_user.id)
    if not allowed(update): return
    sess = get_session(update)
    lines = [f"PWD={sess['cwd']}"] + [f"{k}={v}" for k,v in sorted(sess["env"].items())]
    await send_output(update, "\n".join(lines), "env.txt")

async def reset_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    reporter.report_activity(update.effective_user.id)
    if not allowed(update): return
    chat_id = _chat_id(update)
    sessions.pop(chat_id, None)
    await update.message.reply_text("♻️ הסשן אופס (cwd/env הוחזרו לברירת מחדל)")

async def health_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    reporter.report_activity(update.effective_user.id)
    if not allowed(update): return
    try:
        socket.create_connection(("api.telegram.org", 443), timeout=3).close()
        await update.message.reply_text("✅ OK")
    except OSError:
        await update.message.reply_text("❌ אין חיבור")

async def restart_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    reporter.report_activity(update.effective_user.id)
    if not allowed(update): return
    try:
        # נשמור את הצ'אט כדי להודיע אחרי עלייה
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
        app.add_handler(CommandHandler("start",   start))
        app.add_handler(CommandHandler("sh",      sh_cmd))
        app.add_handler(CommandHandler("py",      py_cmd))
        app.add_handler(CommandHandler("env",     env_cmd))
        app.add_handler(CommandHandler("reset",   reset_cmd))
        app.add_handler(CommandHandler("health",  health_cmd))
        app.add_handler(CommandHandler("restart", restart_cmd))

        # run_polling מבצע initialize/start ופותח polling בצורה בטוחה
        try:
            app.run_polling(drop_pending_updates=True, poll_interval=1.5, timeout=10)
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
