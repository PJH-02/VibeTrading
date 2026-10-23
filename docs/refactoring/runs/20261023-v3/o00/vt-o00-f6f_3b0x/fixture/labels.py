import collections

def normalize_labels(items: list[str]) -> list[str]:
    if not isinstance(items, list):
        raise TypeError("Input must be a list")

    seen = collections.OrderedDict()
    result = []
    for item in items:
        if not isinstance(item, str):
            raise TypeError("List elements must be strings")
        trimmed_item = item.strip()
        if trimmed_item:
            casefolded_item = trimmed_item.casefold()
            if casefolded_item not in seen:
                seen[casefolded_item] = None  # Store to maintain order
                result.append(trimmed_item)
    return result
