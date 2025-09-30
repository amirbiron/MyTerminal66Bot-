#!/usr/bin/env python3
"""
סקריפט לבדיקת הגדרות האינליין בבוט טלגרם
"""
import os
import asyncio
from telegram import Bot

async def check_bot_settings():
    token = os.getenv("BOT_TOKEN")
    if not token:
        print("❌ לא נמצא BOT_TOKEN")
        return
    
    try:
        bot = Bot(token)
        
        # קבלת מידע על הבוט
        me = await bot.get_me()
        print(f"🤖 שם הבוט: @{me.username}")
        print(f"   ID: {me.id}")
        print(f"   שם: {me.first_name}")
        
        # בדיקת תמיכה באינליין
        print(f"\n📝 תמיכה באינליין: {'✅ כן' if me.supports_inline_queries else '❌ לא'}")
        
        if not me.supports_inline_queries:
            print("\n⚠️  כדי להפעיל אינליין:")
            print("1. לכו ל-@BotFather בטלגרם")
            print("2. שלחו /mybots")
            print(f"3. בחרו @{me.username}")
            print("4. לחצו על 'Bot Settings'")
            print("5. לחצו על 'Inline Mode'")
            print("6. הפעילו את המצב האינליין")
            print("7. אופציונלי: הגדירו Inline feedback (100% מומלץ לדיבוג)")
        
        # בדיקת webhook vs polling
        webhook_info = await bot.get_webhook_info()
        if webhook_info.url:
            print(f"\n🌐 Webhook מוגדר: {webhook_info.url}")
        else:
            print("\n🔄 משתמש ב-polling (לא webhook)")
            
    except Exception as e:
        print(f"❌ שגיאה: {e}")

if __name__ == "__main__":
    asyncio.run(check_bot_settings())