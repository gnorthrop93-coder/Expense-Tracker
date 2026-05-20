import base64
import calendar
import csv
import io
import os
import uuid
import openpyxl
from collections import defaultdict
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv(override=True)

from flask import Flask, request, redirect, render_template, url_for
from categorizer import categorize, categorize_batch
from storage import (load_expenses, save_expense, bulk_save_expenses,
                     update_expense, delete_expense, get_groups,
                     load_categories, save_categories,
                     load_budgets, save_budgets)

app = Flask(__name__)
app.jinja_env.globals["enumerate"] = enumerate

READ_ONLY = os.environ.get("READ_ONLY", "").lower() in ("1", "true", "yes")

UPLOADS_DIR = os.path.join(os.path.dirname(__file__), "static", "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)


def _readonly_block():
    """Return a redirect with a flash message if in read-only mode."""
    if READ_ONLY:
        from flask import flash
        app.secret_key = app.secret_key or "readonly"
        return redirect(request.referrer or "/")
    return None

MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
FULL_MONTH_NAMES = ["January","February","March","April","May","June",
                    "July","August","September","October","November","December"]
PRESET_COLORS = ["#f59e0b","#ef4444","#3b82f6","#8b5cf6","#ec4899","#10b981",
                 "#f97316","#06b6d4","#84cc16","#a855f7","#0ea5e9","#64748b"]
PRESET_ICONS  = ["🍔","🍕","☕","🛒","🚗","✈️","🛍️","💻","🎬","🎮","🎵","💊",
                 "🏠","💡","🏋️","💰","🎓","🐾","🔧","🎁","📸","🌍","💄","📦"]


INCOME_CAT = {"name": "Income", "color": "#10b981", "icon": "💰"}


def cat_map():
    cm = {c["name"]: c for c in load_categories()}
    cm["Income"] = INCOME_CAT
    return cm


def _common(extra=None):
    ctx = {
        "categories": load_categories(),
        "cat_map": cat_map(),
        "groups": get_groups(),
        "budgets": load_budgets(),
    }
    if extra:
        ctx.update(extra)
    return ctx


def _get_recurring_merchants(expenses, min_months=3):
    """Return merchants appearing in 3+ distinct calendar months."""
    merchant_months = defaultdict(set)
    merchant_amounts = defaultdict(list)
    for e in expenses:
        if e.get("type") == "income":
            continue
        note = (e.get("note") or "").strip()
        if not note or len(note) < 4:
            continue
        month = e["date"][:7]
        merchant_months[note].add(month)
        merchant_amounts[note].append(e["amount"])

    recurring = []
    for note, months in merchant_months.items():
        if len(months) >= min_months:
            amounts = merchant_amounts[note]
            avg_amt = sum(amounts) / len(amounts)
            recurring.append({"note": note, "months": len(months), "avg_amount": avg_amt})

    return sorted(recurring, key=lambda x: -x["months"])[:8]


# ── Main view ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    expenses = load_expenses()
    active_group = request.args.get("group", "All")
    cat_filter   = request.args.get("cat", "")
    search_q     = request.args.get("q", "").strip()

    indexed = [{**e, "_index": i} for i, e in enumerate(expenses)]

    filtered = indexed
    if active_group != "All":
        filtered = [e for e in filtered if e.get("group", "General") == active_group]
    if cat_filter:
        filtered = [e for e in filtered if e.get("category") == cat_filter]
    if search_q:
        sq_lower = search_q.lower()
        filtered = [e for e in filtered if sq_lower in (e.get("note","") or "").lower()
                    or sq_lower in (e.get("category","") or "").lower()
                    or sq_lower in f"{e.get('amount', 0):.2f}"]

    if search_q or cat_filter:
        recent = list(reversed(filtered))
    else:
        recent = list(reversed(filtered[-20:]))

    search_expense_rows = [e for e in filtered if e.get("type") != "income"]
    search_total  = sum(e["amount"] for e in search_expense_rows)
    search_income = sum(e["amount"] for e in filtered if e.get("type") == "income")
    search_count  = len(filtered)

    now = datetime.now()
    month_prefix = f"{now.year}-{now.month:02d}"
    month_scope = [
        e for e in filtered
        if e["date"].startswith(month_prefix) and e.get("type") != "income"
    ]
    month_total = sum(e["amount"] for e in month_scope)

    cat_totals, cat_counts = {}, {}
    for e in month_scope:
        c = e["category"]
        cat_totals[c]  = cat_totals.get(c, 0) + e["amount"]
        cat_counts[c]  = cat_counts.get(c, 0) + 1

    max_expense = max((e["amount"] for e in recent), default=1) or 1

    return render_template("index.html", **_common({
        "expenses":        recent,
        "month_total":     month_total,
        "category_totals": sorted(cat_totals.items(), key=lambda x: -x[1]),
        "category_counts": cat_counts,
        "max_expense":     max_expense,
        "added":           request.args.get("added"),
        "active_group":    active_group,
        "cat_filter":      cat_filter,
        "search_q":        search_q,
        "search_total":    search_total,
        "search_income":   search_income,
        "search_count":    search_count,
    }))


# ── Add expense ───────────────────────────────────────────────────────────────

@app.route("/add", methods=["POST"])
def add():
    _ro = _readonly_block()
    if _ro: return _ro
    raw_amount = request.form.get("amount", "").strip()
    note       = request.form.get("note", "").strip()
    group      = request.form.get("group", "General").strip() or "General"

    file_data, file_type, attachment_path = None, None, None
    f = request.files.get("receipt")
    if f and f.filename:
        file_bytes = f.read()
        fname_lower = f.filename.lower()

        if fname_lower.endswith((".csv", ".xlsx", ".xls")):
            return _process_import_bytes(file_bytes, fname_lower)

        file_type  = f.content_type or "image/jpeg"
        file_data  = base64.standard_b64encode(file_bytes).decode("utf-8")
        if file_type == "application/pdf":
            fname = f"{uuid.uuid4().hex}.pdf"
            with open(os.path.join(UPLOADS_DIR, fname), "wb") as fh:
                fh.write(file_bytes)
            attachment_path = f"uploads/{fname}"

    if not raw_amount and not file_data:
        return redirect(url_for("index"))

    amount = float(raw_amount) if raw_amount else 0.0
    result = categorize(amount, note, file_data, file_type, categories=load_categories())
    category = result["category"]
    if result.get("amount"):
        amount = result["amount"]

    if amount <= 0:
        return redirect(url_for("index"))

    save_expense(amount, note, category, group=group, attachment=attachment_path)
    return redirect(url_for("index", added=category, group=group))


# ── Update expense ────────────────────────────────────────────────────────────

@app.route("/expense/<int:index>/category", methods=["POST"])
def update_category(index):
    _ro = _readonly_block()
    if _ro: return _ro
    update_expense(index, {"category": request.form.get("category", "Other")})
    if request.form.get("back"):
        return redirect(request.form["back"])
    return redirect(url_for("index",
        group=request.form.get("group", "All"),
        cat=request.form.get("cat", "")))


@app.route("/expense/<int:index>/date", methods=["POST"])
def update_date(index):
    _ro = _readonly_block()
    if _ro: return _ro
    raw = request.form.get("date", "").strip()
    if raw:
        update_expense(index, {"date": raw + "T00:00:00"})
    back = request.form.get("back", "")
    if back:
        return redirect(back)
    return redirect(url_for("index",
        group=request.form.get("group", "All"),
        cat=request.form.get("cat", "")))


@app.route("/expense/<int:index>/amount", methods=["POST"])
def update_amount(index):
    _ro = _readonly_block()
    if _ro: return _ro
    raw = request.form.get("amount", "").strip()
    if raw:
        try:
            amt = round(float(raw), 2)
            if amt > 0:
                update_expense(index, {"amount": amt})
        except ValueError:
            pass
    back = request.form.get("back", "")
    if back:
        return redirect(back)
    return redirect(url_for("index",
        group=request.form.get("group", "All"),
        cat=request.form.get("cat", "")))


@app.route("/expense/<int:index>/note", methods=["POST"])
def update_note(index):
    _ro = _readonly_block()
    if _ro: return _ro
    note = request.form.get("note", "").strip()
    update_expense(index, {"note": note})
    back = request.form.get("back", "")
    if back:
        return redirect(back)
    return redirect(url_for("index",
        group=request.form.get("group", "All"),
        cat=request.form.get("cat", "")))


# ── Delete expense ────────────────────────────────────────────────────────────

@app.route("/delete/<int:index>", methods=["POST"])
def delete(index):
    _ro = _readonly_block()
    if _ro: return _ro
    delete_expense(index)
    if request.form.get("back"):
        return redirect(request.form["back"])
    return redirect(url_for("index",
        group=request.form.get("group", "All"),
        cat=request.form.get("cat", "")))


# ── Day detail ───────────────────────────────────────────────────────────────

@app.route("/day/<date>")
def day_view(date):
    expenses = load_expenses()
    indexed  = [{**e, "_index": i} for i, e in enumerate(expenses)]
    day_exps = [e for e in indexed if e["date"].startswith(date)]
    day_total = sum(e["amount"] for e in day_exps if e.get("type") != "income")

    cat_totals = {}
    for e in day_exps:
        if e.get("type") == "income":
            continue
        c = e["category"]
        cat_totals[c] = cat_totals.get(c, 0) + e["amount"]

    try:
        label = datetime.strptime(date, "%Y-%m-%d").strftime("%A, %B %-d %Y")
    except Exception:
        label = date

    return render_template("day.html", **_common({
        "date":       date,
        "label":      label,
        "expenses":   list(reversed(day_exps)),
        "day_total":  day_total,
        "cat_totals": sorted(cat_totals.items(), key=lambda x: -x[1]),
        "max_expense": max((e["amount"] for e in day_exps), default=1) or 1,
    }))


# ── Summary ───────────────────────────────────────────────────────────────────

@app.route("/summary")
def summary():
    period = request.args.get("period", "month")
    expenses = load_expenses()
    now = datetime.now()

    if period == "daily":
        day_map = {}
        for e in expenses:
            if e.get("type") == "income":
                continue
            d = e["date"][:10]
            if d not in day_map:
                day_map[d] = {"total": 0, "cats": {}}
            day_map[d]["total"] += e["amount"]
            c = e["category"]
            day_map[d]["cats"][c] = day_map[d]["cats"].get(c, 0) + e["amount"]
        days = sorted(day_map.items(), reverse=True)
        return render_template("summary.html", **_common({
            "period": "daily", "days": days, "total": sum(v["total"] for _, v in days),
            "bar_data": [], "max_bar": 1, "cat_totals": [], "cat_counts": {},
            "income_total": 0, "net": 0, "mom_diff": None, "prev_month_name": "",
            "recurring": [],
        }))

    # Parse optional ?month=YYYY-MM for month/calendar navigation
    month_param = request.args.get("month", "").strip()
    view_year, view_month = now.year, now.month
    if month_param:
        try:
            vd = datetime.strptime(month_param, "%Y-%m")
            view_year, view_month = vd.year, vd.month
        except ValueError:
            pass

    # Clamp to current month (no future browsing)
    if (view_year, view_month) > (now.year, now.month):
        view_year, view_month = now.year, now.month

    is_current_month = (view_year == now.year and view_month == now.month)

    # Build prev/next month URL helpers
    def _month_url(p, y, m):
        return f"/summary?period={p}&month={y}-{m:02d}"

    if view_month == 1:
        prev_y, prev_m = view_year - 1, 12
    else:
        prev_y, prev_m = view_year, view_month - 1

    if view_month == 12:
        next_y, next_m = view_year + 1, 1
    else:
        next_y, next_m = view_year, view_month + 1

    view_month_label = f"{FULL_MONTH_NAMES[view_month - 1]} {view_year}"

    if period == "calendar":
        cal_prefix = f"{view_year}-{view_month:02d}"
        cal_exps = [e for e in expenses if e["date"].startswith(cal_prefix) and e.get("type") != "income"]
        daily_totals = {}
        for e in cal_exps:
            d = int(e["date"][8:10])
            daily_totals[d] = daily_totals.get(d, 0) + e["amount"]
        _, days_in_month = calendar.monthrange(view_year, view_month)
        first_weekday, _ = calendar.monthrange(view_year, view_month)
        max_daily = max(daily_totals.values(), default=1) or 1
        return render_template("summary.html", **_common({
            "period": "calendar",
            "daily_totals": daily_totals,
            "days_in_month": days_in_month,
            "first_weekday": first_weekday,
            "max_daily": max_daily,
            "today_day": now.day if is_current_month else -1,
            "cal_year": view_year,
            "cal_month": view_month,
            "cal_month_name": FULL_MONTH_NAMES[view_month - 1],
            "total": sum(daily_totals.values()),
            "bar_data": [], "max_bar": 1, "cat_totals": [], "cat_counts": {},
            "income_total": 0, "net": 0, "mom_diff": None, "prev_month_name": "",
            "recurring": [],
            "view_month_label": view_month_label,
            "prev_month_url": _month_url("calendar", prev_y, prev_m),
            "next_month_url": _month_url("calendar", next_y, next_m) if not is_current_month else None,
        }))

    if period == "week":
        start = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0)
        in_range = [e for e in expenses if datetime.fromisoformat(e["date"]) >= start]
        labels   = [(start + timedelta(days=i)).strftime("%a") for i in range(7)]
        day_keys = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
        buckets  = {d: 0.0 for d in day_keys}
        for e in in_range:
            if e.get("type") == "income":
                continue
            d = e["date"][:10]
            if d in buckets:
                buckets[d] += e["amount"]
        bar_data = [(labels[i], buckets[day_keys[i]], f"/day/{day_keys[i]}") for i in range(7)]

    elif period == "year":
        in_range = [e for e in expenses if e["date"].startswith(str(now.year))]
        buckets  = {m: 0.0 for m in MONTH_NAMES}
        for e in in_range:
            if e.get("type") == "income":
                continue
            buckets[MONTH_NAMES[int(e["date"][5:7]) - 1]] += e["amount"]
        bar_data = [
            (MONTH_NAMES[i], buckets[MONTH_NAMES[i]],
             f"/summary?period=month&month={now.year}-{i+1:02d}")
            for i in range(12)
        ]

    else:  # month
        prefix   = f"{view_year}-{view_month:02d}"
        in_range = [e for e in expenses if e["date"].startswith(prefix)]
        buckets  = {f"Wk {w}": 0.0 for w in range(1, 6)}
        for e in in_range:
            if e.get("type") == "income":
                continue
            wk = (datetime.fromisoformat(e["date"]).day - 1) // 7 + 1
            buckets[f"Wk {wk}"] += e["amount"]
        bar_data = [(k, buckets[k], None) for k in sorted(buckets)]

    expenses_list = [e for e in in_range if e.get("type") != "income"]
    income_list   = [e for e in in_range if e.get("type") == "income"]
    expenses_total = sum(e["amount"] for e in expenses_list)
    income_total   = sum(e["amount"] for e in income_list)
    net = income_total - expenses_total
    total = expenses_total

    cat_totals, cat_counts = {}, {}
    for e in expenses_list:
        c = e["category"]
        cat_totals[c] = cat_totals.get(c, 0) + e["amount"]
        cat_counts[c] = cat_counts.get(c, 0) + 1

    max_bar = max((v for _, v, *_ in bar_data), default=1) or 1

    cat_totals_sorted = sorted(cat_totals.items(), key=lambda x: -x[1])
    cat_grand_total = total

    # Month-over-month, recurring, and indexed expense list (month period only)
    mom_diff, prev_month_name, recurring, period_expenses = None, "", [], []
    summary_back_url = ""
    if period == "month":
        prev_prefix = f"{prev_y}-{prev_m:02d}"
        prev_exps = [e for e in expenses if e["date"].startswith(prev_prefix) and e.get("type") != "income"]
        prev_total = sum(e["amount"] for e in prev_exps)
        mom_diff = expenses_total - prev_total
        prev_month_name = MONTH_NAMES[prev_m - 1]
        recurring = _get_recurring_merchants(expenses)
        # All transactions for this month (expenses + income)
        period_expenses = list(reversed([
            {**e, "_index": i} for i, e in enumerate(expenses)
            if e["date"].startswith(prefix)
        ]))
        summary_back_url = f"/summary?period=month&month={view_year}-{view_month:02d}"

    return render_template("summary.html", **_common({
        "period":           period,
        "total":            total,
        "income_total":     income_total,
        "net":              net,
        "cat_grand_total":  cat_grand_total,
        "bar_data":         bar_data,
        "max_bar":          max_bar,
        "view_month_label": view_month_label if period == "month" else "",
        "view_year":        view_year,
        "view_month":       view_month,
        "prev_month_url":   _month_url("month", prev_y, prev_m) if period == "month" else None,
        "next_month_url":   _month_url("month", next_y, next_m) if (period == "month" and not is_current_month) else None,
        "cat_totals":       cat_totals_sorted,
        "cat_counts":       cat_counts,
        "mom_diff":         mom_diff,
        "prev_month_name":  prev_month_name,
        "recurring":        recurring,
        "period_expenses":  period_expenses,
        "summary_back_url": summary_back_url,
    }))


