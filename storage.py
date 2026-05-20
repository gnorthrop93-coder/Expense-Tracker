import json
import os
from datetime import datetime

FILE = os.path.join(os.path.dirname(__file__), "expenses.json")
CATEGORIES_FILE = os.path.join(os.path.dirname(__file__), "categories.json")
BUDGETS_FILE = os.path.join(os.path.dirname(__file__), "budgets.json")

DEFAULT_CATEGORIES = [
    {"name": "Food",          "icon": "🍔", "color": "#f59e0b"},
    {"name": "Transport",     "icon": "🚗", "color": "#3b82f6"},
    {"name": "Shopping",      "icon": "🛍️", "color": "#8b5cf6"},
    {"name": "Entertainment", "icon": "🎬", "color": "#ec4899"},
    {"name": "Health",        "icon": "💊", "color": "#10b981"},
    {"name": "Utilities",     "icon": "💡", "color": "#64748b"},
    {"name": "Other",         "icon": "📦", "color": "#6b7280"},
]


# ── Expenses ──────────────────────────────────────────────────────────────────

def load_expenses() -> list:
    if not os.path.exists(FILE):
        return []
    with open(FILE) as f:
        return json.load(f)


def save_expense(amount, note, category, group="General", date=None, attachment=None, expense_type=None):
    data = load_expenses()
    record = {
        "date": date or datetime.now().isoformat(timespec="seconds"),
        "amount": round(amount, 2),
        "note": note,
        "category": category,
        "group": group or "General",
    }
    if attachment:
        record["attachment"] = attachment
    if expense_type == "income":
        record["type"] = "income"
    data.append(record)
    _write_expenses(data)


def bulk_save_expenses(rows: list):
    data = load_expenses()
    data.extend(rows)
    _write_expenses(data)


def update_expense(index: int, updates: dict):
    data = load_expenses()
    if 0 <= index < len(data):
        data[index].update(updates)
        _write_expenses(data)


def delete_expense(index: int):
    data = load_expenses()
    if 0 <= index < len(data):
        data.pop(index)
        _write_expenses(data)


def _write_expenses(data):
    with open(FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_groups() -> list:
    groups = {e.get("group", "General") for e in load_expenses()}
    groups.add("General")
    return sorted(groups)


# ── Categories ────────────────────────────────────────────────────────────────

def load_categories() -> list:
    if not os.path.exists(CATEGORIES_FILE):
        return DEFAULT_CATEGORIES[:]
    with open(CATEGORIES_FILE) as f:
        return json.load(f)


def save_categories(cats: list):
    with open(CATEGORIES_FILE, "w") as f:
        json.dump(cats, f, indent=2)


# ── Budgets ───────────────────────────────────────────────────────────────────

def load_budgets() -> dict:
    if not os.path.exists(BUDGETS_FILE):
        return {}
    with open(BUDGETS_FILE) as f:
        return json.load(f)


def save_budgets(budgets: dict):
    with open(BUDGETS_FILE, "w") as f:
        json.dump(budgets, f, indent=2)
