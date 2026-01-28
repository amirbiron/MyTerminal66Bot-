#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Web App Server - Terminal Interface

שרת Flask שמספק ממשק Web App לטרמינל של הבוט.
תומך בהרצת פקודות shell, Python, JS ו-Java.
כולל טרמינל PTY אינטראקטיבי דרך WebSocket.
"""

import os
import sys
import io
import re
import pty
import json
import time
import hmac
import shlex
import struct
import select
import signal
import hashlib
import textwrap
import traceback
import subprocess
import contextlib
from functools import wraps
from urllib.parse import parse_qsl, unquote
from threading import Lock, Thread
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from flask import Flask, request, jsonify, send_from_directory
from flask_sock import Sock

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
sock = Sock(app)

# PTY sessions per user_id
pty_sessions = {}
pty_sessions_lock = Lock()


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


def _execute_external_code(
    code: str,
    sess: dict,
    exec_type: str,
    run_func,
    not_found_msg: str,
    extra_timeout: int = 5
) -> dict:
    """
    Common execution logic for external languages (JS, Java).
    
    Args:
        code: Source code to execute
        sess: Session dict with cwd and env
        exec_type: Type string ('js' or 'java')
        run_func: The blocking execution function to call
        not_found_msg: Error message when runtime is not found
        extra_timeout: Extra seconds for thread timeout (default 5, use 10 for Java compilation)
    """
    cleaned = textwrap.dedent(code)
    cleaned = normalize_code(cleaned).strip("\n") + "\n"
    
    result = {
        "type": exec_type,
        "code": cleaned,
        "output": "",
        "error": "",
        "exit_code": 0,
        "timestamp": time.time(),
    }
    
    try:
        future = executor.submit(run_func, cleaned, sess["cwd"], sess["env"], TIMEOUT)
        try:
            p = future.result(timeout=TIMEOUT + extra_timeout)
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
        result["error"] = not_found_msg
        result["exit_code"] = -1
    except Exception as e:
        result["error"] = str(e)
        result["exit_code"] = -1
    
    return result


def execute_js(code: str, sess: dict) -> dict:
    """Execute JavaScript code with Node.js."""
    return _execute_external_code(
        code, sess, "js", run_js_blocking, "Node.js not found", extra_timeout=5
    )


def execute_java(code: str, sess: dict) -> dict:
    """Execute Java code."""
    return _execute_external_code(
        code, sess, "java", run_java_blocking, "Java not found", extra_timeout=10
    )


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


# ==== PTY WebSocket Terminal ====

def validate_ws_auth(ws) -> int | None:
    """
    Validate WebSocket authentication.
    Returns user_id if valid, None otherwise.
    """
    # Try to get init_data from first message or subprotocol
    if not BOT_TOKEN:
        if DEV_MODE:
            return 0  # Development mode
        return None
    
    try:
        # Wait for auth message (first message should be auth)
        auth_msg = ws.receive(timeout=5)
        if not auth_msg:
            return None
        
        auth_data = json.loads(auth_msg)
        if auth_data.get("type") != "auth":
            return None
        
        init_data = auth_data.get("init_data", "")
        data = validate_telegram_webapp_data(init_data)
        if not data:
            return None
        
        user = data.get("user", {})
        user_id = user.get("id", 0)
        
        if user_id not in OWNER_IDS:
            return None
        
        return user_id
    except Exception:
        return None


def _close_pty_session_internal(session: dict):
    """
    Close PTY session resources (internal helper, no locking).
    """
    if not session:
        return
    
    try:
        os.close(session["master_fd"])
    except Exception:
        pass
    
    try:
        os.kill(session["pid"], signal.SIGTERM)
        # Give it time to terminate gracefully
        time.sleep(0.1)
        os.kill(session["pid"], signal.SIGKILL)
    except Exception:
        pass
    
    try:
        os.waitpid(session["pid"], os.WNOHANG)
    except Exception:
        pass


def create_pty_session(user_id: int) -> dict:
    """Create a new PTY session for user."""
    # Get shell
    shell = SHELL_EXECUTABLE or os.environ.get("SHELL", "/bin/bash")
    
    # Get user session for cwd/env
    sess = get_session(user_id)
    
    # Close existing session first (outside of lock to avoid deadlock)
    close_pty_session(user_id)
    
    # Create PTY with proper cleanup on failure
    master_fd = None
    slave_fd = None
    
    try:
        master_fd, slave_fd = pty.openpty()
        
        # Fork process
        pid = os.fork()
        
        if pid == 0:
            # Child process
            try:
                os.close(master_fd)
                os.setsid()
                
                # Set controlling terminal
                import fcntl
                fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
                
                # Duplicate slave to stdin/stdout/stderr
                os.dup2(slave_fd, 0)
                os.dup2(slave_fd, 1)
                os.dup2(slave_fd, 2)
                
                if slave_fd > 2:
                    os.close(slave_fd)
                
                # Change to session cwd
                try:
                    os.chdir(sess["cwd"])
                except Exception:
                    pass
                
                # Set environment
                env = sess["env"].copy()
                env["TERM"] = "xterm-256color"
                env["COLORTERM"] = "truecolor"
                
                # Execute shell (this replaces the process)
                os.execvpe(shell, [shell], env)
            except Exception:
                # If execvpe fails, terminate child immediately
                os._exit(1)
            
            # Should never reach here, but just in case
            os._exit(1)
        
        else:
            # Parent process
            os.close(slave_fd)
            slave_fd = None  # Mark as closed
            
            pty_session = {
                "master_fd": master_fd,
                "pid": pid,
                "created_at": time.time(),
            }
            
            with pty_sessions_lock:
                pty_sessions[user_id] = pty_session
            
            return pty_session
    
    except Exception:
        # Clean up file descriptors on failure
        if master_fd is not None:
            try:
                os.close(master_fd)
            except Exception:
                pass
        if slave_fd is not None:
            try:
                os.close(slave_fd)
            except Exception:
                pass
        raise


def close_pty_session(user_id: int):
    """Close PTY session for user."""
    with pty_sessions_lock:
        session = pty_sessions.pop(user_id, None)
    
    _close_pty_session_internal(session)


def resize_pty(master_fd: int, rows: int, cols: int):
    """Resize PTY window."""
    try:
        import fcntl
        import termios
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
    except Exception:
        pass


# Import termios for PTY operations
try:
    import termios
    import fcntl
    PTY_AVAILABLE = True
except ImportError:
    PTY_AVAILABLE = False


@sock.route("/ws/terminal")
def ws_terminal(ws):
    """WebSocket endpoint for interactive PTY terminal."""
    if not PTY_AVAILABLE:
        ws.send(json.dumps({
            "type": "error",
            "message": "PTY not available on this system"
        }))
        return
    
    # PTY provides full shell access - only allow if ALLOW_ALL_COMMANDS is enabled
    # This prevents bypassing command restrictions via PTY
    if not ALLOW_ALL_COMMANDS:
        ws.send(json.dumps({
            "type": "error",
            "message": "PTY terminal requires ALLOW_ALL_COMMANDS=1 (full shell access)"
        }))
        return
    
    # Authenticate
    user_id = validate_ws_auth(ws)
    if user_id is None:
        ws.send(json.dumps({
            "type": "error",
            "message": "Authentication failed"
        }))
        return
    
    # Send auth success
    ws.send(json.dumps({"type": "auth_ok", "user_id": user_id}))
    
    # Create PTY session
    try:
        pty_session = create_pty_session(user_id)
    except Exception as e:
        ws.send(json.dumps({
            "type": "error",
            "message": f"Failed to create PTY: {str(e)}"
        }))
        return
    
    master_fd = pty_session["master_fd"]
    
    # Set non-blocking mode
    import fcntl
    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    
    # Read from PTY and send to WebSocket
    def read_pty():
        while True:
            try:
                readable, _, _ = select.select([master_fd], [], [], 0.1)
                if master_fd in readable:
                    try:
                        data = os.read(master_fd, 4096)
                        if data:
                            ws.send(json.dumps({
                                "type": "output",
                                "data": data.decode("utf-8", errors="replace")
                            }))
                        else:
                            # EOF
                            ws.send(json.dumps({"type": "exit"}))
                            break
                    except OSError:
                        ws.send(json.dumps({"type": "exit"}))
                        break
            except Exception:
                break
    
    # Start PTY reader thread
    reader_thread = Thread(target=read_pty, daemon=True)
    reader_thread.start()
    
    # Main loop - receive from WebSocket and write to PTY
    try:
        while True:
            msg = ws.receive()
            if msg is None:
                break
            
            try:
                data = json.loads(msg)
                msg_type = data.get("type")
                
                if msg_type == "input":
                    # Write input to PTY
                    input_data = data.get("data", "")
                    if input_data:
                        os.write(master_fd, input_data.encode("utf-8"))
                
                elif msg_type == "resize":
                    # Resize PTY
                    rows = data.get("rows", 24)
                    cols = data.get("cols", 80)
                    resize_pty(master_fd, rows, cols)
                
                elif msg_type == "ping":
                    ws.send(json.dumps({"type": "pong"}))
                
            except json.JSONDecodeError:
                # Treat as raw input
                os.write(master_fd, msg.encode("utf-8"))
    
    except Exception:
        pass
    
    finally:
        # Cleanup
        close_pty_session(user_id)


@app.route("/api/pty/close", methods=["POST"])
@require_auth
def close_pty():
    """Close PTY session for current user."""
    user_id = getattr(request, "user_id", 0)
    close_pty_session(user_id)
    return jsonify({"status": "ok", "message": "PTY session closed"})


# ==== Run ====
def run_server(host="0.0.0.0", port=None):
    """Run the server."""
    port = port or int(os.getenv("WEBAPP_PORT", "8080"))
    debug = os.getenv("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    run_server()
