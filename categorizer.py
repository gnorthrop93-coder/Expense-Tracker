import json
import re
import anthropic

_client = anthropic.Anthropic()


def _get_cat_names(categories=None):
    if categories:
        return [c["name"] for c in categories]
    from storage import load_categories
    return [c["name"] for c in load_categories()]


def categorize(amount, note, file_data=None, file_type=None, categories=None):
    cats = _get_cat_names(categories)
    if file_data:
        if file_type == "application/pdf":
            return _categorize_document(amount, note, file_data, cats)
        return _categorize_image(amount, note, file_data, file_type, cats)
    return _categorize_text(amount, note, cats)


def categorize_batch(rows, categories=None):
    cats = _get_cat_names(categories)
    if not rows:
        return []

    # Process in chunks of 80 to keep prompts manageable
    CHUNK = 80
    if len(rows) > CHUNK:
        result = []
        for start in range(0, len(rows), CHUNK):
            result.extend(categorize_batch(rows[start:start + CHUNK], categories=categories))
        return result

    lines = "\n".join(
        f"{i+1}. ${r['amount']:.2f} — {r['note'] or 'no description'}"
        for i, r in enumerate(rows)
    )

    msg = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=min(len(rows) * 25 + 100, 4096),
        system=(
            f"Categorize each expense. Categories: {', '.join(cats)}. "
            "Reply with ONLY a JSON array of category strings in the same order as the input. "
            f'Example for 3 items: ["{cats[0]}", "{cats[1] if len(cats)>1 else cats[0]}", "{cats[0]}"]'
        ),
        messages=[{"role": "user", "content": lines}],
    )

    text = msg.content[0].text.strip()
    try:
        # Greedy match to capture the outermost [...] block
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            result = json.loads(match.group())
            cleaned = [c if c in cats else cats[-1] for c in result]
            # Pad or truncate to exactly match input length
            if len(cleaned) < len(rows):
                cleaned += [cats[-1]] * (len(rows) - len(cleaned))
            return cleaned[:len(rows)]
    except (json.JSONDecodeError, ValueError):
        pass
    return [cats[-1]] * len(rows)


def _categorize_text(amount, note, cats):
    context = f"${amount:.2f}"
    if note:
        context += f" — {note}"
    msg = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=15,
        system=f"Categorize expenses. Reply with exactly one word from: {', '.join(cats)}. Nothing else.",
        messages=[{"role": "user", "content": context}],
    )
    word = msg.content[0].text.strip().rstrip(".")
    return {"category": word if word in cats else cats[-1], "amount": None}


def _categorize_image(amount, note, image_data, image_type, cats):
    need_amount = not amount or amount <= 0
    system, user_text = _vision_prompt(amount, note, cats, need_amount)
    msg = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=60,
        system=system,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": image_type, "data": image_data}},
            {"type": "text", "text": user_text},
        ]}],
    )
    return _parse_vision_response(msg.content[0].text.strip(), need_amount, cats)


def _categorize_document(amount, note, pdf_data, cats):
    need_amount = not amount or amount <= 0
    system, user_text = _vision_prompt(amount, note, cats, need_amount)
    msg = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=60,
        system=system,
        messages=[{"role": "user", "content": [
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_data}},
            {"type": "text", "text": user_text},
        ]}],
    )
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
