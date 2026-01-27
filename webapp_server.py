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
    DEFAULT_OWNER_ID,
    parse_owner_ids,
    load_allowed_cmds,
    normalize_code,
    truncate,
    exec_python_in_context,
    run_js_blocking,
    run_java_blocking,
    run_shell_blocking,
    handle_builtins,
)

# ==== Configuration ====
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# Development mode - allows unauthenticated access (DANGEROUS in production!)
# Only enable if you explicitly set WEBAPP_DEV_MODE=1
DEV_MODE = os.getenv("WEBAPP_DEV_MODE", "").lower() in ("1", "true", "yes")
# Use same default as bot.py to prevent authorization bypass
OWNER_IDS = parse_owner_ids(os.getenv("OWNER_ID", DEFAULT_OWNER_ID))

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

# Thread pool for code execution with timeout
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
        
        # Block access if BOT_TOKEN is not set (unless explicit DEV_MODE)
        if not BOT_TOKEN:
            if DEV_MODE:
                # Development mode - allow unauthenticated access with warning
                request.user_id = 0
                request.user_data = {"dev_mode": True}
                return f(*args, **kwargs)
            else:
                return jsonify({
                    "error": "Server misconfigured",
                    "message": "BOT_TOKEN not set. Set WEBAPP_DEV_MODE=1 for development."
                }), 503
        
        data = validate_telegram_webapp_data(init_data)
        if not data:
            return jsonify({"error": "Unauthorized", "message": "Invalid or expired init data"}), 401
        
        user = data.get("user", {})
        user_id = user.get("id", 0)
        
        # Check authorization - OWNER_IDS is never empty due to default
        if user_id not in OWNER_IDS:
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
    
    # Handle shell builtins (cd, export, unset) - same as bot.py
    # Only handles simple commands, compound commands go to shell
    builtin_resp = handle_builtins(sess, cmdline)
    if builtin_resp is not None:
        result["output"] = builtin_resp
        if builtin_resp.startswith("❌") or builtin_resp.startswith("❗"):
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
        future = executor.submit(run_shell_blocking, shell_exec, cmdline, sess["cwd"], sess["env"], TIMEOUT)
        try:
            p = future.result(timeout=TIMEOUT + 5)  # Extra time for thread overhead
            result["output"] = truncate(p.stdout or "", MAX_OUTPUT)
            result["error"] = p.stderr or ""
            result["exit_code"] = p.returncode
        except FuturesTimeoutError:
            future.cancel()
            result["error"] = f"Timeout ({TIMEOUT}s)"
            result["exit_code"] = -1
    except subprocess.TimeoutExpired:
        result["error"] = f"Timeout ({TIMEOUT}s)"
        result["exit_code"] = -1
    except Exception as e:
        result["error"] = str(e)
        result["exit_code"] = -1
    
    return result


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
    
    # Get or create context for user
    ctx = PY_CONTEXT.get(user_id)
    if ctx is None:
        ctx = {"__builtins__": __builtins__, "__name__": "__main__"}
        PY_CONTEXT[user_id] = ctx
    
    try:
        # Execute with timeout using ThreadPoolExecutor
        future = executor.submit(exec_python_in_context, cleaned, ctx)
        try:
            out, err, tb_text = future.result(timeout=TIMEOUT)
            result["output"] = truncate(out, MAX_OUTPUT)
            
            # Preserve both stderr and traceback (don't overwrite)
            error_parts = []
            if err and err.strip():
                error_parts.append(err.rstrip())
            if tb_text and tb_text.strip():
                error_parts.append(tb_text.rstrip())
                result["exit_code"] = 1
            
            result["error"] = "\n".join(error_parts)
            
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
    
    try:
        future = executor.submit(run_js_blocking, cleaned, sess["cwd"], sess["env"], TIMEOUT)
        try:
            p = future.result(timeout=TIMEOUT + 5)
            result["output"] = truncate(p.stdout or "", MAX_OUTPUT)
            result["error"] = p.stderr or ""
            result["exit_code"] = p.returncode
        except FuturesTimeoutError:
            future.cancel()
            result["error"] = f"Timeout ({TIMEOUT}s)"
            result["exit_code"] = -1
    except subprocess.TimeoutExpired:
        result["error"] = f"Timeout ({TIMEOUT}s)"
        result["exit_code"] = -1
    except FileNotFoundError:
        result["error"] = "Node.js not found"
        result["exit_code"] = -1
    except Exception as e:
        result["error"] = str(e)
        result["exit_code"] = -1
    
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
    
    try:
        future = executor.submit(run_java_blocking, cleaned, sess["cwd"], sess["env"], TIMEOUT)
        try:
            p = future.result(timeout=TIMEOUT + 10)  # Extra time for compilation
            result["output"] = truncate(p.stdout or "", MAX_OUTPUT)
            result["error"] = p.stderr or ""
            result["exit_code"] = p.returncode
        except FuturesTimeoutError:
            future.cancel()
            result["error"] = f"Timeout ({TIMEOUT}s)"
            result["exit_code"] = -1
    except subprocess.TimeoutExpired:
        result["error"] = f"Timeout ({TIMEOUT}s)"
        result["exit_code"] = -1
    except FileNotFoundError:
        result["error"] = "Java not found"
        result["exit_code"] = -1
    except Exception as e:
        result["error"] = str(e)
        result["exit_code"] = -1
    
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
