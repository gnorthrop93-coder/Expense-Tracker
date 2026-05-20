import json
import re
import time
import anthropic

_client = anthropic.Anthropic(max_retries=0)

# ── Keyword matching (no API needed) ─────────────────────────────────────────

_KEYWORDS = [
    ("Transport",     ["uber", "lyft", "taxi", "transit", "ventra", "mta ", "bart ",
                       "cta ", "metro", "parking", "chevron", "shell", "exxon", "bp ",
                       "mobil", "arco", "gas station", "airline", "delta ", "united ",
                       "southwest", "american air", "jetblue", "spirit air", "amtrak",
                       "hertz", "enterprise rent", "avis ", "zipcar", "bird ", "lime "]),
    ("Groceries",     ["wholefds", "whole foods", "trader joe", "safeway", "kroger",
                       "vons", "ralphs", "aldi", "sprouts", "publix", "wegmans",
                       "heb ", "costco", "sam's club", "market", "grocery"]),
    ("Food",          ["doordash", "uber eat", "grubhub", "instacart", "seamless",
                       "postmates", "tst*", "sq *", "toast", "restaurant", "pizza",
                       "mcdonald", "starbucks", "chipotle", "subway ", "dunkin",
                       "domino", "taco bell", "chick-fil", "panera", "in-n-out",
                       "five guys", "shake shack", "sweetgreen", "bbq", "sushi",
                       "bagel", "cafe ", "diner", "grill", "kitchen", "eatery",
                       "bistro", "tavern", "brewery", "food", "l & m fine"]),
    ("Health",        ["pharmacy", "cvs", "walgreens", "rite aid", "medical",
                       "dental", "doctor", "clinic", "hospital", "urgent care",
                       "optometrist", "vision", "therapist", "fitness", "gym",
                       "24 hour fitness", "planet fitness", "equinox", "la fitness",
                       "radiology", "beverly radiology", "activepitch"]),
    ("Entertainment", ["spotify", "netflix", "hulu", "disney", "hbo", "apple.com/bill",
                       "itunes", "xbox", "playstation", "steam", "twitch", "youtube",
                       "ticketmaster", "eventbrite", "amc ", "cinemark", "fandango",
                       "airbnb", "vrbo", "hotel", "marriott", "hilton", "hyatt",
                       "microsoft*xbox", "nintendo", "chicago athletic"]),
    ("Shopping",      ["amazon", "etsy", "ebay", "shopify", "target", "walmart",
                       "best buy", "apple store", "nordstrom", "macy", "gap ",
                       "zara", "h&m", "old navy", "banana republic", "anthropologie"]),
    ("Utilities",     ["spectrum", "comcast", "at&t", "verizon", "t-mobile",
                       "electric", "pg&e", "con ed", "water ", "internet", "phone ",
                       "frontier ai", "openai", "chatgpt", "adobe", "dropbox",
                       "google ", "microsoft*", "apple one", "icloud", "cf united",
                       "city of santa monica"]),
    ("Venmo/Zelle",   ["venmo", "zelle"]),
]


def _keyword_match(note, cats):
    """Return category name if note matches a keyword, else None."""
    if not note:
        return None
    n = note.lower()
    for cat_name, keywords in _KEYWORDS:
        if cat_name in cats and any(k in n for k in keywords):
            return cat_name
    return None


# ── API helpers ───────────────────────────────────────────────────────────────

def _api_call(fn, retries=5, wait=8):
    for attempt in range(retries):
        try:
            return fn()
        except anthropic.APIStatusError as e:
            if e.status_code == 529 and attempt < retries - 1:
                time.sleep(wait)
            else:
                raise


def _get_cat_names(categories=None):
    if categories:
        return [c["name"] for c in categories]
    from storage import load_categories
    return [c["name"] for c in load_categories()]


# ── Public API ────────────────────────────────────────────────────────────────

def categorize(amount, note, file_data=None, file_type=None, categories=None):
    cats = _get_cat_names(categories)
    if file_data:
        if file_type == "application/pdf":
            return _categorize_document(amount, note, file_data, cats)
        return _categorize_image(amount, note, file_data, file_type, cats)
    # Try keyword match first
    matched = _keyword_match(note, cats)
    if matched:
        return {"category": matched, "amount": None}
    return _categorize_text(amount, note, cats)


