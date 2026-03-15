---
# Portfolio – MyTerminal66Bot

name: "MyTerminal66Bot"
repo: "https://github.com/amirbiron/MyTerminal66Bot-"
status: "פעיל"

one_liner: "בוט טלגרם להרצת קוד ופקודות Shell מרחוק – תומך Python, JavaScript, Java ו-Bash"

stack:
  - Python 3.11
  - python-telegram-bot 22.x
  - Flask + flask-sock (WebSocket)
  - Gunicorn
  - Docker (python:3.11-slim)
  - NumPy, Matplotlib, Pygame (pre-installed)
  - MongoDB (activity reporting)
  - Cryptography, PyJWT

key_features:
  - הרצת קוד Python מרחוק דרך טלגרם
  - תמיכה ב-JavaScript (Node.js), Java ו-Shell
  - Web App ממשק גרפי של טלגרם עם טרמינל PTY אינטראקטיבי
  - ניהול הרשאות – רשימת פקודות מותרות, OWNER_IDS
  - ספריות מדעיות מותקנות מראש (numpy, matplotlib, pygame)
  - Task Manager מובנה עם ניהול משימות
  - Activity Reporter ל-MongoDB
  - Inline mode עם שיתוף תוצאות

architecture:
  summary: |
    בוט טלגרם (python-telegram-bot) עם שרת Flask נלווה ל-Web App.
    הבוט מקבל קוד מהמשתמש, מריץ אותו בתהליך משנה מוגבל (timeout),
    ומחזיר את הפלט. Web App מספק ממשק טרמינל PTY אינטראקטיבי דרך WebSocket.
  entry_points:
    - bot.py – בוט טלגרם ראשי
    - webapp_server.py – שרת Flask ל-Web App
    - run_all.py – הרצת בוט + Web App יחד
    - shared_utils.py – פונקציות משותפות

demo:
  live_url: "" # TODO: בדוק ידנית
  video_url: "" # TODO: בדוק ידנית

setup:
  quickstart: |
    1. git clone <repo-url> && cd MyTerminal66Bot-
    2. export BOT_TOKEN="..." OWNER_ID="..."
    3. docker build -t myterminal66bot:py311 .
    4. docker run --rm -e BOT_TOKEN -e OWNER_ID myterminal66bot:py311

your_role: "פיתוח מלא – ארכיטקטורה, בוט טלגרם, Web App, ניהול הרשאות, Docker"

tradeoffs:
  - הרצת קוד מרחוק מציבה סיכוני אבטחה – מוגבל ל-OWNER_IDS בלבד
  - Docker image גדול בגלל ספריות מדעיות מותקנות מראש
  - WebSocket PTY דורש חיבור יציב

metrics: "" # TODO: בדוק ידנית

faq:
  - q: "מי יכול להריץ פקודות דרך הבוט?"
    a: "רק משתמשים שה-ID שלהם מוגדר ב-OWNER_ID. ניתן להגדיר מספר IDs."
  - q: "אילו שפות תכנות נתמכות?"
    a: "Python, JavaScript (Node.js), Java ו-Bash/Shell."
---
