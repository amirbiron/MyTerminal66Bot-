#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Web App Server - Terminal Interface

שרת Flask שמספק ממשק Web App לטרמינל של הבוט.
תומך בהרצת פקודות shell, Python, JS ו-Java.
"""

import os
import sys
import io
import re
import json
import time
import hmac
import shlex
import hashlib
import asyncio
import tempfile
import textwrap
import traceback
import subprocess
import contextlib
import unicodedata
from functools import wraps
from urllib.parse import parse_qsl, unquote
from threading import Lock

from flask import Flask, request, jsonify, send_from_directory, render_template_string

# ==== תצורה ====
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_SECRET = os.getenv("WEBAPP_SECRET", "")  # אופציונלי - secret נוסף לאבטחה
OWNER_IDS = set()
_owner_raw = os.getenv("OWNER_ID", "")
if _owner_raw:
    for p in _owner_raw.replace("\n", ",").split(","):
        p = p.strip()
        if p.isdigit():
            OWNER_IDS.add(int(p))

TIMEOUT = int(os.getenv("CMD_TIMEOUT", "60"))
MAX_OUTPUT = int(os.getenv("MAX_OUTPUT", "10000"))
SHELL_EXECUTABLE = os.getenv("SHELL_EXECUTABLE") or ("/bin/bash" if os.path.exists("/bin/bash") else None)
ALLOW_ALL_COMMANDS = os.getenv("ALLOW_ALL_COMMANDS", "").lower() in ("1", "true", "yes", "on")

# רשימת פקודות מאושרות (מועתק מ-bot.py)
DEFAULT_ALLOWED_CMDS = set(
    "ls,pwd,cp,mv,rm,mkdir,rmdir,touch,ln,stat,du,df,find,realpath,readlink,file,tar,cat,tac,head,tail,cut,sort,uniq,wc,sed,awk,tr,paste,join,nl,rev,grep,curl,wget,ping,traceroute,dig,host,nslookup,ip,ss,nc,netstat,uname,uptime,date,whoami,id,who,w,hostname,lscpu,lsblk,free,nproc,ps,top,echo,env,git,python,python3,pip,pip3,poetry,uv,pytest,go,rustc,cargo,node,npm,npx,tsc,deno,zip,unzip,7z,tar,tee,yes,xargs,printf,kill,killall,bash,sh,chmod,chown,chgrp,df,du,make,gcc,g++,javac,java,ssh,scp".split(",")
)
ALLOWED_CMDS = set(DEFAULT_ALLOWED_CMDS)

# סשנים לפי user_id
webapp_sessions = {}
webapp_sessions_lock = Lock()

# הקשר פייתון לפי user_id
PY_CONTEXT = {}

# Flask app
app = Flask(__name__, static_folder="webapp/static")

# ==== עזר ====
def normalize_code(text: str) -> str:
    """ניקוי תווים נסתרים, גרשיים חכמים, NBSP וכד'."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace(""", '"').replace(""", '"').replace("„", '"')
    text = text.replace("'", "'").replace("'", "'")
    text = text.replace("\u00A0", " ").replace("\u202F", " ")
    text = text.replace("\u200E", "").replace("\u200F", "")
    text = text.replace("\u00AD", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    try:
        text = re.sub(r"(?m)^\s*```[a-zA-Z0-9_+\-]*\s*$", "", text)
        text = re.sub(r"(?m)^\s*```\s*$", "", text)
    except Exception:
        pass
    return text


def truncate(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return "(no output)"
    if len(s) <= MAX_OUTPUT:
        return s
    return s[:MAX_OUTPUT] + f"\n\n…[truncated {len(s) - MAX_OUTPUT} chars]"


def validate_telegram_webapp_data(init_data: str) -> dict | None:
    """
    מאמת את הנתונים שנשלחו מ-Telegram Web App.
    מחזיר dict עם הנתונים אם תקינים, None אם לא.
    """
    if not BOT_TOKEN:
        return None
    
    try:
        # פירוק הנתונים
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        if "hash" not in parsed:
            return None
        
        received_hash = parsed.pop("hash")
        
        # יצירת data-check-string
        data_check_arr = []
        for key in sorted(parsed.keys()):
            data_check_arr.append(f"{key}={parsed[key]}")
        data_check_string = "\n".join(data_check_arr)
        
        # חישוב ה-hash
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if not hmac.compare_digest(calculated_hash, received_hash):
            return None
        
        # בדיקת תוקף (עד 24 שעות)
        auth_date = int(parsed.get("auth_date", 0))
        if time.time() - auth_date > 86400:
            return None
        
        # פירוק user
        user_data = parsed.get("user", "{}")
        user = json.loads(unquote(user_data))
        parsed["user"] = user
        
        return parsed
    except Exception:
        return None


def get_session(user_id: int) -> dict:
    """מקבל או יוצר סשן למשתמש."""
    with webapp_sessions_lock:
        if user_id not in webapp_sessions:
            webapp_sessions[user_id] = {
                "cwd": os.getcwd(),
                "env": dict(os.environ),
            }
        return webapp_sessions[user_id]


def require_auth(f):
    """דקורטור לאימות משתמש."""
    @wraps(f)
    def decorated(*args, **kwargs):
        init_data = request.headers.get("X-Telegram-Init-Data", "")
        
        # אם אין BOT_TOKEN, נאפשר גישה (למצב פיתוח)
        if not BOT_TOKEN:
            request.user_id = 0
            request.user_data = {}
            return f(*args, **kwargs)
        
        data = validate_telegram_webapp_data(init_data)
        if not data:
            return jsonify({"error": "Unauthorized", "message": "Invalid or expired init data"}), 401
        
        user = data.get("user", {})
        user_id = user.get("id", 0)
        
        # בדיקת הרשאה
        if OWNER_IDS and user_id not in OWNER_IDS:
            return jsonify({"error": "Forbidden", "message": "Access denied", "user_id": user_id}), 403
        
        request.user_id = user_id
        request.user_data = user
        return f(*args, **kwargs)
    return decorated


# ==== API Endpoints ====

@app.route("/")
def index():
    """מחזיר את דף ה-Web App הראשי."""
    return send_from_directory("webapp", "index.html")


@app.route("/static/<path:filename>")
def static_files(filename):
    """מחזיר קבצים סטטיים."""
    return send_from_directory("webapp/static", filename)


@app.route("/api/health")
def health():
    """בדיקת בריאות."""
    return jsonify({"status": "ok", "timestamp": time.time()})


@app.route("/api/execute", methods=["POST"])
@require_auth
def execute():
    """מריץ פקודה או קוד."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        exec_type = data.get("type", "sh")  # sh, py, js, java
        code = data.get("code", "").strip()
        
        if not code:
            return jsonify({"error": "No code provided"}), 400
        
        user_id = getattr(request, "user_id", 0)
        sess = get_session(user_id)
        
        result = {
            "type": exec_type,
            "code": code,
            "output": "",
            "error": "",
            "exit_code": 0,
            "timestamp": time.time(),
        }
        
        if exec_type == "sh":
            result = execute_shell(code, sess, user_id)
        elif exec_type == "py":
            result = execute_python(code, user_id)
        elif exec_type == "js":
            result = execute_js(code, sess)
        elif exec_type == "java":
            result = execute_java(code, sess)
        else:
            return jsonify({"error": f"Unknown type: {exec_type}"}), 400
        
        return jsonify(result)
    
    except Exception as e:
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


def execute_shell(cmdline: str, sess: dict, user_id: int) -> dict:
    """מריץ פקודת shell."""
    cmdline = normalize_code(cmdline)
    result = {
        "type": "sh",
        "code": cmdline,
        "output": "",
        "error": "",
        "exit_code": 0,
        "timestamp": time.time(),
    }
    
    # טיפול ב-cd
    if cmdline.strip().startswith("cd "):
        parts = cmdline.strip().split(maxsplit=1)
        target = parts[1] if len(parts) > 1 else "~"
        target = os.path.expanduser(target)
        if not os.path.isabs(target):
            target = os.path.abspath(os.path.join(sess["cwd"], target))
        if os.path.isdir(target):
            sess["cwd"] = target
            result["output"] = f"Changed to: {target}"
        else:
            result["error"] = f"Directory not found: {target}"
            result["exit_code"] = 1
        return result
    
    # בדיקת הרשאה
    if not ALLOW_ALL_COMMANDS:
        try:
            parts = shlex.split(cmdline, posix=True)
            if parts:
                first_token = parts[0].strip()
                if first_token and first_token not in ALLOWED_CMDS:
                    result["error"] = f"Command not allowed: {first_token}"
                    result["exit_code"] = 1
                    return result
        except ValueError:
            result["error"] = "Parse error"
            result["exit_code"] = 1
            return result
    
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
        result["output"] = truncate(p.stdout or "")
        result["error"] = p.stderr or ""
        result["exit_code"] = p.returncode
    except subprocess.TimeoutExpired:
        result["error"] = "Timeout"
        result["exit_code"] = -1
    except Exception as e:
        result["error"] = str(e)
        result["exit_code"] = -1
    
    return result


def execute_python(code: str, user_id: int) -> dict:
    """מריץ קוד פייתון בהקשר משותף."""
    cleaned = textwrap.dedent(code)
    cleaned = normalize_code(cleaned).strip("\n") + "\n"
    
    result = {
        "type": "py",
        "code": cleaned,
        "output": "",
        "error": "",
        "exit_code": 0,
        "timestamp": time.time(),
    }
    
    # אתחול הקשר
    ctx = PY_CONTEXT.get(user_id)
    if ctx is None:
        ctx = {"__builtins__": __builtins__, "__name__": "__main__"}
        PY_CONTEXT[user_id] = ctx
    
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    
    try:
        with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
            exec(cleaned, ctx, ctx)
        result["output"] = truncate(stdout_buffer.getvalue())
        result["error"] = stderr_buffer.getvalue()
    except Exception:
        result["error"] = traceback.format_exc()
        result["exit_code"] = 1
    
    return result


def execute_js(code: str, sess: dict) -> dict:
    """מריץ קוד JavaScript עם Node.js."""
    cleaned = textwrap.dedent(code)
    cleaned = normalize_code(cleaned).strip("\n") + "\n"
    
    result = {
        "type": "js",
        "code": cleaned,
        "output": "",
        "error": "",
        "exit_code": 0,
        "timestamp": time.time(),
    }
    
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".js", encoding="utf-8") as tf:
            tf.write(cleaned)
            tmp_path = tf.name
        
        p = subprocess.run(
            ["node", tmp_path],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            cwd=sess["cwd"],
            env=sess["env"],
        )
        result["output"] = truncate(p.stdout or "")
        result["error"] = p.stderr or ""
        result["exit_code"] = p.returncode
    except subprocess.TimeoutExpired:
        result["error"] = "Timeout"
        result["exit_code"] = -1
    except FileNotFoundError:
        result["error"] = "Node.js not found"
        result["exit_code"] = -1
    except Exception as e:
        result["error"] = str(e)
        result["exit_code"] = -1
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
    
    return result


def execute_java(code: str, sess: dict) -> dict:
    """מריץ קוד Java."""
    cleaned = textwrap.dedent(code)
    cleaned = normalize_code(cleaned).strip("\n") + "\n"
    
    result = {
        "type": "java",
        "code": cleaned,
        "output": "",
        "error": "",
        "exit_code": 0,
        "timestamp": time.time(),
    }
    
    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp()
        
        # חיפוש שם class
        class_name = "Main"
        match = re.search(r'public\s+(?:final\s+|abstract\s+|static\s+)*class\s+(\w+)', cleaned)
        if match:
            class_name = match.group(1)
        
        java_file = os.path.join(tmp_dir, f"{class_name}.java")
        with open(java_file, "w", encoding="utf-8") as f:
            f.write(cleaned)
        
        # קומפילציה
        compile_proc = subprocess.run(
            ["javac", java_file],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            cwd=tmp_dir,
            env=sess["env"],
        )
        
        if compile_proc.returncode != 0:
            result["error"] = compile_proc.stderr or "Compilation failed"
            result["exit_code"] = compile_proc.returncode
            return result
        
        # הרצה
        p = subprocess.run(
            ["java", class_name],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            cwd=tmp_dir,
            env=sess["env"],
        )
        result["output"] = truncate(p.stdout or "")
        result["error"] = p.stderr or ""
        result["exit_code"] = p.returncode
        
    except subprocess.TimeoutExpired:
        result["error"] = "Timeout"
        result["exit_code"] = -1
    except FileNotFoundError:
        result["error"] = "Java not found"
        result["exit_code"] = -1
    except Exception as e:
        result["error"] = str(e)
        result["exit_code"] = -1
    finally:
        if tmp_dir and os.path.exists(tmp_dir):
            try:
                import shutil
                shutil.rmtree(tmp_dir)
            except Exception:
                pass
    
    return result


@app.route("/api/session", methods=["GET"])
@require_auth
def get_session_info():
    """מחזיר מידע על הסשן הנוכחי."""
    user_id = getattr(request, "user_id", 0)
    sess = get_session(user_id)
    return jsonify({
        "user_id": user_id,
        "cwd": sess["cwd"],
        "user": getattr(request, "user_data", {}),
    })


@app.route("/api/session/reset", methods=["POST"])
@require_auth
def reset_session():
    """מאפס את הסשן."""
    user_id = getattr(request, "user_id", 0)
    with webapp_sessions_lock:
        if user_id in webapp_sessions:
            del webapp_sessions[user_id]
        if user_id in PY_CONTEXT:
            del PY_CONTEXT[user_id]
    return jsonify({"status": "ok", "message": "Session reset"})


@app.route("/api/commands", methods=["GET"])
@require_auth
def list_commands():
    """מחזיר רשימת פקודות מאושרות."""
    return jsonify({
        "commands": sorted(ALLOWED_CMDS),
        "allow_all": ALLOW_ALL_COMMANDS,
    })


# ==== הרצה ====
def run_server(host="0.0.0.0", port=None):
    """מריץ את השרת."""
    port = port or int(os.getenv("WEBAPP_PORT", "8080"))
    debug = os.getenv("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    run_server()
