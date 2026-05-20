# Expense Tracker — CLAUDE.md

## Run the app
```bash
cd ~/expense-tracker
python3 app.py          # http://localhost:5000
```
Verify no syntax errors before every change: `python3 -c "import app"`

---

## Stack
- **Python 3.9.6** — no walrus operator (`:=`), no `match` statements
- **Flask** — Jinja2 server-rendered templates, no JS framework
- **Anthropic SDK** — `categorizer.py` calls Claude for expense categorisation
- **Storage** — flat JSON files, no database

---

## Project structure
```
app.py              # All routes
categorizer.py      # Claude API calls (single + batch)
storage.py          # Read/write for all JSON files
templates/
  index.html        # Main expenses page (add + list)
  summary.html      # Summary tabs: Daily/Week/Month/Year/Calendar
  day.html          # Single day drill-down
  category.html     # Single category drill-down
  pnl.html          # Profit & loss breakdown
  import.html       # CSV/Excel import preview + confirm
  categories.html   # Category + budget management
static/
  uploads/          # Receipt/PDF attachments
expenses.json       # All expense records
categories.json     # User-defined categories
budgets.json        # Monthly budget caps per category
```

---

## Data model

### Expense record
```json
{
  "date": "2026-05-19T14:32:00",
  "amount": 45.00,
  "note": "Whole Foods",
  "category": "Food",
  "group": "General",
  "type": "income"        // only present for income rows
}
```
`_index` is computed at render time — never stored. Income rows have `"type": "income"`.

### categories.json
```json
[{"name": "Food", "icon": "🍔", "color": "#f59e0b"}, ...]
```

### budgets.json
```json
{"Food": 500.0, "Transport": 150.0}
```

---

## Key patterns

### `_common()` helper
Always use `_common({...})` when calling `render_template`. It injects `categories`, `cat_map`, `groups`, `budgets` into every template. `cat_map` always includes a synthetic `"Income"` entry (`{"name":"Income","color":"#10b981","icon":"💰"}`).

### Indexed expenses
`_index` is added at view time, never stored:
```python
indexed = [{**e, "_index": i} for i, e in enumerate(expenses)]
```

### Inline edit pattern
All field edits use POST forms with `display:contents` and `onchange="this.form.submit()"`. A hidden `back` field returns the user to the right page after submission:
```html
<form method="post" action="/expense/{{ e._index }}/note" style="display:contents">
  <input type="hidden" name="back" value="/day/{{ date }}">
  <input type="text" name="note" ...>
</form>
```
Edit routes: `/expense/<i>/note`, `/expense/<i>/amount`, `/expense/<i>/category`, `/expense/<i>/date`.

### Category names in URLs
Category names can contain `/` (e.g. "Venmo/Zelle"). Use `<path:name>` on the category route. For delete routes with a suffix, pass `name` in the POST body instead of the URL.

### bar_data format
3-tuples: `(label, amount, link_or_none)`. The third element is a full URL string or `None`. Template iterates as `{% for label, amt, link in bar_data %}`.

### Import flow
`_parse_amount_typed(val)` returns `(abs_amount, 'expense'|'income')`. Income rows skip `categorize_batch()`, get `category="Other"`, `type="income"`. Duplicate detection uses `(date[:10], round(amount,2))` tuples against existing expenses.

### `_get_recurring_merchants()`
Merchants appearing in 3+ distinct calendar months. Returns top 8 sorted by month count.

---

## CSS rules — do not break these

### Input specificity fix
Global `input[type="text"]` and `input[type="number"]` rules have high specificity. Always use `input.classname` (element + class) for inline edit inputs, never just `.classname`:
```css
input.note-edit { ... }    /* correct */
input.amount-edit { ... }  /* correct */
.note-edit { ... }         /* WRONG — will be overridden */
```

### Theme variables
```
--bg: #0d0d10  --card: #1a1a22  --card2: #20202c
--accent: #8b5cf6  --accent2: #a78bfa
--green: #10b981  --red: #f43f5e  --muted: #5a5a78
```
Purple (`--accent`) is the primary accent — use sparingly. Green = income/positive. Red = expense/negative.

### Calendar cells
Use fixed `height: 46px` on `.cal-cell`, not `aspect-ratio: 1`. The aspect-ratio approach causes WebKit to not expand the parent card correctly.

### Entrance animations
All major cards use `animation: fadeUp 0.45s ease-out both` with staggered delays via `:nth-child`. Expense rows use `animation: itemIn 0.35s ease-out both`.

### Count-up animation
Starts at 82% of target, runs 400ms, ease-out quadratic. Never count from 0 — it looks clunky on large numbers.

---

## Route reference
| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Main expenses page |
| `/add` | POST | Add expense (also handles file import if CSV/XLSX attached) |
| `/expense/<i>/note` | POST | Inline edit note |
| `/expense/<i>/amount` | POST | Inline edit amount |
| `/expense/<i>/category` | POST | Inline edit category |
| `/expense/<i>/date` | POST | Inline edit date |
| `/delete/<i>` | POST | Delete expense |
| `/day/<date>` | GET | Day drill-down (`YYYY-MM-DD`) |
| `/summary` | GET | Summary (`?period=daily\|week\|month\|year\|calendar`, `?month=YYYY-MM`) |
| `/category/<path:name>` | GET | Category drill-down (`?month=YYYY-MM`) |
| `/pnl` | GET | P&L breakdown (`?month=YYYY-MM`) |
| `/categories` | GET | Category + budget management |
| `/categories/create` | POST | Create category |
| `/categories/delete` | POST | Delete category (name in POST body) |
| `/budgets/set` | POST | Set/update budget |
| `/budgets/delete` | POST | Delete budget (name in POST body) |
| `/import` | GET/POST | CSV/Excel import |
| `/import/confirm` | POST | Confirm import |

---

## Preferences
- No comments unless the WHY is non-obvious
- No new files unless required — edit existing ones
- Keep templates self-contained (styles inline in `<style>` blocks, no external CSS)
- Mobile-first: 480px max-width container, touch targets min 44px, `env(safe-area-inset-bottom)` on nav
- Income is always treated separately from expenses — never mixed into expense category totals
- Category drill-downs use `<path:name>` routing; delete actions use POST body for the name
- The `"Income"` pseudo-category is injected in `_common()` / `cat_map()`, never stored in `categories.json`
