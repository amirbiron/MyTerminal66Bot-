# MyTerminal66Bot

מדריך קצר:

[הוראות בעברית מלאות](INSTRUCTIONS_HE.md)

## מצב דיבוג (Debug Mode)

כדי להפעיל לוגים מפורטים (שימושי לפתרון בעיות באינליין ובכלל):

```bash
# הגדרת משתנה סביבה
export DEBUG_MODE=true

# או הרצה עם המשתנה
DEBUG_MODE=true python bot.py
```

**כשמצב דיבוג כבוי (ברירת מחדל):**
- רק לוגים ברמת CRITICAL מוצגים
- הבוט רץ בשקט ללא פלט דיבוג

**כשמצב דיבוג פעיל:**
- כל לוגי הדיבוג מוצגים עם חותמות זמן
- עוזר לפתרון בעיות באינליין ובפונקציות אחרות
- מציג מידע מפורט על אינטראקציות משתמשים

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