# ── Category detail ───────────────────────────────────────────────────────────

@app.route("/category/<path:name>")
def category_view(name):
    expenses = load_expenses()
    month_param = request.args.get("month", "").strip()
    now = datetime.now()
    view_year, view_month = now.year, now.month
    if month_param:
        try:
            vd = datetime.strptime(month_param, "%Y-%m")
            view_year, view_month = vd.year, vd.month
        except ValueError:
            pass

    indexed = [{**e, "_index": i} for i, e in enumerate(expenses)]
    is_income_cat = (name == "Income")
    if month_param:
        prefix = f"{view_year}-{view_month:02d}"
        if is_income_cat:
            cat_exps = [e for e in indexed
                        if e["date"].startswith(prefix)
                        and e.get("type") == "income"]
        else:
            cat_exps = [e for e in indexed
                        if e["date"].startswith(prefix)
                        and e.get("category") == name
                        and e.get("type") != "income"]
        back_url = f"/summary?period=month&month={view_year}-{view_month:02d}"
        month_label = f"{FULL_MONTH_NAMES[view_month-1]} {view_year}"
    else:
        if is_income_cat:
            cat_exps = [e for e in indexed if e.get("type") == "income"]
        else:
            cat_exps = [e for e in indexed
                        if e.get("category") == name
                        and e.get("type") != "income"]
        back_url = "/summary?period=month"
        month_label = "All time"

    cat_exps = list(reversed(cat_exps))
    cat_total = sum(e["amount"] for e in cat_exps)
    self_url = f"/category/{name}?month={view_year}-{view_month:02d}" if month_param else f"/category/{name}"

    return render_template("category.html", **_common({
        "name":        name,
        "expenses":    cat_exps,
        "cat_total":   cat_total,
        "month_label": month_label,
        "back_url":    back_url,
        "self_url":    self_url,
    }))


