import os, shlex, subprocess, tempfile, textwrap, time, socket, asyncio
import zipfile

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import NetworkError, TimedOut, Conflict

# ==== תצורה ====
OWNER_ID   = int(os.getenv("OWNER_ID", "6865105071"))
TIMEOUT    = int(os.getenv("CMD_TIMEOUT", "60"))
MAX_OUTPUT = int(os.getenv("MAX_OUTPUT", "10000"))
ALLOWED_CMDS = set((os.getenv("ALLOWED_CMDS") or
    "ls,pwd,cp,mv,rm,mkdir,rmdir,touch,ln,stat,du,df,find,realpath,readlink,file,tar,cat,tac,head,tail,cut,sort,uniq,wc,sed,awk,tr,paste,join,nl,rev,grep,curl,wget,ping,traceroute,dig,host,nslookup,ip,ss,nc,netstat,uname,uptime,date,whoami,id,who,w,hostname,lscpu,lsblk,free,nproc,ps,top,echo,env,git,python,python3,pip,pip3,poetry,uv,pytest,go,rustc,cargo,node,npm,npx,tsc,deno,zip,unzip,7z,tar,tee,yes,xargs,printf,kill,killall,bash,sh,chmod,chown,chgrp,df,du,make,gcc,g++,javac,java,ssh,scp"
).split(","))

# ==== עזר ====
def allowed(u: Update) -> bool:
    return u.effective_user and u.effective_user.id == OWNER_ID

def truncate(s: str) -> str:
    s = (s or "").strip() or "(no output)"
    return s if len(s) <= MAX_OUTPUT else s[:MAX_OUTPUT] + "\n…(truncated)"

# ==== פקודות ====
async def start(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    await update.message.reply_text("/sh <פקודה>\n/py <קוד>\n/health\n/restart")

async def sh_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    cmdline = update.message.text.partition(" ")[2].strip()
    if not cmdline: return await update.message.reply_text("שימוש: /sh <פקודה>")

    # Detect chaining; if multiple commands are present, skip ALLOWED_CMDS validation
    is_multi = (";" in cmdline) or ("&&" in cmdline) or ("\n" in cmdline)

    if not is_multi:
        try:
            parts = shlex.split(cmdline, posix=True)
        except ValueError:
            return await update.message.reply_text("❗ שגיאת פרסינג")
        if not parts:
            return await update.message.reply_text("❗ אין פקודה")
        cmd_name = parts[0]
        if cmd_name not in ALLOWED_CMDS:
            return await update.message.reply_text(f"❗ פקודה לא מאושרת: {cmd_name}")

    # Execute the full line in a shell, so ';' and '&&' work as expected
    try:
        p = subprocess.run(cmdline, shell=True, capture_output=True, text=True, timeout=TIMEOUT)
        out = p.stdout or ""
        err = p.stderr or ""
        resp = f"$ {cmdline}\n\n{out}"
        if err:
            resp += "\nERR:\n" + err
        resp = resp.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        resp = f"$ {cmdline}\n\n⏱️ Timeout"

    if len(resp) <= MAX_OUTPUT:
        await update.message.reply_text(resp)
    else:
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf:
                tf.write(resp)
                tmp_path = tf.name
            with open(tmp_path, "rb") as fh:
                await update.message.reply_document(fh, filename="output.txt", caption="📄 פלט גדול נשלח כקובץ")
        finally:
            try:
                if tmp_path and os.path.exists(tmp_path): os.remove(tmp_path)
            except: pass

async def py_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    code = update.message.text.partition(" ")[2]
    if not code.strip(): return await update.message.reply_text("שימוש: /py <קוד>")
    cleaned = textwrap.dedent(code).strip() + "\n"
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tf:
            tf.write(cleaned); tmp = tf.name
        p = subprocess.run(["python", "-I", "-S", tmp],
                           capture_output=True, text=True, timeout=TIMEOUT,
                           env={"PYTHONUNBUFFERED":"1"})
        out = p.stdout or "(no output)"
        if p.stderr: out += "\nERR:\n" + p.stderr
        if len(out) <= MAX_OUTPUT:
            await update.message.reply_text(out)
        else:
            big_tmp = None
            try:
                with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf2:
                    tf2.write(out); big_tmp = tf2.name
                with open(big_tmp, "rb") as fh:
                    await update.message.reply_document(fh, filename="py-output.txt", caption="📄 פלט גדול נשלח כקובץ")
            finally:
                try:
                    if big_tmp and os.path.exists(big_tmp): os.remove(big_tmp)
                except: pass
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

# ==== main ====
def main():
    # לוגים שקטים (רק ERROR) כדי למנוע ספאם
    import logging
    logging.basicConfig(level=logging.ERROR)
    for n in ("telegram", "telegram.ext", "httpx"):
        logging.getLogger(n).setLevel(logging.ERROR)

    token = os.getenv("BOT_TOKEN")
    if not token: return

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("sh",      sh_cmd))
    app.add_handler(CommandHandler("py",      py_cmd))
    app.add_handler(CommandHandler("health",  health_cmd))
    app.add_handler(CommandHandler("restart", restart_cmd))

    # נקה webhook לפני polling כדי לצמצם בעיות היסטוריות
    async def _preflight():
        try: await app.bot.delete_webhook(drop_pending_updates=True)
        except: pass
    asyncio.run(_preflight())

    # ריצה שקטה: Conflict לא נפתר – רק לא מדפיס traceback
    while True:
        try:
            app.run_polling(drop_pending_updates=True, poll_interval=1.5, timeout=10)
        except Conflict:
            # יש אינסטנס אחר – נצא בשקט בלי הדפסות
            break
        except (NetworkError, TimedOut):
            time.sleep(8)  # רשת חלשה – ננסה שוב בשקט
            continue
        except Exception:
            time.sleep(8)
            continue

if __name__ == "__main__":
    main()
