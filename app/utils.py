import json

def get_categories_list(categories_raw):
    """Декодирует категории из JSON или старого текстового формата"""
    if not categories_raw:
        return []
    try:
        data = json.loads(categories_raw)
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    
    # Резервный разбор для старых записей
    return [c.strip() for c in categories_raw.split(',') if c.strip()]