# ── P&L ──────────────────────────────────────────────────────────────────────

@app.route("/pnl")
def pnl():
    month_param = request.args.get("month", "").strip()
    now = datetime.now()
    view_year, view_month = now.year, now.month
    if month_param:
        try:
            vd = datetime.strptime(month_param, "%Y-%m")
            view_year, view_month = vd.year, vd.month
        except ValueError:
            pass

    prefix = f"{view_year}-{view_month:02d}"
    expenses = load_expenses()
    in_range = [e for e in expenses if e["date"].startswith(prefix)]

    expense_items = [e for e in in_range if e.get("type") != "income"]
    income_items  = [e for e in in_range if e.get("type") == "income"]

    expenses_total = sum(e["amount"] for e in expense_items)
    income_total   = sum(e["amount"] for e in income_items)
    net = income_total - expenses_total

    cat_totals = {}
    for e in expense_items:
        c = e["category"]
        cat_totals[c] = cat_totals.get(c, 0) + e["amount"]

    income_by_note = {}
    for e in income_items:
        note = (e.get("note") or "").strip() or "Other income"
        income_by_note[note] = income_by_note.get(note, 0) + e["amount"]

    month_label = f"{FULL_MONTH_NAMES[view_month - 1]} {view_year}"
    back_url    = f"/summary?period=month&month={view_year}-{view_month:02d}"
    self_url    = f"/pnl?month={view_year}-{view_month:02d}"

    # Prev/next month nav
    if view_month == 1:
        prev_y, prev_m = view_year - 1, 12
    else:
        prev_y, prev_m = view_year, view_month - 1
    if view_month == 12:
        next_y, next_m = view_year + 1, 1
    else:
        next_y, next_m = view_year, view_month + 1
    is_current = (view_year == now.year and view_month == now.month)

    return render_template("pnl.html", **_common({
        "month_label":    month_label,
        "back_url":       back_url,
        "self_url":       self_url,
        "expenses_total": expenses_total,
        "income_total":   income_total,
        "net":            net,
        "cat_totals":     sorted(cat_totals.items(), key=lambda x: -x[1]),
        "income_by_note": sorted(income_by_note.items(), key=lambda x: -x[1]),
        "view_year":      view_year,
        "view_month":     view_month,
        "prev_url":       f"/pnl?month={prev_y}-{prev_m:02d}",
        "next_url":       f"/pnl?month={next_y}-{next_m:02d}" if not is_current else None,
    }))


