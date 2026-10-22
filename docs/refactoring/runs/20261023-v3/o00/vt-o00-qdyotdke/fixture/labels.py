def normalize_labels(items):
    if not isinstance(items, list):
        raise TypeError("items must be a list of strings")
    result = []
    seen = set()
    for item in items:
        if not isinstance(item, str):
            raise TypeError("all items must be strings")
        trimmed = item.strip()
        if not trimmed:
            continue
        key = trimmed.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(trimmed)
    return result
