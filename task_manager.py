import time
import random
from datetime import datetime, timedelta


class TaskManager:
    def __init__(self) -> None:
        self.tasks: list[dict] = []

    def print_header(self) -> None:
        print("\n" + "=" * 80)
        print(" " * 25 + "📋 מערכת ניהול משימות מתקדמת 📋")
        print(" " * 28 + "Task Management System Pro")
        print("=" * 80 + "\n")

    def add_sample_tasks(self) -> None:
        now = datetime.now()
        sample_tasks = [
            {
                "title": "סיום פרויקט Python",
                "priority": "גבוה",
                "status": "בתהליך",
                "progress": 65,
                "due_date": now + timedelta(days=2),
                "category": "פיתוח",
            },
            {
                "title": "פגישה עם צוות",
                "priority": "בינוני",
                "status": "ממתין",
                "progress": 0,
                "due_date": now + timedelta(days=1),
                "category": "ניהול",
            },
            {
                "title": "כתיבת דוקומנטציה",
                "priority": "נמוך",
                "status": "בתהליך",
                "progress": 30,
                "due_date": now + timedelta(days=5),
                "category": "תיעוד",
            },
            {
                "title": "בדיקת באגים",
                "priority": "גבוה",
                "status": "הושלם",
                "progress": 100,
                "due_date": now - timedelta(days=1),
                "category": "QA",
            },
            {
                "title": "עדכון dependencies",
                "priority": "בינוני",
                "status": "בתהליך",
                "progress": 45,
                "due_date": now + timedelta(days=3),
                "category": "תחזוקה",
            },
            {
                "title": "Code Review",
                "priority": "גבוה",
                "status": "ממתין",
                "progress": 0,
                "due_date": now + timedelta(hours=12),
                "category": "פיתוח",
            },
            {
                "title": "אופטימיזציה",
                "priority": "נמוך",
                "status": "בתהליך",
                "progress": 20,
                "due_date": now + timedelta(days=7),
                "category": "ביצועים",
            },
            {
                "title": "כתיבת טסטים",
                "priority": "גבוה",
                "status": "בתהליך",
                "progress": 55,
                "due_date": now + timedelta(days=4),
                "category": "QA",
            },
            {
                "title": "עיצוב UI חדש",
                "priority": "בינוני",
                "status": "ממתין",
                "progress": 0,
                "due_date": now + timedelta(days=10),
                "category": "עיצוב",
            },
            {
                "title": "שדרוג שרת",
                "priority": "גבוה",
                "status": "בתהליך",
                "progress": 80,
                "due_date": now + timedelta(days=1),
                "category": "תשתית",
            },
        ]
        self.tasks = sample_tasks

    def get_status_icon(self, status: str) -> str:
        icons = {"הושלם": "✅", "בתהליך": "⚙️", "ממתין": "⏸️"}
        return icons.get(status, "❓")

    def draw_progress_bar(self, progress: int, width: int = 20) -> str:
        filled = int(width * max(0, min(progress, 100)) / 100)
        bar = "█" * filled + "░" * (width - filled)
        return f"[{bar}] {progress}%"

    def display_tasks(self) -> None:
        print("\n" + "┌" + "─" * 78 + "┐")
        print(
            "│ {:^3} │ {:^28} │ {:^10} │ {:^10} │ {:^18} │".format(
                "#", "משימה", "עדיפות", "סטטוס", "התקדמות"
            )
        )
        print("├" + "─" * 78 + "┤")

        for idx, task in enumerate(self.tasks, 1):
            status_icon = self.get_status_icon(task["status"]) 
            progress_bar = self.draw_progress_bar(task["progress"], 12)

            time_left = task["due_date"] - datetime.now()
            if time_left.days < 0:
                due_indicator = "⚠️ "
            elif time_left.days == 0:
                due_indicator = "🔥 "
            else:
                due_indicator = ""

            print(
                "│ {:^3} │ {:^28} │ {:^10} │ {} {:^7} │ {:^18} │".format(
                    idx,
                    task["title"][:28],
                    task["priority"],
                    status_icon,
                    task["status"],
                    progress_bar,
                )
            )

        print("└" + "─" * 78 + "┘\n")

    def display_detailed_info(self) -> None:
        print("\n" + "╔" + "═" * 78 + "╗")
        print("║" + " " * 25 + "📝 מידע מפורט על המשימות" + " " * 26 + "║")
        print("╠" + "═" * 78 + "╣")

        for idx, task in enumerate(self.tasks, 1):
            due_date = task["due_date"].strftime("%d/%m/%Y %H:%M")
            time_left = task["due_date"] - datetime.now()

            if time_left.days < 0:
                time_status = f"⚠️ איחור של {abs(time_left.days)} ימים"
            elif time_left.days == 0:
                hours = time_left.seconds // 3600
                time_status = f"🔥 נותרו {hours} שעות"
            else:
                time_status = f"✓ נותרו {time_left.days} ימים"

            print("║ משימה #{}: {}".format(idx, task["title"]).ljust(79) + "║")
            print(
                "║   ⭐ עדיפות: {}  |  📂 קטגוריה: {}  |  ⏰ יעד: {}".format(
                    task["priority"], task["category"], due_date
                ).ljust(79)
                + "║"
            )
            print(
                "║   📊 התקדמות: {}  |  {}".format(
                    self.draw_progress_bar(task["progress"], 15), time_status
                ).ljust(79)
                + "║"
            )
            print("╠" + "─" * 78 + "╣")

        print("╚" + "═" * 78 + "╝\n")

    def display_statistics(self) -> None:
        total = len(self.tasks)
        completed = sum(1 for t in self.tasks if t["status"] == "הושלם")
        in_progress = sum(1 for t in self.tasks if t["status"] == "בתהליך")
        waiting = sum(1 for t in self.tasks if t["status"] == "ממתין")
        avg_progress = (
            sum(t["progress"] for t in self.tasks) / total if total > 0 else 0
        )

        high_priority = sum(1 for t in self.tasks if t["priority"] == "גבוה")
        medium_priority = sum(1 for t in self.tasks if t["priority"] == "בינוני")
        low_priority = sum(1 for t in self.tasks if t["priority"] == "נמוך")

        print("╔" + "═" * 78 + "╗")
        print("║" + " " * 30 + "📊 סטטיסטיקות 📊" + " " * 30 + "║")
        print("╠" + "═" * 78 + "╣")

        print(
            "║ ✅ משימות שהושלמו: {}/{} ({}%)".format(
                completed, total, completed * 100 // total if total > 0 else 0
            ).ljust(79)
            + "║"
        )
        print(
            "║ ⚙️  משימות בתהליך: {}/{} ({}%)".format(
                in_progress, total, in_progress * 100 // total if total > 0 else 0
            ).ljust(79)
            + "║"
        )
        print(
            "║ ⏸️  משימות ממתינות: {}/{} ({}%)".format(
                waiting, total, waiting * 100 // total if total > 0 else 0
            ).ljust(79)
            + "║"
        )
        print(
            "║ 📈 התקדמות כללית: {:.1f}%".format(avg_progress).ljust(79) + "║"
        )
        print("╠" + "─" * 78 + "╣")
        print(
            "║ 🔴 עדיפות גבוהה: {} משימות".format(high_priority).ljust(79) + "║"
        )
        print(
            "║ 🟡 עדיפות בינונית: {} משימות".format(medium_priority).ljust(79)
            + "║"
        )
        print(
            "║ 🟢 עדיפות נמוכה: {} משימות".format(low_priority).ljust(79) + "║"
        )

        print("╚" + "═" * 78 + "╝\n")

    def display_timeline(self) -> None:
        print("⏰ ציר זמן - המשימות הקרובות:\n")
        print("┌" + "─" * 78 + "┐")

        sorted_tasks = sorted(self.tasks, key=lambda x: x["due_date"]) 

        for task in sorted_tasks:
            time_left = task["due_date"] - datetime.now()
            due_str = task["due_date"].strftime("%d/%m %H:%M")

            if time_left.days < 0:
                time_str = "⚠️  איחור של {} ימים".format(abs(time_left.days))
            elif time_left.days == 0:
                hours = time_left.seconds // 3600
                time_str = "🔥 נותרו {} שעות".format(hours)
            else:
                time_str = "✓ נותרו {} ימים".format(time_left.days)

            status_icon = self.get_status_icon(task["status"]) 
            print(
                "│ {} {:30} │ {:12} │ {:30} │".format(
                    status_icon, task["title"][:30], due_str, time_str
                )
            )

        print("└" + "─" * 78 + "┘\n")

    def display_categories(self) -> None:
        print("📂 חלוקה לפי קטגוריות:\n")

        categories: dict[str, list[dict]] = {}
        for task in self.tasks:
            cat = task["category"]
            categories.setdefault(cat, []).append(task)

        print("┌" + "─" * 78 + "┐")
        for cat_name, cat_tasks in sorted(categories.items()):
            avg_progress = sum(t["progress"] for t in cat_tasks) / len(cat_tasks)
            print(
                "│ 📁 {:20} │ {} משימות │ התקדמות ממוצעת: {:.0f}%".format(
                    cat_name, len(cat_tasks), avg_progress
                ).ljust(77)
                + "│"
            )

            for task in cat_tasks:
                print(
                    "│    └─ {} ({})".format(task["title"], task["status"]).ljust(77)
                    + "│"
                )
            print("├" + "─" * 78 + "┤")

        print("└" + "─" * 78 + "┘\n")

    def animate_loading(self) -> None:
        frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        print("טוען נתונים...")
        for _ in range(3):
            for frame in frames:
                print(frame, flush=True)
                time.sleep(0.05)
        print("✓ הנתונים נטענו בהצלחה!\n")

    def display_priority_report(self) -> None:
        print("\n" + "▓" * 80)
        print(" " * 25 + "🎯 דוח עדיפויות ומשימות דחופות")
        print("▓" * 80 + "\n")

        urgent_tasks = [
            t
            for t in self.tasks
            if (t["due_date"] - datetime.now()).days <= 1 and t["status"] != "הושלם"
        ]
        high_priority_incomplete = [
            t for t in self.tasks if t["priority"] == "גבוה" and t["status"] != "הושלם"
        ]

        print("🚨 משימות דחופות (תוך 24 שעות):")
        if urgent_tasks:
            for task in urgent_tasks:
                hours_left = (task["due_date"] - datetime.now()).seconds // 3600
                print(
                    f"   • {task['title']} - נותרו {hours_left} שעות | התקדמות: {task['progress']}%"
                )
        else:
            print("   ✓ אין משימות דחופות")

        print("\n🔥 משימות בעדיפות גבוהה שטרם הושלמו:")
        if high_priority_incomplete:
            for task in high_priority_incomplete:
                print(
                    f"   • {task['title']} - {task['status']} | התקדמות: {task['progress']}%"
                )
        else:
            print("   ✓ כל המשימות בעדיפות גבוהה הושלמו!")

        print("\n" + "▓" * 80 + "\n")

    def run(self) -> None:
        self.print_header()
        self.animate_loading()
        self.add_sample_tasks()
        self.display_tasks()
        self.display_statistics()
        self.display_timeline()
        self.display_categories()
        self.display_detailed_info()
        self.display_priority_report()
        print("╔" + "═" * 78 + "╗")
        print("║" + " " * 27 + "✨ המערכת מוכנה לשימוש ✨" + " " * 26 + "║")
        print("╚" + "═" * 78 + "╝\n")


if __name__ == "__main__":
    TaskManager().run()
