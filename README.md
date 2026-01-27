# MyTerminal66Bot

מדריך קצר:

[הוראות בעברית מלאות](INSTRUCTIONS_HE.md)

## שליחת הודעת בדיקה בטלגרם

החלף לערכים אמיתיים (ללא <>).

```bash
export BOT_TOKEN="123456789:ABCDEF..."
export CHAT_ID="123456789"

curl -sS "https://api.telegram.org/bot$BOT_TOKEN/sendMessage" \
  --data-urlencode "chat_id=$CHAT_ID" \
  --data-urlencode "text=בדיקה"

# בדיקה
curl -sS "https://api.telegram.org/bot$BOT_TOKEN/getMe"
# מציאת chat_id (לאחר שליחת הודעה לבוט)
curl -sS "https://api.telegram.org/bot$BOT_TOKEN/getUpdates"
```

טיפים:
- למשתמש: `chat_id` הוא מזהה מספרי.
- קבוצה/סופרגroupe: לרוב מזהה שלילי (`-100...`).
- ערוץ: אפשר `chat_id=@channelusername` והבוט חייב להיות מנהל.

## פתיחת Pull Request

אם GitHub מציג "There isn’t anything to compare" — צריך לפחות שינוי קובץ אחד.

```bash
git add -A
git commit -m "docs: add README with Telegram tips"
git push -u origin HEAD
```

לאחר מכן פתחו PR מהענף אל `main` דרך עמוד ה-Compare של GitHub.

## הרצה בסביבת Docker (Python 3.11)

```bash
# בנייה
docker build -t myterminal66bot:py311 .

# הרצה (מעביר את הטוקן דרך משתני סביבה)
docker run --rm -e BOT_TOKEN="$BOT_TOKEN" -e OWNER_ID="$OWNER_ID" \
  -e CMD_TIMEOUT=60 -e TG_MAX_MESSAGE=4000 \
  --name myterminal66bot myterminal66bot:py311
```

הדימוי מבוסס על `python:3.11-slim` ומותקנות בו מראש ספריות נפוצות (numpy, matplotlib, pygame) כך שקוד עם `import` יעבוד ללא שגיאות.

## Web App - ממשק גרפי

הבוט כולל ממשק Web App של טלגרם לחוויית משתמש משופרת.

### הרצה מקומית

```bash
# הרצת שרת ה-Web App בלבד
python run_all.py --web-only

# הרצת הבוט והשרת יחד
python run_all.py

# או בנפרד
python webapp_server.py &
python bot.py
```

### הגדרת Web App בטלגרם

1. פתח @BotFather ובחר את הבוט שלך
2. שלח `/setmenubutton`
3. בחר את הבוט והגדר URL לכפתור (למשל: `https://your-domain.com`)
4. קבע את משתנה הסביבה `WEBAPP_URL` לאותו URL

### הרצה עם Docker (בוט + Web App)

```bash
# הרצה עם שני השירותים
docker run --rm -p 8080:8080 \
  -e BOT_TOKEN="$BOT_TOKEN" \
  -e OWNER_ID="$OWNER_ID" \
  -e WEBAPP_URL="https://your-domain.com" \
  --name myterminal66bot myterminal66bot:py311 \
  python run_all.py
```

### הרצה בפרודקשן עם Gunicorn

```bash
gunicorn -w 4 -b 0.0.0.0:8080 webapp_server:app
```

### משתני סביבה ל-Web App

| משתנה | תיאור | ברירת מחדל |
|-------|--------|------------|
| `WEBAPP_URL` | URL של ה-Web App (לכפתור בבוט) | - |
| `WEBAPP_PORT` | פורט לשרת ה-Web | 8080 |
| `WEBAPP_HOST` | כתובת לשרת ה-Web | 0.0.0.0 |
| `FLASK_DEBUG` | מצב Debug של Flask | false |

### מבנה קבצים

```
webapp/
  index.html      # דף ראשי
  static/
    style.css     # עיצוב
    app.js        # לוגיקה
webapp_server.py  # שרת Flask + API
run_all.py        # סקריפט להרצת הכל
```