# ── Budgets ───────────────────────────────────────────────────────────────────

@app.route("/budgets/set", methods=["POST"])
def set_budget():
    _ro = _readonly_block()
    if _ro: return _ro
    name = request.form.get("name", "").strip()
    raw  = request.form.get("amount", "").strip()
    if name and raw:
        try:
            budgets = load_budgets()
            budgets[name] = round(float(raw), 2)
            save_budgets(budgets)
        except ValueError:
            pass
    return redirect(url_for("categories_page"))


@app.route("/budgets/delete", methods=["POST"])
def delete_budget():
    _ro = _readonly_block()
    if _ro: return _ro
    name = request.form.get("name", "")
    budgets = load_budgets()
    budgets.pop(name, None)
    save_budgets(budgets)
    return redirect(url_for("categories_page"))


# ── Categories management ─────────────────────────────────────────────────────

@app.route("/categories")
def categories_page():
    expenses = load_expenses()
    cats = load_categories()
    now  = datetime.now()
    month_prefix = f"{now.year}-{now.month:02d}"

    stats = {}
    for e in expenses:
        if e.get("type") == "income":
            continue
        c = e["category"]
        if c not in stats:
            stats[c] = {"total": 0, "count": 0, "month": 0}
        stats[c]["total"] += e["amount"]
        stats[c]["count"] += 1
        if e["date"].startswith(month_prefix):
            stats[c]["month"] += e["amount"]

    return render_template("categories.html", **_common({
        "stats":         stats,
        "preset_colors": PRESET_COLORS,
        "preset_icons":  PRESET_ICONS,
    }))