def categorize_batch(rows, categories=None):
    cats = _get_cat_names(categories)
    if not rows:
        return []

    # Phase 1: keyword match everything we can locally
    results = [None] * len(rows)
    unmatched_indices = []
    for i, row in enumerate(rows):
        matched = _keyword_match(row.get("note", ""), cats)
        if matched:
            results[i] = matched
        else:
            unmatched_indices.append(i)

    # Phase 2: send only unmatched rows to the API in chunks of 50
    if unmatched_indices:
        CHUNK = 50
        for start in range(0, len(unmatched_indices), CHUNK):
            chunk_indices = unmatched_indices[start:start + CHUNK]
            chunk_rows = [rows[i] for i in chunk_indices]
            chunk_cats = _api_categorize(chunk_rows, cats)
            for j, i in enumerate(chunk_indices):
                results[i] = chunk_cats[j]

    fallback = cats[-1]
    return [r if r else fallback for r in results]


def _api_categorize(rows, cats):
    """Call Claude to categorize a list of rows. Returns list of category strings."""
    lines = "\n".join(
        f"{i+1}. ${r['amount']:.2f} — {r.get('note') or 'no description'}"
        for i, r in enumerate(rows)
    )
    try:
        msg = _api_call(lambda: _client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=min(len(rows) * 25 + 100, 4096),
            system=(
                f"Categorize each expense. Categories: {', '.join(cats)}. "
                "Reply with ONLY a JSON array of category strings in the same order. "
                f'Example for 3 items: ["{cats[0]}", "{cats[1] if len(cats) > 1 else cats[0]}", "{cats[0]}"]'
            ),
            messages=[{"role": "user", "content": lines}],
        ))
        text = msg.content[0].text.strip()
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            cleaned = [c if c in cats else cats[-1] for c in parsed]
            if len(cleaned) < len(rows):
                cleaned += [cats[-1]] * (len(rows) - len(cleaned))
            return cleaned[:len(rows)]
    except Exception:
        pass
    return [cats[-1]] * len(rows)


# ── Single-item helpers ───────────────────────────────────────────────────────

def _categorize_text(amount, note, cats):
    context = f"${amount:.2f}"
    if note:
        context += f" — {note}"
    msg = _api_call(lambda: _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=15,
        system=f"Categorize expenses. Reply with exactly one word from: {', '.join(cats)}. Nothing else.",
        messages=[{"role": "user", "content": context}],
    ))
    word = msg.content[0].text.strip().rstrip(".")
    return {"category": word if word in cats else cats[-1], "amount": None}


def _categorize_image(amount, note, image_data, image_type, cats):
    need_amount = not amount or amount <= 0
    system, user_text = _vision_prompt(amount, note, cats, need_amount)
    msg = _api_call(lambda: _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=60,
        system=system,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": image_type, "data": image_data}},
            {"type": "text", "text": user_text},
        ]}],
    ))
    return _parse_vision_response(msg.content[0].text.strip(), need_amount, cats)


def _categorize_document(amount, note, pdf_data, cats):
    need_amount = not amount or amount <= 0
    system, user_text = _vision_prompt(amount, note, cats, need_amount)
    msg = _api_call(lambda: _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=60,
        system=system,
        messages=[{"role": "user", "content": [
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_data}},
            {"type": "text", "text": user_text},
        ]}],
    ))
    return _parse_vision_response(msg.content[0].text.strip(), need_amount, cats)


def _vision_prompt(amount, note, cats, need_amount):
    if need_amount:
        system = (
            f"Analyze this receipt. Extract the total and categorize. Categories: {', '.join(cats)}. "
            'Reply JSON only: {"category": "Food", "amount": 12.50}'
        )
        user_text = "Extract total and categorize."
    else:
        system = f"Categorize this expense. Reply with exactly one word from: {', '.join(cats)}. Nothing else."
        parts = [f"Amount: ${amount:.2f}"]
        if note:
            parts.append(f"Note: {note}")
        user_text = ". ".join(parts) + ". Categorize."
    return system, user_text


def _parse_vision_response(text, need_amount, cats):
    if need_amount:
        try:
            match = re.search(r'\{.*?\}', text, re.DOTALL)
            if match:
                data = json.loads(match.group())
                cat = data.get("category", cats[-1])
                extracted = float(data.get("amount", 0))
                return {"category": cat if cat in cats else cats[-1], "amount": extracted or None}
        except (json.JSONDecodeError, ValueError):
            pass
        return {"category": cats[-1], "amount": None}
    word = text.rstrip(".").strip()
    return {"category": word if word in cats else cats[-1], "amount": None}
