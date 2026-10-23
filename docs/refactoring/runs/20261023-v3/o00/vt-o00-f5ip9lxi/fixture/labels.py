def normalize_labels(items):
    if not isinstance(items, list):
        raise TypeError("items must be a list")
    seen = set()
    result = []
    for item in items:
        if not isinstance(item, str):
            raise TypeError("items must be a list of str")
        trimmed = item.strip()
        if not trimmed:
            continue
        key = trimmed.casefold()
        if key not in seen:
            seen.add(key)
            result.append(trimmed)
    return result