@app.route("/categories/create", methods=["POST"])
def create_category():
    _ro = _readonly_block()
    if _ro: return _ro
    name  = request.form.get("name", "").strip()
    icon  = request.form.get("icon", "📦").strip()
    color = request.form.get("color", "#6b7280").strip()
    if not name:
        return redirect(url_for("categories_page"))
    cats = load_categories()
    if not any(c["name"] == name for c in cats):
        cats.append({"name": name, "icon": icon, "color": color})
        save_categories(cats)
    return redirect(url_for("categories_page"))


@app.route("/categories/delete", methods=["POST"])
def delete_category():
    _ro = _readonly_block()
    if _ro: return _ro
    name = request.form.get("name", "")
    cats = load_categories()
    cats = [c for c in cats if c["name"] != name or c["name"] == "Other"]
    save_categories(cats)
    expenses = load_expenses()
    for i, e in enumerate(expenses):
        if e.get("category") == name:
            update_expense(i, {"category": "Other"})
    return redirect(url_for("categories_page"))


# ── CSV import ────────────────────────────────────────────────────────────────

@app.route("/import", methods=["GET"])
def import_page():
    return render_template("import.html", **_common({"preview": None, "error": None}))


@app.route("/import", methods=["POST"])
def import_csv():
    _ro = _readonly_block()
    if _ro: return _ro
    f = request.files.get("csv_file")
    if not f or not f.filename:
        return render_template("import.html", **_common({"preview": None, "error": "Please choose a CSV file."}))
    try:
        file_bytes = f.read()
        return _process_import_bytes(file_bytes, f.filename.lower())
    except Exception as e:
        return render_template("import.html", **_common({"preview": None, "error": f"Could not parse file: {e}"}))


