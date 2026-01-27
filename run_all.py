#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Run both the Telegram Bot and the Web App Server.

Usage:
    python run_all.py              # Run both
    python run_all.py --bot-only   # Run only the bot
    python run_all.py --web-only   # Run only the web server

Environment Variables:
    BOT_TOKEN       - Telegram Bot Token (required for bot)
    OWNER_ID        - Comma-separated list of owner IDs
    WEBAPP_URL      - URL of the Web App (for bot to link to)
    WEBAPP_PORT     - Port for the web server (default: 8080)
    WEBAPP_HOST     - Host for the web server (default: 0.0.0.0)
"""

import os
import sys
import argparse
import threading
import signal
import time

# Track thread errors
bot_error = None
web_error = None


def run_bot():
    """Run the Telegram bot."""
    global bot_error
    print("🤖 Starting Telegram Bot...")
    try:
        import bot
        bot.main()
    except Exception as e:
        bot_error = e
        print(f"❌ Bot error: {e}")
        raise


def run_webapp():
    """Run the Flask web server."""
    global web_error
    print("🌐 Starting Web App Server...")
    try:
        import webapp_server
        host = os.getenv("WEBAPP_HOST", "0.0.0.0")
        port = int(os.getenv("WEBAPP_PORT", "8080"))
        webapp_server.run_server(host=host, port=port)
    except Exception as e:
        web_error = e
        print(f"❌ Web App error: {e}")
        raise


def main():
    parser = argparse.ArgumentParser(description="Run Terminal Bot and/or Web App")
    parser.add_argument("--bot-only", action="store_true", help="Run only the Telegram bot")
    parser.add_argument("--web-only", action="store_true", help="Run only the Web App server")
    args = parser.parse_args()
    
    # Handle graceful shutdown
    shutdown_event = threading.Event()
    exit_code = 0
    
    def signal_handler(signum, frame):
        print("\n⏹️ Shutting down...")
        shutdown_event.set()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    if args.bot_only:
        try:
            run_bot()
        except Exception:
            sys.exit(1)
        sys.exit(0)
    elif args.web_only:
        try:
            run_webapp()
        except Exception:
            sys.exit(1)
        sys.exit(0)
    else:
        # Run both in separate threads
        bot_thread = threading.Thread(target=run_bot, daemon=True, name="bot")
        web_thread = threading.Thread(target=run_webapp, daemon=True, name="webapp")
        
        bot_thread.start()
        web_thread.start()
        
        # Wait a moment for threads to start and potentially fail
        time.sleep(2)
        
        # Check if threads started successfully
        if not bot_thread.is_alive() and not web_thread.is_alive():
            print("❌ Both services failed to start")
            sys.exit(1)
        
        if bot_thread.is_alive() and web_thread.is_alive():
            print("\n✅ Both services started!")
            print("   Bot: Running with polling")
            print(f"   Web: http://0.0.0.0:{os.getenv('WEBAPP_PORT', '8080')}")
            print("\nPress Ctrl+C to stop.\n")
        elif not bot_thread.is_alive():
            print("⚠️ Bot failed to start, web server running")
            exit_code = 1
        elif not web_thread.is_alive():
            print("⚠️ Web server failed to start, bot running")
            exit_code = 1
        
        # Wait for shutdown or thread death
        while not shutdown_event.is_set():
            time.sleep(1)
            
            # Check if both threads died (crash)
            if not bot_thread.is_alive() and not web_thread.is_alive():
                print("❌ Both services have stopped unexpectedly")
                sys.exit(1)
        
        # Exit with appropriate code
        if bot_error or web_error:
            sys.exit(1)
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
