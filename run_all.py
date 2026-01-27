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

def run_bot():
    """Run the Telegram bot."""
    print("🤖 Starting Telegram Bot...")
    try:
        import bot
        bot.main()
    except Exception as e:
        print(f"❌ Bot error: {e}")
        raise

def run_webapp():
    """Run the Flask web server."""
    print("🌐 Starting Web App Server...")
    try:
        import webapp_server
        host = os.getenv("WEBAPP_HOST", "0.0.0.0")
        port = int(os.getenv("WEBAPP_PORT", "8080"))
        webapp_server.run_server(host=host, port=port)
    except Exception as e:
        print(f"❌ Web App error: {e}")
        raise

def main():
    parser = argparse.ArgumentParser(description="Run Terminal Bot and/or Web App")
    parser.add_argument("--bot-only", action="store_true", help="Run only the Telegram bot")
    parser.add_argument("--web-only", action="store_true", help="Run only the Web App server")
    args = parser.parse_args()
    
    # Handle graceful shutdown
    shutdown_event = threading.Event()
    
    def signal_handler(signum, frame):
        print("\n⏹️ Shutting down...")
        shutdown_event.set()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    if args.bot_only:
        run_bot()
    elif args.web_only:
        run_webapp()
    else:
        # Run both in separate threads
        bot_thread = threading.Thread(target=run_bot, daemon=True)
        web_thread = threading.Thread(target=run_webapp, daemon=True)
        
        bot_thread.start()
        web_thread.start()
        
        print("\n✅ Both services started!")
        print("   Bot: Running with polling")
        print(f"   Web: http://0.0.0.0:{os.getenv('WEBAPP_PORT', '8080')}")
        print("\nPress Ctrl+C to stop.\n")
        
        # Wait for shutdown
        while not shutdown_event.is_set():
            time.sleep(1)
            # Check if threads are still alive
            if not bot_thread.is_alive() and not web_thread.is_alive():
                break

if __name__ == "__main__":
    main()