@app.route("/import/confirm", methods=["POST"])
def import_confirm():
    _ro = _readonly_block()
    if _ro: return _ro
    group  = request.form.get("group", "General").strip() or "General"
    keep   = set(request.form.getlist("keep"))
    amounts  = request.form.getlist("amount")
    notes    = request.form.getlist("note")
    cats_l   = request.form.getlist("category")
    dates    = request.form.getlist("date")
    types    = request.form.getlist("type")

    records = []
    for i, (amt, note, cat, date, typ) in enumerate(zip(amounts, notes, cats_l, dates, types)):
        if str(i) not in keep:
            continue
        try:
            record = {
                "date": date or datetime.now().isoformat(timespec="seconds"),
                "amount": round(float(amt), 2),
                "note": note, "category": cat, "group": group,
            }
            if typ == "income":
                record["type"] = "income"
            records.append(record)
        except ValueError:
            continue

    if records:
        bulk_save_expenses(records)
    return redirect(url_for("index", group=group, added=f"{len(records)} imported"))


# ── CSV helpers ───────────────────────────────────────────────────────────────

def _process_import_bytes(file_bytes, fname_lower):
    """Parse CSV or Excel bytes and render the import preview page."""
    try:
        if fname_lower.endswith((".xlsx", ".xls")):
            rows = _parse_excel(file_bytes)
        else:
            content = file_bytes.decode("utf-8-sig")
            content = _find_transaction_section(content)
            reader  = csv.DictReader(io.StringIO(content))
            headers = reader.fieldnames or []
            date_col, desc_col, amount_col = _detect_columns(headers)
            rows = []
            for row in reader:
                if amount_col:
                    amount, etype = _parse_amount_typed(row.get(amount_col, ""))
                else:
                    amount, etype = None, None
                if not amount:
                    continue
                note = (row.get(desc_col, "") or "").strip() if desc_col else ""
                rows.append({
                    "amount": amount,
                    "note": note,
                    "date": _parse_date(row.get(date_col, "")) if date_col else None,
                    "expense_type": etype,
                })

        if not rows:
            return render_template("import.html", **_common(
                {"preview": None, "error": "No expenses found — couldn't detect amount/description columns."}))

        # Duplicate detection: hash existing by (date, amount)
        existing = load_expenses()
        existing_hashes = set()
        for e in existing:
            existing_hashes.add((e["date"][:10], round(e["amount"], 2)))

        for row in rows:
            date_key = (row.get("date") or "")[:10]
            row["is_dupe"] = (date_key, round(row["amount"], 2)) in existing_hashes

        # Categorize only expense rows
        cats = load_categories()
        cm = {c["name"]: c for c in cats}
        fallback = cats[-1]["name"] if cats else "Other"

        expense_indices = [i for i, r in enumerate(rows) if r.get("expense_type") != "income"]
        income_indices  = [i for i, r in enumerate(rows) if r.get("expense_type") == "income"]

        if expense_indices:
            expense_batch = [rows[i] for i in expense_indices]
            categories = categorize_batch(expense_batch, categories=cats)
            for j, i in enumerate(expense_indices):
                cat = categories[j] if j < len(categories) else fallback
                rows[i]["category"] = cat
                rows[i]["icon"] = cm.get(cat, {}).get("icon", "📦")

        for i in income_indices:
            rows[i]["category"] = "Other"
            rows[i]["icon"] = "💰"

        return render_template("import.html", **_common({"preview": rows, "error": None}))

    except Exception as e:
        return render_template("import.html", **_common(
            {"preview": None, "error": f"Could not parse file: {e}"}))


