import json
import os
from contextlib import contextmanager
from datetime import datetime

DATABASE_URL = os.environ.get("DATABASE_URL")

DEFAULT_CATEGORIES = [
    {"name": "Food",          "icon": "🍔", "color": "#f59e0b"},
    {"name": "Transport",     "icon": "🚗", "color": "#3b82f6"},
    {"name": "Shopping",      "icon": "🛍️", "color": "#8b5cf6"},
    {"name": "Entertainment", "icon": "🎬", "color": "#ec4899"},
    {"name": "Health",        "icon": "💊", "color": "#10b981"},
    {"name": "Utilities",     "icon": "💡", "color": "#64748b"},
    {"name": "Other",         "icon": "📦", "color": "#6b7280"},
]


# ══════════════════════════════════════════════════════════════════════════════
# PostgreSQL backend (Railway / production)
# ══════════════════════════════════════════════════════════════════════════════

if DATABASE_URL:
    import psycopg2
    import psycopg2.extras

    @contextmanager
    def _db():
        conn = psycopg2.connect(DATABASE_URL)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init():
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS expenses (
                        id         SERIAL PRIMARY KEY,
                        date       TEXT    NOT NULL,
                        amount     REAL    NOT NULL,
                        note       TEXT    DEFAULT '',
                        category   TEXT    DEFAULT 'Other',
                        group_name TEXT    DEFAULT 'General',
                        type       TEXT,
                        attachment TEXT
                    );
                    CREATE TABLE IF NOT EXISTS categories (
                        sort_order INTEGER DEFAULT 0,
                        name       TEXT    PRIMARY KEY,
                        icon       TEXT    DEFAULT '📦',
                        color      TEXT    DEFAULT '#6b7280'
                    );
                    CREATE TABLE IF NOT EXISTS budgets (
                        category_name TEXT PRIMARY KEY,
                        amount        REAL NOT NULL
                    );
                """)

    _init()

    def _row_to_dict(row):
        e = {
            "date":     row["date"],
            "amount":   row["amount"],
            "note":     row["note"] or "",
            "category": row["category"] or "Other",
            "group":    row["group_name"] or "General",
        }
        if row["type"]:
            e["type"] = row["type"]
        if row["attachment"]:
            e["attachment"] = row["attachment"]
        return e

    def _id_at(conn, index):
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM expenses ORDER BY id LIMIT 1 OFFSET %s", (index,))
            row = cur.fetchone()
            return row[0] if row else None

    def load_expenses():
        with _db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM expenses ORDER BY id")
                return [_row_to_dict(r) for r in cur.fetchall()]

    def save_expense(amount, note, category, group="General", date=None,
                     attachment=None, expense_type=None):
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO expenses (date, amount, note, category, group_name, type, attachment)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (date or datetime.now().isoformat(timespec="seconds"),
                     round(amount, 2), note or "", category,
                     group or "General", expense_type, attachment)
                )

    def bulk_save_expenses(rows):
        with _db() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur,
                    "INSERT INTO expenses (date, amount, note, category, group_name, type, attachment)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    [(r.get("date", datetime.now().isoformat(timespec="seconds")),
                      round(r["amount"], 2), r.get("note", ""), r.get("category", "Other"),
                      r.get("group", "General"), r.get("type"), r.get("attachment"))
                     for r in rows]
                )

    def update_expense(index, updates):
        col_map = {"note": "note", "category": "category",
                   "amount": "amount", "date": "date", "group": "group_name"}
        sets, vals = [], []
        for k, v in updates.items():
            col = col_map.get(k)
            if col:
                sets.append(f"{col} = %s")
                vals.append(v)
        if not sets:
            return
        with _db() as conn:
            eid = _id_at(conn, index)
            if eid is None:
                return
            vals.append(eid)
            with conn.cursor() as cur:
                cur.execute(f"UPDATE expenses SET {', '.join(sets)} WHERE id = %s", vals)

    def delete_expense(index):
        with _db() as conn:
            eid = _id_at(conn, index)
            if eid is None:
                return
            with conn.cursor() as cur:
                cur.execute("DELETE FROM expenses WHERE id = %s", (eid,))

    def get_groups():
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT group_name FROM expenses")
                groups = {row[0] for row in cur.fetchall() if row[0]}
        groups.add("General")
        return sorted(groups)

    def load_categories():
        with _db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT name, icon, color FROM categories ORDER BY sort_order, name")
                rows = cur.fetchall()
        return [dict(r) for r in rows] if rows else DEFAULT_CATEGORIES[:]

    def save_categories(cats):
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM categories")
                for i, c in enumerate(cats):
                    cur.execute(
                        "INSERT INTO categories (sort_order, name, icon, color) VALUES (%s,%s,%s,%s)",
                        (i, c["name"], c.get("icon", "📦"), c.get("color", "#6b7280"))
                    )

    def load_budgets():
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT category_name, amount FROM budgets")
                return {row[0]: row[1] for row in cur.fetchall()}

    def save_budgets(budgets):
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM budgets")
                for name, amount in budgets.items():
                    cur.execute(
                        "INSERT INTO budgets (category_name, amount) VALUES (%s,%s)",
                        (name, amount)
                    )


# ══════════════════════════════════════════════════════════════════════════════
# JSON fallback (local development)
# ══════════════════════════════════════════════════════════════════════════════

else:
    _DIR = os.path.dirname(__file__)
    FILE            = os.path.join(_DIR, "expenses.json")
    CATEGORIES_FILE = os.path.join(_DIR, "categories.json")
    BUDGETS_FILE    = os.path.join(_DIR, "budgets.json")

    def load_expenses():
        if not os.path.exists(FILE):
            return []
        with open(FILE) as f:
            return json.load(f)

    def save_expense(amount, note, category, group="General", date=None,
                     attachment=None, expense_type=None):
        data = load_expenses()
        record = {
            "date":     date or datetime.now().isoformat(timespec="seconds"),
            "amount":   round(amount, 2),
            "note":     note,
            "category": category,
            "group":    group or "General",
        }
        if attachment:
            record["attachment"] = attachment
        if expense_type == "income":
            record["type"] = "income"
        data.append(record)
        _write(data)

    def bulk_save_expenses(rows):
        data = load_expenses()
        data.extend(rows)
        _write(data)

    def update_expense(index, updates):
        data = load_expenses()
        if 0 <= index < len(data):
            data[index].update(updates)
            _write(data)

    def delete_expense(index):
        data = load_expenses()
        if 0 <= index < len(data):
            data.pop(index)
            _write(data)

    def _write(data):
        with open(FILE, "w") as f:
            json.dump(data, f, indent=2)

    def get_groups():
        groups = {e.get("group", "General") for e in load_expenses()}
        groups.add("General")
        return sorted(groups)

    def load_categories():
        if not os.path.exists(CATEGORIES_FILE):
            return DEFAULT_CATEGORIES[:]
        with open(CATEGORIES_FILE) as f:
            return json.load(f)

    def save_categories(cats):
        with open(CATEGORIES_FILE, "w") as f:
            json.dump(cats, f, indent=2)

    def load_budgets():
        if not os.path.exists(BUDGETS_FILE):
            return {}
        with open(BUDGETS_FILE) as f:
            return json.load(f)

    def save_budgets(budgets):
        with open(BUDGETS_FILE, "w") as f:
            json.dump(budgets, f, indent=2)
