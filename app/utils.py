import json
from collections import defaultdict

from app.models import ReceivedLog, QSO

# Алиасы видов модуляции: разные программы (Ермак, логгеры Cabrillo) могут
# записать один и тот же вид по-разному (PH/PHONE/USB/LSB вместо SSB).
# Приводим к каноническому значению, используемому в конструкторе.
MODE_ALIASES = {
    'PH': 'SSB',
    'PHONE': 'SSB',
    'USB': 'SSB',
    'LSB': 'SSB',
}

def normalize_mode(mode):
    """Приводит вид модуляции к каноническому значению (SSB, CW, FM, ...)."""
    m = str(mode).strip().upper()
    return MODE_ALIASES.get(m, m)


def get_categories_list(categories_raw):
    """Декодирует НАЗВАНИЯ зачетных групп из JSON или старого текстового формата.
    Для полного разбора (с диапазонами и видами модуляции) используйте parse_categories."""
    return [c['name'] for c in parse_categories(categories_raw)]


def parse_categories(categories_raw):
    """Декодирует зачетные группы в единый формат:
    [{'name': str, 'bands': [...], 'modes': [...]}, ...]

    Пустые bands/modes означают «без ограничений» (допускаются все
    диапазоны/виды модуляции). Поддерживает старый формат данных —
    список строк-названий групп.
    """
    if not categories_raw:
        return []
    try:
        data = json.loads(categories_raw)
    except (json.JSONDecodeError, TypeError):
        data = None

    result = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                name = str(item.get('name', '')).strip()
                bands = [str(b).strip() for b in item.get('bands', []) if str(b).strip()]
                modes = [str(m).strip() for m in item.get('modes', []) if str(m).strip()]
            else:
                name = str(item).strip()
                bands, modes = [], []
            if name:
                result.append({'name': name, 'bands': bands, 'modes': modes})
        return result

    # Резервный разбор для старых записей (текстовый список через запятую)
    return [{'name': c.strip(), 'bands': [], 'modes': []}
            for c in categories_raw.split(',') if c.strip()]


def category_rules_map(categories_raw):
    """Словарь зачетных групп: имя -> {'bands': set, 'modes': set}.
    Пустое множество означает «без ограничений» (допускаются все
    диапазоны/виды модуляции)."""
    rules = {}
    for c in parse_categories(categories_raw):
        rules[c['name']] = {
            'bands': {b.lower() for b in c['bands']},
            'modes': {normalize_mode(m) for m in c['modes']},
        }
    return rules


def out_of_category_reason(rules, band, mode):
    """Если связь на диапазоне/виде модуляции выходит за рамки зачетной группы
    участника, возвращает текстовую причину для UBN-протокола, иначе None.

    Правила:
    - только диапазон вне зачета -> 'вне зачета (заявленный диапазон)'
    - только вид модуляции вне зачета -> 'вне зачета (заявленный вид модуляции)'
    - и то и другое -> 'вне зачета (заявленный диапазон/заявленный вид модуляции)'
    """
    if not rules:
        return None
    allowed_bands = rules.get('bands') or set()
    allowed_modes = rules.get('modes') or set()
    band_out = bool(allowed_bands) and str(band).strip().lower() not in allowed_bands
    mode_out = bool(allowed_modes) and normalize_mode(mode) not in allowed_modes
    if not band_out and not mode_out:
        return None
    labels = []
    if band_out:
        labels.append('заявленный диапазон')
    if mode_out:
        labels.append('заявленный вид модуляции')
    return 'вне зачета (' + '/'.join(labels) + ')'

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


def get_grouped_results(comp):
    """Группирует официальные отчеты соревнования по категориям в порядке
    конструктора. Учитываются только последние (официальные) отчеты — дубли
    не попадают в протокол. Сортировка внутри группы: по подтвержденным очкам,
    подтвержденным и заявленным связям (по убыванию)."""
    grouped = defaultdict(list)
    for cat in get_categories_list(comp.categories):
        grouped[cat] = []

    for log in get_official_logs(comp.id):
        cat = log.category if log.category in grouped else (log.category or 'Без категории')
        grouped[cat].append(log)

    for cat in grouped:
        grouped[cat].sort(
            key=lambda x: (
                x.score if x.score is not None else 0,
                x.confirmed_qsos if x.confirmed_qsos is not None else 0,
                x.claimed_qsos if x.claimed_qsos is not None else 0,
            ),
            reverse=True,
        )
    return dict(grouped)