def _parse_excel(file_bytes):
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    ws = wb.active
    rows_raw = list(ws.iter_rows(values_only=True))
    if not rows_raw:
        return []

    header_idx = 0
    for i, row in enumerate(rows_raw):
        cells = [str(c).lower() if c else "" for c in row]
        if any("date" in c for c in cells) and any("amount" in c or "debit" in c for c in cells):
            header_idx = i
            break

    headers = [str(c) if c is not None else "" for c in rows_raw[header_idx]]
    date_col, desc_col, amount_col = _detect_columns(headers)

    di = headers.index(date_col)  if date_col  and date_col  in headers else None
    ni = headers.index(desc_col)  if desc_col  and desc_col  in headers else None
    ai = headers.index(amount_col) if amount_col and amount_col in headers else None

    rows = []
    for row in rows_raw[header_idx + 1:]:
        if ai is None or ai >= len(row):
            continue
        amount, etype = _parse_amount_typed(str(row[ai]) if row[ai] is not None else "")
        if not amount:
            continue
        note = str(row[ni]).strip() if ni is not None and ni < len(row) and row[ni] else ""
        date_val = row[di] if di is not None and di < len(row) else None
        date_str = date_val.isoformat(timespec="seconds") if hasattr(date_val, "isoformat") else _parse_date(str(date_val) if date_val else "")
        rows.append({"amount": amount, "note": note, "date": date_str, "expense_type": etype})
    return rows


