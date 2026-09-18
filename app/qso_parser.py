import re
from datetime import datetime

# ---------------------------------------------------------------------------
# Строгий разбор строки QSO (формат Cabrillo/Ермак):
#   QSO: <частота> <вид> <дата> <время> <МОЙ_ПОЗЫВНОЙ> [переданный обмен]
#        <ПОЗЫВНОЙ_КОРРЕСПОНДЕНТА> [принятый обмен]
#
# Обмен (exchange) по умолчанию: RST + контрольный номер. Состав обмена
# задается плагином через QSO_EXCHANGE_SPEC (например {'sent': ['rst','nr'],
# 'rcvd': ['rst','nr']}).
#
# Отличия от «доброго» разбора по позициям:
#  * позывной корреспондента ищется как ПОСЛЕДНИЙ позывной-подобный токен
#    (устойчиво к локатору/мусору в середине строки);
#  * всё, что правее принятого контрольного номера, игнорируется (P-02, RT0O);
#  * если RST отсутствует там, где он должен быть (или есть там, где
#    не должен) — связь получает КРИТИЧЕСКУЮ ошибку формата (P-01, RN0JT),
#    отчёт с такими связями принимается только в CHECKLOG;
#  * слитые RST+номер (например «59021») — некритичное предупреждение
#    (P-02, RX0L), связь показывается как есть.
# ---------------------------------------------------------------------------

# Позывной-подобный токен: латиница/цифры/слеш, обязательно с буквой.
CALLSIGN_RE = re.compile(r'^[A-Z0-9/]{2,12}$')
# RST: 2-3 символа, первая цифра 1..5, допускается «N» (CW-проscript 5NN).
RST_RE = re.compile(r'^[1-5][0-9N]{0,2}$')
# Слитый RST+номер — длинная цифровая «каша» вроде 59021 / 599002.
MERGED_RE = re.compile(r'^\d{5,}$')

DEFAULT_EXCHANGE_SPEC = {'sent': ['rst', 'nr'], 'rcvd': ['rst', 'nr']}


def _is_callsign(token):
    t = str(token).strip().upper()
    if not CALLSIGN_RE.match(t):
        return False
    return bool(re.search(r'[A-Z]', t))


def _looks_merged(token):
    return bool(MERGED_RE.match(str(token).strip()))


def _align_exchange(fields, names, label):
    """Согласует фактические поля обмена с ожидаемым составом names.

    Возвращает (positions, errors), где positions — словарь имя->значение
    ('' для отсутствующих), errors — список (is_critical, label, text).
    """
    errors = []
    n, e = len(fields), len(names)

    if n == e:
        return dict(zip(names, fields)), errors

    if n > e:
        # Лишние токены. Для Cabrillo — «хвост» после принятого номера (RT0O),
        # для Ермака — блок локатора между обменами. Берём первые e полей
        # (если первое похоже на RST) либо последние e.
        if names and names[0] == 'rst' and e and RST_RE.match(str(fields[-e])):
            take, extra = fields[-e:], fields[:-e]
        else:
            take, extra = fields[:e], fields[e:]
        if extra:
            errors.append((
                False,
                "Лишние данные",
                f"Лишние данные ({', '.join(extra)}) в {label} обмене — игнорируются"
            ))
        return dict(zip(names, take)), errors

    # n < e: полей меньше, чем ожидается.
    if n == 1 and names and 'rst' in names and _looks_merged(fields[0]):
        # Слитые RST и контрольный номер (например «59021») — небрежность
        # оператора, показываем токен как есть, без блокировки отчёта.
        errors.append((
            False,
            "Слиты RST+номер",
            f"Слиты RST и контрольный номер в {label} обмене: '{fields[0]}'"
        ))
        values = {name: '' for name in names}
        values[names[-1]] = str(fields[0])
        return values, errors

    values = {name: '' for name in names}
    if not fields:
        missing = names
    elif names and names[0] == 'rst' and RST_RE.match(str(fields[0])):
        # Первое поле — RST, дальше (до e) идут номера.
        for i, val in enumerate(fields):
            if i < e:
                values[names[i]] = val
        missing = names[min(n, e):]
    else:
        # Поля сдвинуты к концу: значит RST отсутствует, а это номер(а).
        start = e - n
        for i, val in enumerate(fields):
            if start + i < e:
                values[names[start + i]] = val
        missing = names[:start]

    for name in missing:
        errors.append((
            True,
            f"Нет '{name}'",
            f"Отсутствует поле '{name}' в {label} обмене"
        ))
    return values, errors


