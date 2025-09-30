# הוראות הפעלה ובדיקה (עברית)

מסמך זה מסביר כיצד להריץ את הבוט בסביבות שונות, כולל בדיקות קבלה.

## דרישות כלליות
- משתני סביבה חובה:
  - `BOT_TOKEN`: טוקן הבוט מטלגרם
  - `OWNER_ID`: מזהה/ים של בעלי הבוט (מספר/ים, אפשר פסיקים: `123,456`)
- אופציונלי:
  - `CMD_TIMEOUT` (ברירת מחדל 60)
  - `TG_MAX_MESSAGE` (ברירת מחדל 4000)

## הרצה עם Docker (מומלץ לשרת/מחשב אישי)
1. ודא שיש Docker על המערכת.
2. בנייה והרצה:
```bash
docker build -t myterminal66bot:py311 .

docker run --rm \
  -e BOT_TOKEN="<הכנס_טוקן>" \
  -e OWNER_ID="<הכנס_מזהה>" \
  -e CMD_TIMEOUT=60 -e TG_MAX_MESSAGE=4000 \
  --name myterminal66bot myterminal66bot:py311
```

## Render (שירות Deployment)
- אי אפשר להריץ Docker מה-Shell של Render.
- כדי לפרוס:
  1. ודא שה-`Dockerfile` קיים בריפו (קיים).
  2. צור Background Worker מסוג Docker ברנדר או המר שירות קיים ל-Docker.
  3. קשרה לריפו/בראנץ'. Render יבנה אוטומטית מה-`Dockerfile`.
  4. הגדר משתני סביבה: `BOT_TOKEN`, `OWNER_ID`, `CMD_TIMEOUT`, `TG_MAX_MESSAGE`.
  5. פרוס.

## הרצה מקומית ללא Docker (Linux/Mac)
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -U pip setuptools wheel
pip install -r requirements.txt
pip install numpy matplotlib pygame

export BOT_TOKEN="<טוקן>"
export OWNER_ID="<מזהה>"
python bot.py
```

## Termux (אנדרואיד)
### מסלול 1: ישיר (פשוט ומהיר)
```bash
pkg update
pkg install -y python git clang make libffi openssl \
  sdl2 sdl2-image sdl2-mixer sdl2-ttf

git clone <repo-url> && cd <repo-name>
pip install -U pip setuptools wheel
pip install -r requirements.txt
pip install numpy matplotlib pygame

export BOT_TOKEN="<טוקן>"
export OWNER_ID="<מזהה>"
python bot.py
```
הערות:
- `turtle` (tkinter) לרוב לא יעבוד ישירות ב-Termux.
- `pygame` מצריך SDL2 (מותקן), לפתיחת חלון יידרש X11/Termux:X11. ל-import בלבד בד"כ מספיק.

### מסלול 2: proot-distro (תאימות גבוהה, כולל tkinter/turtle)
```bash
pkg install -y proot-distro
proot-distro install debian
proot-distro login debian

apt update
apt install -y python3.11 python3.11-venv python3-pip python3-tk \
  libsdl2-dev libsdl2-image-dev libsdl2-mixer-dev libsdl2-ttf-dev \
  git build-essential

git clone <repo-url> && cd <repo-name>
python3 -m pip install -U pip setuptools wheel
pip install -r requirements.txt
pip install numpy matplotlib pygame

export BOT_TOKEN="<טוקן>"
export OWNER_ID="<מזהה>"
python3 bot.py
```
אם תרצה GUI (turtle/pygame חלונאי), התקן Termux:X11 או VNC והגדר DISPLAY.

## בדיקות קבלה (בטלגרם)
- `/py x=5` ואז `/py print(x*2)` → "10"
- `/sh echo "hi" ; echo "bye"` → שתי שורות
- `/sh yes hi | head -n 5` → פועל עם pipe
- קוד פייתון תקול → מציג traceback קריא
- פלט גדול (>4000 תווים) → נשלחת תצוגה מקדימה וקובץ עם פלט מלא

## הערות טכניות
- ריצת פייתון היא בסשן מתמשך עם `PY_CONTEXT`, כך שאפשר להריץ קוד בהמשכים.
- פקודת `/sh` תומכת בצינורות/תנאים/לולאות דרך `bash -c`.
- קיימת פונקציית `normalize_code` שמסירה תווים נסתרים (NBSP/גרשיים חכמים וכו').
- יש הגבלת זמן לפקודות והחזרת שגיאות ידידותיות.