def _find_transaction_section(content):
    lines = content.splitlines()
    for i, line in enumerate(lines):
        low = line.lower()
        if ("date" in low or "posted" in low) and ("amount" in low or "debit" in low or "credit" in low):
            return "\n".join(lines[i:])
    return content


def _detect_columns(headers):
    h = [c.lower().strip() for c in headers]
    def find(exact, keywords):
        for c in exact:
            if c in h: return headers[h.index(c)]
        for i, col in enumerate(h):
            if any(k in col for k in keywords): return headers[i]
        return None
    return (
        find(["date","transaction date","clearing date","posted date","post date"], ["date"]),
        find(["description","merchant","payee","memo","name","original description"], ["desc","merchant","payee","memo"]),
        find(["amount","amount (usd)","debit","transaction amount"], ["amount","debit"]),
    )


def _parse_amount(val):
    if not val: return None
    cleaned = str(val).replace("$","").replace(",","").replace(" ","").strip()
    if not cleaned or cleaned in ["-","+"]: return None
    try:
        v = float(cleaned)
        return abs(v) if v < 0 else None
    except ValueError:
        return None


def _parse_amount_typed(val):
    """Returns (abs_amount, 'expense'|'income') or (None, None)."""
    if not val: return None, None
    cleaned = str(val).replace("$","").replace(",","").replace(" ","").strip()
    if not cleaned or cleaned in ["-","+"]: return None, None
    try:
        v = float(cleaned)
        if v < 0:
            return abs(v), "expense"
        elif v > 0:
            return v, "income"
        return None, None
    except ValueError:
        return None, None


def _parse_date(val):
    if not val: return None
    for fmt in ("%Y-%m-%d","%m/%d/%Y","%m/%d/%y","%d/%m/%Y","%Y/%m/%d"):
        try: return datetime.strptime(val.strip(), fmt).isoformat(timespec="seconds")
        except ValueError: continue
    return None


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