def _parse_datetime(date_str, time_str):
    if len(time_str) == 4:
        fmt = "%Y-%m-%d %H%M"
    else:
        fmt = "%Y-%m-%d %H:%M"
    return datetime.strptime(f"{date_str} {time_str}", fmt)


def parse_qso_line(tokens, exchange_spec=None):
    """Строгий разбор одной строки QSO.

    tokens — строка, разбитая по пробелам (первым токеном может быть
    'QSO:' или 'QSO'). exchange_spec — состав обмена (по умолчанию
    RST + номер на обеих сторонах).

    Возвращает словарь связь или None (в строке нет позывного корреспондента —
    это не связь). Всегда присутствуют ключи 'format_errors' (список сообщений)
    и 'has_critical_format' (есть ли критическое нарушение формата).
    """
    spec = exchange_spec or DEFAULT_EXCHANGE_SPEC
    sent_names = [str(x).lower() for x in (spec.get('sent') or [])]
    rcvd_names = [str(x).lower() for x in (spec.get('rcvd') or [])]

    if not tokens:
        return None
    first = str(tokens[0]).strip().upper()
    if first == 'QSO' or first.startswith('QSO:'):
        tokens = tokens[1:]
    if len(tokens) < 6:
        return None

    freq, mode, date_str, time_str = tokens[0], tokens[1], tokens[2], tokens[3]
    my_call = str(tokens[4]).strip().upper()
    remaining = [str(t).strip().upper() for t in tokens[5:]]

    # Позывной корреспондента — последний позывной-подобный токен.
    corr_idx = None
    for i in range(len(remaining) - 1, -1, -1):
        if _is_callsign(remaining[i]):
            corr_idx = i
            break
    if corr_idx is None:
        return None

    corr_call = remaining[corr_idx]
    sent_fields = remaining[:corr_idx]
    rcvd_fields = remaining[corr_idx + 1:]

    sent_vals, sent_errors = _align_exchange(sent_fields, sent_names, 'переданном')
    rcvd_vals, rcvd_errors = _align_exchange(rcvd_fields, rcvd_names, 'принятом')
    format_errors = sent_errors + rcvd_errors

    # Контроль «RST есть там, где его быть не должно».
    for name, vals, label in ((sent_names, sent_vals, 'переданном'),
                              (rcvd_names, rcvd_vals, 'принятом')):
        if 'rst' not in name and vals.get('rst'):
            format_errors.append((True, "RST не предусмотрен",
                                  f"RST указан в {label} обмене, но не предусмотрен"))

    dt = None
    try:
        dt = _parse_datetime(date_str, time_str)
    except (ValueError, TypeError):
        format_errors.append((True, "Неверная дата/время",
                              f"Неверная дата/время: {date_str} {time_str}"))

    if not _is_callsign(my_call):
        format_errors.append((True, "Нет позывного",
                              "Отсутствует или неверен позывной (мой)"))

    has_critical = any(critical for critical, _, _ in format_errors)

    return {
        'freq': freq,
        'mode_raw': mode,
        'date': date_str,
        'time': time_str,
        'my_call': my_call,
        'his_call': corr_call,
        'rst_s': sent_vals.get('rst', ''),
        'exch_s': sent_vals.get('nr', ''),
        'rst_r': rcvd_vals.get('rst', ''),
        'exch_r': rcvd_vals.get('nr', ''),
        'dt': dt,
        'format_errors': [
            {'critical': c, 'label': lbl, 'text': txt}
            for c, lbl, txt in format_errors
        ],
        'has_critical_format': has_critical,
    }