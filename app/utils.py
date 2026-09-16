import json
from app.models import ReceivedLog, QSO

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

def full_exchange(rst, nr):
    """Полный контрольный номер связи: RST + номер (например '599 012')."""
    return f"{rst} {nr}".strip()


def get_qso_orders(comp_id):
    """Порядковые номера QSO в отчетах: {log_id: {qso_id: номер_связи}}.
    Номер считается в порядке внесения в базу (соответствует порядку строк в отчете)."""
    rows = QSO.query.filter_by(competition_id=comp_id).order_by(QSO.log_id, QSO.id).all()
    result = {}
    for q in rows:
        bucket = result.setdefault(q.log_id, {})
        bucket[q.id] = len(bucket) + 1
    return result


def get_official_logs(comp_id):
    """
    Возвращает по одному "официальному" отчету на позывной:
    самый последний по времени загрузки (остальные считаются дублями
    и учитываются только до их удаления администратором).
    """
    logs = ReceivedLog.query.filter_by(competition_id=comp_id).order_by(
        ReceivedLog.upload_time.desc(), ReceivedLog.id.desc()
    ).all()

    seen = set()
    official = []
    for log in logs:
        key = (log.callsign or '').upper()
        if key not in seen:
            seen.add(key)
            official.append(log)
    return official