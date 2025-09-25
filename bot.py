#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import shlex
import json
import time
import socket
import asyncio
import tempfile
import textwrap
import subprocess
import zipfile  # נשאר אם תרצה להשתמש בהמשך
import io
import traceback
import contextlib
import unicodedata
import re

from activity_reporter import create_reporter
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import NetworkError, TimedOut, Conflict, BadRequest

# ==== תצורה ====
OWNER_ID = int(os.getenv("OWNER_ID", "6865105071"))
TIMEOUT = int(os.getenv("CMD_TIMEOUT", "60"))
PIP_TIMEOUT = int(os.getenv("PIP_TIMEOUT", "120"))
MAX_OUTPUT = int(os.getenv("MAX_OUTPUT", "10000"))
TG_MAX_MESSAGE = int(os.getenv("TG_MAX_MESSAGE", "4000"))
RESTART_NOTIFY_PATH = os.getenv("RESTART_NOTIFY_PATH", "/tmp/bot_restart_notify.json")

def _parse_cmds_string(value: str) -> set:
    """Parses comma/newline separated command names into a set, trimming blanks."""
    if not value:
        return set()
    tokens = []
    # Support both comma and newline separated formats
    for part in value.replace("\r", "").replace("\n", ",").split(","):
        tok = part.strip()
        if tok:
            tokens.append(tok)
    return set(tokens)


DEFAULT_ALLOWED_CMDS = _parse_cmds_string(
    os.getenv("ALLOWED_CMDS")
    or "ls,pwd,cp,mv,rm,mkdir,rmdir,touch,ln,stat,du,df,find,realpath,readlink,file,tar,cat,tac,head,tail,cut,sort,uniq,wc,sed,awk,tr,paste,join,nl,rev,grep,curl,wget,ping,traceroute,dig,host,nslookup,ip,ss,nc,netstat,uname,uptime,date,whoami,id,who,w,hostname,lscpu,lsblk,free,nproc,ps,top,echo,env,git,python,python3,pip,pip3,poetry,uv,pytest,go,rustc,cargo,node,npm,npx,tsc,deno,zip,unzip,7z,tar,tee,yes,xargs,printf,kill,killall,bash,sh,chmod,chown,chgrp,df,du,make,gcc,g++,javac,java,ssh,scp"
)

# In-memory allowlist. Will be overridden from file if present.
ALLOWED_CMDS = set(DEFAULT_ALLOWED_CMDS)

ALLOW_ALL_COMMANDS = os.getenv("ALLOW_ALL_COMMANDS", "").lower() in ("1", "true", "yes", "on")
SHELL_EXECUTABLE = os.getenv("SHELL_EXECUTABLE") or ("/bin/bash" if os.path.exists("/bin/bash") else None)
ALLOWED_CMDS_FILE = os.getenv("ALLOWED_CMDS_FILE", "allowed_cmds.txt")


def _serialize_cmds(cmds: set) -> str:
    # Persist one-per-line for readability
    return "\n".join(sorted(cmds))


def load_allowed_cmds_from_file() -> None:
    """Load allowed commands from file if it exists; otherwise keep current (env/default)."""
    global ALLOWED_CMDS
    try:
        if os.path.exists(ALLOWED_CMDS_FILE):
            with open(ALLOWED_CMDS_FILE, "r", encoding="utf-8") as fh:
                content = fh.read()
            parsed = _parse_cmds_string(content)
            # If file exists but empty, treat as empty allowlist
            ALLOWED_CMDS = set(parsed)
    except Exception:
        # If load fails, keep existing in-memory allowlist
        pass


def save_allowed_cmds_to_file() -> None:
    try:
        with open(ALLOWED_CMDS_FILE, "w", encoding="utf-8") as fh:
            fh.write(_serialize_cmds(ALLOWED_CMDS))
    except Exception:
        # Do not crash on persistence issues
        pass

# ==== Reporter ====
reporter = create_reporter(
    mongodb_uri="mongodb+srv://mumin:M43M2TFgLfGvhBwY@muminai.tm6x81b.mongodb.net/?retryWrites=true&w=majority&appName=muminAI",
    service_id="srv-d2d0dnc9c44c73b5d6q0",
    service_name="MyTerminal66Bot",
)

