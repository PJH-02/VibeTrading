def normalize_labels(items):
    if not isinstance(items, list):
        raise TypeError("Input must be a list")
    
    result = []
    seen = set()
    
    for item in items:
        if not isinstance(item, str):
            raise TypeError("All elements must be strings")
        
        trimmed = item.strip()
        if not trimmed:
            continue
            
        key = trimmed.casefold()
        if key not in seen:
            seen.add(key)
            result.append(trimmed)
    
    return result