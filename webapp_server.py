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
import signal
import hashlib
import tempfile
import textwrap
import traceback
import subprocess
import contextlib
from functools import wraps
from urllib.parse import parse_qsl, unquote
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from flask import Flask, request, jsonify, send_from_directory

# Import shared utilities
from shared_utils import (
    parse_owner_ids,
    load_allowed_cmds,
    normalize_code,
    truncate,
)

# ==== Configuration ====
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_IDS = parse_owner_ids(os.getenv("OWNER_ID", ""))

TIMEOUT = int(os.getenv("CMD_TIMEOUT", "60"))
MAX_OUTPUT = int(os.getenv("MAX_OUTPUT", "10000"))
SHELL_EXECUTABLE = os.getenv("SHELL_EXECUTABLE") or ("/bin/bash" if os.path.exists("/bin/bash") else None)
ALLOW_ALL_COMMANDS = os.getenv("ALLOW_ALL_COMMANDS", "").lower() in ("1", "true", "yes", "on")

# Load allowed commands (respects ENV and file like bot.py)
ALLOWED_CMDS = load_allowed_cmds()

# Sessions per user_id
webapp_sessions = {}
webapp_sessions_lock = Lock()

# Python context per user_id
PY_CONTEXT = {}

# Thread pool for Python execution with timeout
executor = ThreadPoolExecutor(max_workers=4)

# Flask app
app = Flask(__name__, static_folder="webapp/static")


# ==== Helpers ====
def get_session(user_id: int) -> dict:
    """Get or create session for user."""
    with webapp_sessions_lock:
        if user_id not in webapp_sessions:
            webapp_sessions[user_id] = {
                "cwd": os.getcwd(),
                "env": dict(os.environ),
            }
        return webapp_sessions[user_id]


def validate_telegram_webapp_data(init_data: str) -> dict | None:
    """
    Validate data sent from Telegram Web App.
    Returns dict with data if valid, None otherwise.
    """
    if not BOT_TOKEN:
        return None
    
    try:
        # Parse data
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        if "hash" not in parsed:
            return None
        
        received_hash = parsed.pop("hash")
        
        # Create data-check-string
        data_check_arr = []
        for key in sorted(parsed.keys()):
            data_check_arr.append(f"{key}={parsed[key]}")
        data_check_string = "\n".join(data_check_arr)
        
        # Calculate hash
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if not hmac.compare_digest(calculated_hash, received_hash):
            return None
        
        # Check validity (up to 24 hours)
        auth_date = int(parsed.get("auth_date", 0))
        if time.time() - auth_date > 86400:
            return None
        
        # Parse user
        user_data = parsed.get("user", "{}")
        user = json.loads(unquote(user_data))
        parsed["user"] = user
        
        return parsed
    except Exception:
        return None


def require_auth(f):
    """Decorator for user authentication."""
    @wraps(f)
    def decorated(*args, **kwargs):
        init_data = request.headers.get("X-Telegram-Init-Data", "")
        
        # If no BOT_TOKEN, allow access (development mode)
        if not BOT_TOKEN:
            request.user_id = 0
            request.user_data = {}
            return f(*args, **kwargs)
        
        data = validate_telegram_webapp_data(init_data)
        if not data:
            return jsonify({"error": "Unauthorized", "message": "Invalid or expired init data"}), 401
        
        user = data.get("user", {})
        user_id = user.get("id", 0)
        
        # Check authorization
        if OWNER_IDS and user_id not in OWNER_IDS:
            return jsonify({"error": "Forbidden", "message": "Access denied", "user_id": user_id}), 403
        
        request.user_id = user_id
        request.user_data = user
        return f(*args, **kwargs)
    return decorated


# ==== API Endpoints ====

@app.route("/")
def index():
    """Return main Web App page."""
    return send_from_directory("webapp", "index.html")


@app.route("/static/<path:filename>")
def static_files(filename):
    """Return static files."""
    return send_from_directory("webapp/static", filename)


@app.route("/api/health")
def health():
    """Health check."""
    return jsonify({"status": "ok", "timestamp": time.time()})


@app.route("/api/execute", methods=["POST"])
@require_auth
def execute():
    """Execute command or code."""
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
        
        if exec_type == "sh":
            result = execute_shell(code, sess)
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


def execute_shell(cmdline: str, sess: dict) -> dict:
    """Execute shell command."""
    cmdline = normalize_code(cmdline)
    result = {
        "type": "sh",
        "code": cmdline,
        "output": "",
        "error": "",
        "exit_code": 0,
        "timestamp": time.time(),
    }
    
    # Handle cd
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
    
    # Check permission
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
        result["output"] = truncate(p.stdout or "", MAX_OUTPUT)
        result["error"] = p.stderr or ""
        result["exit_code"] = p.returncode
    except subprocess.TimeoutExpired:
        result["error"] = "Timeout"
        result["exit_code"] = -1
    except Exception as e:
        result["error"] = str(e)
        result["exit_code"] = -1
    
    return result


def _exec_python_code(code: str, user_id: int) -> tuple:
    """Execute Python code in shared context. Returns (stdout, stderr, tb_text)."""
    ctx = PY_CONTEXT.get(user_id)
    if ctx is None:
        ctx = {"__builtins__": __builtins__, "__name__": "__main__"}
        PY_CONTEXT[user_id] = ctx
    
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    tb_text = None
    
    try:
        with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
            exec(code, ctx, ctx)
    except Exception:
        tb_text = traceback.format_exc()
    
    return stdout_buffer.getvalue(), stderr_buffer.getvalue(), tb_text


def execute_python(code: str, user_id: int) -> dict:
    """Execute Python code in shared context with timeout."""
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
    
    try:
        # Execute with timeout using ThreadPoolExecutor
        future = executor.submit(_exec_python_code, cleaned, user_id)
        try:
            out, err, tb_text = future.result(timeout=TIMEOUT)
            result["output"] = truncate(out, MAX_OUTPUT)
            result["error"] = err
            if tb_text:
                result["error"] = tb_text
                result["exit_code"] = 1
        except FuturesTimeoutError:
            future.cancel()
            result["error"] = f"Timeout ({TIMEOUT}s)"
            result["exit_code"] = -1
    except Exception as e:
        result["error"] = str(e)
        result["exit_code"] = 1
    
    return result


def execute_js(code: str, sess: dict) -> dict:
    """Execute JavaScript code with Node.js."""
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
        result["output"] = truncate(p.stdout or "", MAX_OUTPUT)
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
    """Execute Java code."""
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
        
        # Find class name
        class_name = "Main"
        match = re.search(r'public\s+(?:final\s+|abstract\s+|static\s+)*class\s+(\w+)', cleaned)
        if match:
            class_name = match.group(1)
        
        java_file = os.path.join(tmp_dir, f"{class_name}.java")
        with open(java_file, "w", encoding="utf-8") as f:
            f.write(cleaned)
        
        # Compile
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
        
        # Run
        p = subprocess.run(
            ["java", class_name],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            cwd=tmp_dir,
            env=sess["env"],
        )
        result["output"] = truncate(p.stdout or "", MAX_OUTPUT)
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
    """Return current session info."""
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
    """Reset session."""
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
    """Return list of allowed commands."""
    return jsonify({
        "commands": sorted(ALLOWED_CMDS),
        "allow_all": ALLOW_ALL_COMMANDS,
    })


# ==== Run ====
def run_server(host="0.0.0.0", port=None):
    """Run the server."""
    port = port or int(os.getenv("WEBAPP_PORT", "8080"))
    debug = os.getenv("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    run_server()