# ==== גלובלי לסשנים ====
sessions = {}

# ==== הקשר גלובלי לסשן פייתון מתמשך (לפי chat_id) ====
# מיפוי chat_id -> context dict לשמירת מצב פייתון לכל צ'אט בנפרד
PY_CONTEXT = {}


# ==== עזר ====
def allowed(u: Update) -> bool:
    return bool(u.effective_user and u.effective_user.id == OWNER_ID)


def truncate(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return "(no output)"
    if len(s) <= MAX_OUTPUT:
        return s
    return s[:MAX_OUTPUT] + f"\n\n…[truncated {len(s) - MAX_OUTPUT} chars]"


def normalize_code(text: str) -> str:
    """ניקוי תווים נסתרים, גרשיים חכמים, NBSP וכד'.
    - ממיר גרשיים חכמים לגרשיים רגילים
    - ממיר NBSP ותווים דומים לרווח רגיל
    - מנרמל יוניקוד ל-NFKC
    - מחליף \r\n ל-\n
    """
    if not text:
        return ""
    # נירמול יוניקוד כללי
    text = unicodedata.normalize("NFKC", text)
    # המרות גרשיים חכמים
    text = text.replace("“", '"').replace("”", '"').replace("„", '"')
    text = text.replace("‘", "'").replace("’", "'")
    # NBSP וקרובים
    text = text.replace("\u00A0", " ").replace("\u202F", " ")
    # סימני כיוון בלתי נראים
    text = text.replace("\u200E", "").replace("\u200F", "")
    # קו מפריד רך -> רגיל
    text = text.replace("\u00AD", "")
    # CRLF ל-LF
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


SAFE_PIP_NAME_RE = re.compile(r'^[a-zA-Z0-9_\-]+$')


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


def _resolve_path(base_cwd: str, target: str) -> str:
    if target == "-":
        return base_cwd
    p = os.path.expanduser(target)
    if not os.path.isabs(p):
        p = os.path.abspath(os.path.join(base_cwd, p))
    return p


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


def handle_builtins(sess, cmdline: str):
    """
    תומך בפקודות cd/export/unset אם הפקודה פשוטה (ללא ; || && | \n).
    אם טופל – מחזיר מחרוזת תגובה. אם לא – None.
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
        target = parts[1] if len(parts) > 1 else (sess["env"].get("HOME") or os.path.expanduser("~"))
        new_path = _resolve_path(sess["cwd"], target)
        if os.path.isdir(new_path):
            sess["cwd"] = new_path
            return f"📁 cwd: {new_path}"
        return f"❌ תיקייה לא נמצאה: {target}"

    if cmd == "export":
        # export A=1 B=2 ; export A ; export (לרשימת כל המשתנים)
        if len(parts) == 1:
            return "\n".join([f"PWD={sess['cwd']}"] + [f"{k}={v}" for k, v in sorted(sess["env"].items())])
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
        if len(parts) == 1:
            return "❗ שימוש: unset VAR [VAR2 ...]"
        out_lines = []
        for tok in parts[1:]:
            if tok in sess["env"]:
                sess["env"].pop(tok, None)
                out_lines.append(f"unset {tok}")
            else:
                out_lines.append(f"{tok} לא מוגדר")
        return "\n".join(out_lines)

    return None


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
    except Exception:
        # לא מפיל את הבוט אם יש בעיות הרשאות/קובץ
        pass


# ==== פקודות ====
async def start(update: Update, _: ContextTypes.DEFAULT_TYPE):
    reporter.report_activity(update.effective_user.id if update.effective_user else 0)
    if not allowed(update):
        return await update.message.reply_text("/sh <פקודת shell>\n/py <קוד פייתון>\n/health\n/restart\n/env\n/reset\n/allow,/deny,/list,/update (מנהלי הרשאות לבעלים בלבד)\n(תמיכה ב-cd/export/unset, ושמירת cwd/env לסשן)")


async def sh_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    reporter.report_activity(update.effective_user.id if update.effective_user else 0)
    if not allowed(update):
        return

    cmdline = update.message.text.partition(" ")[2].strip()
    cmdline = normalize_code(cmdline)
    if not cmdline:
        return await update.message.reply_text("שימוש: /sh <פקודה>")

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
    reporter.report_activity(update.effective_user.id if update.effective_user else 0)
    if not allowed(update):
        return await update.message.reply_text("❌ אין הרשאה")
    if not ALLOWED_CMDS:
        return await update.message.reply_text("(רשימה ריקה)")
    await update.message.reply_text(",".join(sorted(ALLOWED_CMDS)))


async def allow_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    reporter.report_activity(update.effective_user.id if update.effective_user else 0)
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
    reporter.report_activity(update.effective_user.id if update.effective_user else 0)
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
    reporter.report_activity(update.effective_user.id if update.effective_user else 0)
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
    reporter.report_activity(update.effective_user.id if update.effective_user else 0)
    if not allowed(update):
        return

    code = update.message.text.partition(" ")[2]
    if not code.strip():
        return await update.message.reply_text("שימוש: /py <קוד פייתון>")

    # ניקוי ופירמוט קוד
    cleaned = textwrap.dedent(code)
    cleaned = normalize_code(cleaned).strip("\n") + "\n"

    def _exec_in_context(src: str, chat_id: int):
        global PY_CONTEXT
        # אתחול ראשוני של הקשר ההרצה לצ'אט הנוכחי
        ctx = PY_CONTEXT.get(chat_id)
        if ctx is None:
            ctx = {"__builtins__": __builtins__}
            PY_CONTEXT[chat_id] = ctx
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
        out, err, tb_text = await asyncio.wait_for(asyncio.to_thread(_exec_in_context, cleaned, chat_id), timeout=TIMEOUT)

        # Attempt dynamic install on ModuleNotFoundError, up to 3 modules per run
        attempts = 0
        while tb_text and "ModuleNotFoundError" in tb_text and attempts < 3:
            m = re.search(r"ModuleNotFoundError: No module named '([^']+)'", tb_text)
            if not m:
                break
            missing_mod = m.group(1)
            if not SAFE_PIP_NAME_RE.match(missing_mod):
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

        parts = []
        if out.strip():
            parts.append(out.rstrip())
        if err.strip():
            parts.append("STDERR:\n" + err.rstrip())
        if tb_text and tb_text.strip():
            parts.append(tb_text.rstrip())
        resp = "\n".join(parts).strip() or "(no output)"
        await send_output(update, truncate(resp), "py-output.txt")
    except asyncio.TimeoutError:
        await send_output(update, "⏱️ Timeout", "py-output.txt")
    except Exception as e:
        await send_output(update, f"ERR:\n{e}", "py-output.txt")


async def env_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    reporter.report_activity(update.effective_user.id if update.effective_user else 0)
    if not allowed(update):
        return
    sess = get_session(update)
    lines = [f"PWD={sess['cwd']}"] + [f"{k}={v}" for k, v in sorted(sess["env"].items())]
    await send_output(update, "\n".join(lines), "env.txt")


async def reset_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    reporter.report_activity(update.effective_user.id if update.effective_user else 0)
    if not allowed(update):
        return
    chat_id = _chat_id(update)
    sessions.pop(chat_id, None)
    await update.message.reply_text("♻️ הסשן אופס (cwd/env הוחזרו לברירת מחדל)")


async def health_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    reporter.report_activity(update.effective_user.id if update.effective_user else 0)
    if not allowed(update):
        return
    try:
        socket.create_connection(("api.telegram.org", 443), timeout=3).close()
        await update.message.reply_text("✅ OK")
    except OSError:
        await update.message.reply_text("❌ אין חיבור")


async def restart_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    reporter.report_activity(update.effective_user.id if update.effective_user else 0)
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

        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("sh", sh_cmd))
        app.add_handler(CommandHandler("py", py_cmd))
        app.add_handler(CommandHandler("env", env_cmd))
        app.add_handler(CommandHandler("reset", reset_cmd))
        app.add_handler(CommandHandler("health", health_cmd))
        app.add_handler(CommandHandler("restart", restart_cmd))
        app.add_handler(CommandHandler("list", list_cmd))
        app.add_handler(CommandHandler("allow", allow_cmd))
        app.add_handler(CommandHandler("deny", deny_cmd))
        app.add_handler(CommandHandler("update", update_allow_cmd))

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
