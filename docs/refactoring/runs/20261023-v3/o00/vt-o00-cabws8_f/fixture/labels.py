def normalize_labels(items):
    if not isinstance(items, list):
        raise TypeError("items must be a list of str")
    seen = set()
    result = []
    for item in items:
        if not isinstance(item, str):
            raise TypeError("items must be a list of str")
        stripped = item.strip()
        if not stripped:
            continue
        key = stripped.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(stripped)
    return result
