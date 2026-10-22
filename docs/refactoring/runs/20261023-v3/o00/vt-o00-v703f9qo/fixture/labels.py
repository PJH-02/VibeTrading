def normalize_labels(items):
    if not isinstance(items, list):
        raise TypeError("Expected list[str]")
    seen = set()
    result = []
    for item in items:
        if not isinstance(item, str):
            raise TypeError("Expected list[str]")
        trimmed = item.strip()
        if not trimmed:
            continue
        folded = trimmed.casefold()
        if folded not in seen:
            seen.add(folded)
            result.append(trimmed)
    return result