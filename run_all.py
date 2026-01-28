#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Run both the Telegram Bot and the Web App Server.

Usage:
    python run_all.py              # Run both
    python run_all.py --bot-only   # Run only the bot
    python run_all.py --web-only   # Run only the web server
    python run_all.py --web-only --dev  # Run web server in dev mode (no auth)

Environment Variables:
    BOT_TOKEN       - Telegram Bot Token (required for bot)
    OWNER_ID        - Comma-separated list of owner IDs
    WEBAPP_URL      - URL of the Web App (for bot to link to)
    WEBAPP_PORT     - Port for the web server (default: 8080)
    WEBAPP_HOST     - Host for the web server (default: 0.0.0.0)
    WEBAPP_DEV_MODE - Set to 1 to skip authentication (for development)
"""

import os
import sys
import argparse
import threading
import signal
import time


def run_webapp_in_thread():
    """Run the Flask web server in a background thread."""
    try:
        import webapp_server
        host = os.getenv("WEBAPP_HOST", "0.0.0.0")
        port = int(os.getenv("WEBAPP_PORT", "8080"))
        # Disable Flask reloader when running in thread
        webapp_server.app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)
    except Exception as e:
        print(f"❌ Web App error: {e}")


def run_bot_in_main():
    """Run the Telegram bot in the main thread (required for signal handlers)."""
    print("🤖 Starting Telegram Bot...")
    import bot
    bot.main()


def run_webapp_only():
    """Run only the Flask web server."""
    print("🌐 Starting Web App Server...")
    try:
        import webapp_server
        host = os.getenv("WEBAPP_HOST", "0.0.0.0")
        port = int(os.getenv("WEBAPP_PORT", "8080"))
        webapp_server.run_server(host=host, port=port)
    except Exception as e:
        print(f"❌ Web App error: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Run Terminal Bot and/or Web App")
    parser.add_argument("--bot-only", action="store_true", help="Run only the Telegram bot")
    parser.add_argument("--web-only", action="store_true", help="Run only the Web App server")
    parser.add_argument("--dev", action="store_true", help="Enable dev mode (skip auth for web app)")
    args = parser.parse_args()
    
    # Set dev mode environment variable if --dev flag is used
    if args.dev:
        os.environ["WEBAPP_DEV_MODE"] = "1"
        print("🔧 Dev mode enabled - authentication disabled")
    
    if args.bot_only:
        try:
            run_bot_in_main()
        except KeyboardInterrupt:
            print("\n⏹️ Shutting down...")
            sys.exit(0)
        except Exception as e:
            print(f"❌ Bot crashed: {e}")
            sys.exit(1)
        return
    
    if args.web_only:
        run_webapp_only()
        return
    
    # Run both: Web App in thread, Bot in main thread
    # (Bot MUST be in main thread for signal handlers to work)
    
    print("🌐 Starting Web App Server in background...")
    web_thread = threading.Thread(target=run_webapp_in_thread, daemon=True, name="webapp")
    web_thread.start()
    
    # Give the web server a moment to start
    time.sleep(1)
    
    if web_thread.is_alive():
        print(f"✅ Web App running on http://0.0.0.0:{os.getenv('WEBAPP_PORT', '8080')}")
    else:
        print("⚠️ Web App failed to start")
    
    # Run bot in main thread (required!)
    print("🤖 Starting Telegram Bot in main thread...")
    try:
        run_bot_in_main()
    except KeyboardInterrupt:
        print("\n⏹️ Shutting down...")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Bot crashed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
