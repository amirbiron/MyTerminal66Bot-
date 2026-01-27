#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shared utilities for the Terminal Bot and Web App.

This module contains common functions and constants used by both
bot.py and webapp_server.py to avoid code duplication.
"""

import os
import re
import io
import tempfile
import textwrap
import traceback
import subprocess
import contextlib
import unicodedata


# ==== Default Owner ID ====
# Used when OWNER_ID env var is not set
DEFAULT_OWNER_ID = "6865105071"


# ==== Configuration ====
def parse_owner_ids(raw: str | None) -> set[int]:
    """Parses comma/newline separated owner IDs into a set."""
    if not raw:
        return set()
    parts = [p.strip() for p in raw.replace("\n", ",").split(",")]
    owners: set[int] = set()
    for p in parts:
        if not p:
            continue
        try:
            owners.add(int(p))
        except ValueError:
            continue
    return owners


def parse_cmds_string(value: str) -> set:
    """Parses comma/newline separated command names into a set, trimming blanks."""
    if not value:
        return set()
    tokens = []
    for part in value.replace("\r", "").replace("\n", ",").split(","):
        tok = part.strip()
        if tok:
            tokens.append(tok)
    return set(tokens)


# Default allowed commands
DEFAULT_ALLOWED_CMDS_STR = (
    "ls,pwd,cp,mv,rm,mkdir,rmdir,touch,ln,stat,du,df,find,realpath,readlink,file,tar,"
    "cat,tac,head,tail,cut,sort,uniq,wc,sed,awk,tr,paste,join,nl,rev,grep,"
    "curl,wget,ping,traceroute,dig,host,nslookup,ip,ss,nc,netstat,"
    "uname,uptime,date,whoami,id,who,w,hostname,lscpu,lsblk,free,nproc,ps,top,"
    "echo,env,git,python,python3,pip,pip3,poetry,uv,pytest,"
    "go,rustc,cargo,node,npm,npx,tsc,deno,"
    "zip,unzip,7z,tar,tee,yes,xargs,printf,kill,killall,"
    "bash,sh,chmod,chown,chgrp,make,gcc,g++,javac,java,ssh,scp"
)

DEFAULT_ALLOWED_CMDS = parse_cmds_string(DEFAULT_ALLOWED_CMDS_STR)

# File path for persistent allowed commands
ALLOWED_CMDS_FILE = os.getenv("ALLOWED_CMDS_FILE", "allowed_cmds.txt")


def load_allowed_cmds() -> set:
    """
    Load allowed commands from environment variable and/or file.
    Priority: ENV ALLOWED_CMDS > allowed_cmds.txt > DEFAULT
    """
    # First check environment variable
    env_cmds = os.getenv("ALLOWED_CMDS", "")
    if env_cmds:
        return parse_cmds_string(env_cmds)
    
    # Then check file
    try:
        if os.path.exists(ALLOWED_CMDS_FILE):
            with open(ALLOWED_CMDS_FILE, "r", encoding="utf-8") as fh:
                content = fh.read()
            parsed = parse_cmds_string(content)
            if parsed:  # Only use if file has content
                return parsed
    except Exception:
        pass
    
    # Fall back to default
    return set(DEFAULT_ALLOWED_CMDS)


def save_allowed_cmds(cmds: set) -> None:
    """Save allowed commands to file."""
    try:
        with open(ALLOWED_CMDS_FILE, "w", encoding="utf-8") as fh:
            fh.write("\n".join(sorted(cmds)))
    except Exception:
        pass


# ==== Text Processing ====
def normalize_code(text: str) -> str:
    """
    Clean hidden characters, smart quotes, NBSP, etc.
    - Convert smart quotes to regular quotes
    - Convert NBSP and similar to regular space
    - Normalize unicode to NFKC
    - Replace CRLF with LF
    - Remove markdown code fences
    """
    if not text:
        return ""
    # Unicode normalization
    text = unicodedata.normalize("NFKC", text)
    # Smart quotes
    text = text.replace(""", '"').replace(""", '"').replace("„", '"')
    text = text.replace("'", "'").replace("'", "'")
    # NBSP and similar
    text = text.replace("\u00A0", " ").replace("\u202F", " ")
    # Direction markers
    text = text.replace("\u200E", "").replace("\u200F", "")
    # Soft hyphen
    text = text.replace("\u00AD", "")
    # CRLF to LF
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Remove markdown code fences
    try:
        text = re.sub(r"(?m)^\s*```[a-zA-Z0-9_+\-]*\s*$", "", text)
        text = re.sub(r"(?m)^\s*```\s*$", "", text)
    except Exception:
        pass
    return text


def truncate(s: str, max_output: int = 10000) -> str:
    """Truncate string to max_output characters."""
    s = (s or "").strip()
    if not s:
        return "(no output)"
    if len(s) <= max_output:
        return s
    return s[:max_output] + f"\n\n…[truncated {len(s) - max_output} chars]"


# ==== Validation ====
SAFE_PIP_NAME_RE = re.compile(r'^(?![.-])[a-zA-Z0-9_.-]+$')


def is_safe_pip_name(name: str) -> bool:
    """Check if a package name is safe for pip install."""
    return bool(SAFE_PIP_NAME_RE.match(name))


# ==== Code Execution ====
def exec_python_in_context(src: str, ctx: dict) -> tuple[str, str, str | None]:
    """
    Execute Python code in a given context dict.
    Returns (stdout, stderr, traceback_text | None)
    """
    ctx.setdefault("__builtins__", __builtins__)
    ctx.setdefault("__name__", "__main__")
    
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    tb_text = None
    
    try:
        with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
            exec(src, ctx, ctx)
    except Exception:
        tb_text = traceback.format_exc()
    
    return stdout_buffer.getvalue(), stderr_buffer.getvalue(), tb_text


def run_js_blocking(src: str, cwd: str, env: dict, timeout_sec: int) -> subprocess.CompletedProcess:
    """Execute JS code via node on a temp file (blocking, for use in thread)."""
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".js", encoding="utf-8") as tf:
            tf.write(src)
            tmp_path = tf.name
        return subprocess.run(
            ["node", tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=cwd,
            env=env,
        )
    finally:
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def run_java_blocking(src: str, cwd: str, env: dict, timeout_sec: int) -> subprocess.CompletedProcess:
    """
    Execute Java code via javac+java on a temp file (blocking, for use in thread).
    Finds public class name in code to determine filename, defaults to Main.
    """
    tmp_dir = None
    try:
        # Create temp directory for compilation
        tmp_dir = tempfile.mkdtemp()
        
        # Find public class name
        class_name = "Main"
        try:
            match = re.search(r'public\s+(?:final\s+|abstract\s+|static\s+)*class\s+(\w+)', src)
            if match:
                class_name = match.group(1)
        except Exception:
            pass
        
        # Write code to file
        java_file = os.path.join(tmp_dir, f"{class_name}.java")
        with open(java_file, "w", encoding="utf-8") as f:
            f.write(src)
        
        # Compile
        compile_proc = subprocess.run(
            ["javac", java_file],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=tmp_dir,
            env=env,
        )
        
        if compile_proc.returncode != 0:
            return compile_proc
        
        # Run
        return subprocess.run(
            ["java", class_name],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=tmp_dir,
            env=env,
        )
    finally:
        # Clean up temp files
        try:
            if tmp_dir and os.path.exists(tmp_dir):
                import shutil
                shutil.rmtree(tmp_dir)
        except Exception:
            pass


def run_shell_blocking(shell_exec: str, cmd: str, cwd: str, env: dict, timeout_sec: int) -> subprocess.CompletedProcess:
    """Execute shell command (blocking, for use in thread)."""
    return subprocess.run(
        [shell_exec, "-c", cmd],
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        cwd=cwd,
        env=env,
    )
