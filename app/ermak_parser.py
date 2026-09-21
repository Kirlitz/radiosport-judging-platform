import re

from app.qso_parser import parse_qso_line

# Карта замены визуально одинаковых кириллических букв на латинские
HOMOGLYPHS = {
    'А': 'A', 'В': 'B', 'С': 'C', 'Е': 'E', 'Н': 'H',
    'К': 'K', 'М': 'M', 'О': 'O', 'Р': 'P', 'Т': 'T',
    'Х': 'X', 'У': 'Y'
}

def normalize_header_key(raw_key):
    cleaned = raw_key.replace('\x00', '').replace('\ufeff', '').replace('\u200b', '').strip().upper()
    latin_key = "".join(HOMOGLYPHS.get(ch, ch) for ch in cleaned)
    
    if latin_key in ['LOC', 'GRID', 'GRID-SQUARE', 'WWL', 'QTH']:
        return 'LOCATION'
    return latin_key


def freq_to_band(freq_str):
    try:
        f = float(freq_str)
        if f < 100: 
            f = f * 1000
        
        if 1800 <= f <= 2000: return '160m'
        if 3500 <= f <= 3800: return '80m'
        if 7000 <= f <= 7300: return '40m'
        if 14000 <= f <= 14350: return '20m'
        if 21000 <= f <= 21450: return '15m'
        if 28000 <= f <= 29700: return '10m'
        if 144000 <= f <= 146000 or 144 <= f <= 146: return '2m'
        if 430000 <= f <= 440000 or 430 <= f <= 440: return '70cm'
    except Exception:
        pass
    return str(freq_str).lower().strip()


_SINGLE_DOB_RE = re.compile(r'^\d{1,2}[./-]\d{1,2}[./-]\d{2,4}$')
_YEAR_RE = re.compile(r'^\d{4}$')
_KNOWN_RANKS = {'МС', 'КМС', 'ЗМС', 'МСМК', 'Б/Р', 'БР', '1', '2', '3',
                '1К', '2К', '3К', 'I', 'II', 'III'}

def _is_callsign_like(token):
    t = str(token).strip()
    return bool(re.match(r'^[A-Z0-9/]{2,12}$', t) and re.search(r'[A-Z]', t))

def _strip_sep(token):
    return re.sub(r'^[,.;:\s]+|[,.;:\s]+$', '', str(token))


def parse_operator_line(line_val):
    """Разбор данных об операторе в формате Ермак/Cabrillo.

    Поддерживает варианты:
      Фамилия, Имя, Отчество, ГГГГ, разряд, позывной, категория   (Ермак)
      Фамилия, Имя, Отчество, ДД.ММ.ГГГГ, разряд, позывной, категория  (Cabrillo/5MContest)
      UA8AA UA8BA UA8AC @UA8XYZ                                 (список позывных)
    Допускается отсутствие отдельных полей и завершающий идентификатор «Тренер».
    Точка внутри даты НЕ разделяет поля, поэтому дата не разбивается на части.
    """
    if not line_val or not str(line_val).strip():
        return None

    raw = str(line_val).strip()
    try:
        tokens = [t for t in (_strip_sep(t) for t in re.split(r'[\s,]+', raw)) if t]
        if not tokens:
            return None

        fio_parts = []
        dob = ""
        rank = ""
        callsign = ""
        category = ""
        extra_calls = []
        remainder = []

        dob_idx = -1
        for i, t in enumerate(tokens):
            if _SINGLE_DOB_RE.match(t) or _YEAR_RE.match(t):
                dob_idx = i
                break

        if dob_idx != -1:
            fio_parts = tokens[:dob_idx]
            dob = tokens[dob_idx]
            remainder = tokens[dob_idx + 1:]
        else:
            # Возможный список позывных операторов (формат 1 Ермака,
            # например «UA8AA UA8BA UA8AC @UA8XYZ»). Токен @<позывной>
            # обозначает подпозывной станции и оператором не является.
            call_candidates = [t for t in tokens if not t.startswith('@')]
            if call_candidates and all(_is_callsign_like(t) for t in call_candidates):
                callsign = call_candidates[0]
                extra_calls = call_candidates[1:]
            else:
                stop_idx = len(tokens)
                for i, t in enumerate(tokens):
                    if t.upper() in _KNOWN_RANKS or re.search(r'\d', t):
                        stop_idx = i
                        break
                fio_parts = tokens[:stop_idx]
                remainder = tokens[stop_idx:]

        fio = " ".join(fio_parts)

        if remainder:
            if remainder[-1].lower() == 'тренер':
                remainder = remainder[:-1]
            if len(remainder) >= 1:
                rank = remainder[0]
            if len(remainder) >= 2:
                callsign = remainder[1]
            if len(remainder) > 2:
                category = ", ".join(remainder[2:])

        return {
            'fio': fio, 'name': fio, 'full_name': fio, 'fio_operatora': fio,
            'dob': dob, 'birth_date': dob, 'birthdate': dob, 'date_of_birth': dob,
            'birth_year': dob,
            'rank': rank, 'sport_rank': rank, 'sport_category': rank, 'category': category,
            'callsign': callsign, 'call': callsign, 'personal_callsign': callsign,
            'extra_calls': extra_calls
        }
    except Exception:
        return {
            'fio': raw, 'name': raw, 'full_name': raw, 'fio_operatora': raw,
            'dob': '', 'birth_date': '', 'birthdate': '', 'date_of_birth': '',
            'birth_year': '',
            'rank': '', 'sport_rank': '', 'sport_category': '', 'category': '',
            'callsign': '', 'call': '', 'personal_callsign': '',
            'extra_calls': []
        }


# Максимум записей об операторах, отдаваемых в форму подтверждения.
# Ограничивает раздувание списка из строки OPERATORS (списки позывных
# «UA8AA UA8BA ..» и «;»-разделители) — защита от DoS-усиления.
MAX_OPERATORS = 25

# Ключевые слова шапки, для которых допускается запись без двоеточия
# (например "LOCATION PK01" или "EMAIL user@mail.ru"). Ключ — нормализованный.
_KNOWN_HEADER_KEYS = frozenset({
    'START-OF-LOG', 'END-OF-LOG', 'CALLSIGN', 'CONTEST',
    'CATEGORY-OPERATOR', 'CATEGORY-BAND', 'CATEGORY-MODE',
    'CATEGORY-ASSISTED', 'CATEGORY-POWER', 'LOCATION', 'NAME',
    'ADDRESS', 'EMAIL', 'CLUB', 'SOAPBOX', 'OPERATORS', 'OPERATOR',
    'CLAIMED-SCORE', 'CREATED-BY', 'QSO',
    'GRID-LOCATOR', 'ADDRESS-CITY', 'ADDRESS-POSTALCODE',
    'ADDRESS-STATE-PROVINCE', 'ADDRESS-COUNTRY', 'COUNTRY'
})

# Поля, встречающиеся только в Cabrillo (нужны для опознания формата файла).
_CABRILLO_ONLY_KEYS = (
    'GRID-LOCATOR', 'ADDRESS-CITY', 'ADDRESS-POSTALCODE',
    'ADDRESS-STATE-PROVINCE', 'ADDRESS-COUNTRY', 'COUNTRY'
)


def detect_report_format(headers):
    """Ориентировочное определение «номинального» формата файла отчета.

    Парсер не полагается на результат (оба формата разбираются вместе),
    но значение сохраняется в шапку как информация для судейской коллегии.
    """
    cab = sum(1 for k in _CABRILLO_ONLY_KEYS if (headers.get(k) or '').strip())
    created = (headers.get('CREATED-BY') or '').upper()
    if cab >= 2 or (cab == 1 and '5MCONTEST' in created):
        return 'CABRILLO'
    if cab == 1:
        return 'MIXED'
    return 'ERMAK'

def parse_ermak(file_content, comp_start=None, comp_end=None, plugin_title=None, exchange_spec=None):
    if hasattr(file_content, 'read'):
        file_content = file_content.read()

    if isinstance(file_content, bytes):
        text = None
        encodings = ['utf-8-sig', 'utf-8', 'cp1251', 'mac_cyrillic', 'cp866', 'latin-1']
        for encoding in encodings:
            try:
                text = file_content.decode(encoding)
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
        if text is None:
            text = file_content.decode('utf-8', errors='replace')
    else:
        text = str(file_content)

    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = text.replace('\x00', '').replace('\ufeff', '').replace('\u200b', '')
    text = text.replace('：', ':')

    headers = {}
    qsos = []
    missing_headers = []
    operators = [] 
    
    required_headers = ['START-OF-LOG', 'END-OF-LOG', 'CALLSIGN', 'CONTEST']

    for line in text.split('\n'):
        line_str = line.strip()
        if not line_str:
            continue

        if not line_str.upper().startswith('QSO:'):
            raw_key = None
            val = ''
            if ':' in line_str:
                parts = line_str.split(':', 1)
                raw_key = parts[0]
                val = parts[1].replace('\xa0', ' ').strip() if len(parts) > 1 else ''
            else:
                # Поддержка записи ключевых слов без двоеточия: "LOCATION PK01",
                # "EMAIL user@mail.ru" (пользователь мог забыть разделитель).
                kwargs_scan = line_str.split(None, 1)
                if len(kwargs_scan) == 2:
                    key_candidate = normalize_header_key(kwargs_scan[0])
                    if key_candidate in _KNOWN_HEADER_KEYS:
                        raw_key = kwargs_scan[0]
                        val = kwargs_scan[1].replace('\xa0', ' ').strip()

            if raw_key:
                key = normalize_header_key(raw_key)
                if key == 'ADDRESS':
                    # В формате Ermak может быть до трех полей ADDRESS очереди:
                    # каждая следующая строка дополняет предыдущую (индекс, регион/город, улица и т.д.).
                    # Объединяем их в одно значение через запятую.
                    new_val = val.strip(' ,')
                    prev = headers.get('ADDRESS')
                    if new_val:
                        if prev:
                            headers['ADDRESS'] = f"{prev.rstrip(' ,')}, {new_val}"
                        else:
                            headers['ADDRESS'] = new_val
                else:
                    headers[key] = val

                if key in ['OPERATORS', 'OPERATOR'] and val:
                    # В формате Ermak несколько операторов разделяются точкой с запятой.
                    for op_segment in re.split(r'[;]', val):
                        op_segment = op_segment.strip()
                        if not op_segment:
                            continue
                        op_data = parse_operator_line(op_segment)
                        if op_data:
                            operators.append(op_data)
                            # Вариант «список позывных»: каждый позывной —
                            # отдельная запись оператора.
                            for extra_call in op_data.get('extra_calls', []):
                                if len(operators) >= MAX_OPERATORS:
                                    break
                                operators.append({
                                    'fio': '', 'name': '', 'full_name': '', 'fio_operatora': '',
                                    'dob': '', 'birth_date': '', 'birthdate': '',
                                    'date_of_birth': '', 'birth_year': '',
                                    'rank': '', 'sport_rank': '', 'sport_category': '',
                                    'category': '',
                                    'callsign': extra_call, 'call': extra_call,
                                    'personal_callsign': extra_call, 'extra_calls': []
                                })

        elif line_str.upper().startswith('QSO:'):
            tokens = line_str.split()
            parsed = parse_qso_line(tokens, exchange_spec=exchange_spec)
            if parsed is None:
                continue

            freq = parsed['freq']
            mode = parsed['mode_raw'].upper()
            date_str = parsed['date']
            time_str = parsed['time']
            my_call = parsed['my_call']
            his_call = parsed['his_call']
            rst_s = parsed['rst_s']
            exch_s = parsed['exch_s']
            rst_r = parsed['rst_r']
            exch_r = parsed['exch_r']

            if mode in ['SSB', 'FM', 'AM']:
                mode_display = 'PH'
            elif mode == 'CW':
                mode_display = 'CW'
            else:
                mode_display = mode

            status_info = {'is_error': False, 'text': 'OK'}
            if parsed['dt'] is None:
                status_info = {'is_error': True, 'text': 'Invalid Date/Time format'}
            elif comp_start and comp_end and not (comp_start <= parsed['dt'] <= comp_end):
                status_info = {'is_error': True, 'text': 'Out of competition time'}

            qsos.append({
                'freq': freq,
                'band': freq_to_band(freq),
                'mode': mode_display,
                'date': date_str,
                'time': time_str,
                'my_call': my_call,
                'his_call': his_call,

                'my_rst': rst_s,
                'my_exch': exch_s,
                'his_rst': rst_r,
                'his_exch': exch_r,

                'rst_s': rst_s,
                'exch_s': exch_s,
                'rst_r': rst_r,
                'exch_r': exch_r,

                'points': 1,
                'status_info': status_info,
                'format_errors': parsed['format_errors'],
                'has_critical_format': parsed['has_critical_format'],
                'raw_line': line_str
            })

    # Ограничение размера списка операторов (защита от DoS-усиления
    # через гигантские списки в строке OPERATORS).
    if len(operators) > MAX_OPERATORS:
        operators = operators[:MAX_OPERATORS]

    # Сборка полного почтового адреса из блоков Cabrillo
    # (ADDRESS + ADDRESS-CITY + ADDRESS-STATE-PROVINCE +
    #  ADDRESS-POSTALCODE + ADDRESS-COUNTRY).
    address_parts = [headers.get('ADDRESS')]
    for sub in ('ADDRESS-CITY', 'ADDRESS-STATE-PROVINCE',
                'ADDRESS-POSTALCODE', 'ADDRESS-COUNTRY'):
        sub_val = headers.get(sub)
        if sub_val and sub_val.strip():
            address_parts.append(sub_val.strip(' ,'))
    address_parts = [p.strip(' ,') for p in address_parts if p and p.strip()]
    if address_parts:
        headers['ADDRESS'] = ', '.join(address_parts)

    # GRID-LOCATOR как запасной источник для LOCATION (Cabrillo-файлы).
    if not (headers.get('LOCATION') or '').strip() and (headers.get('GRID-LOCATOR') or '').strip():
        headers['LOCATION'] = headers['GRID-LOCATOR']

    # Если NAME в шапке пуст (как в Cabrillo-отчетах 5MContest), подставляем
    # ФИО первого оператора из OPERATORS — это требование формы подтверждения.
    if not (headers.get('NAME') or '').strip():
        for op in operators:
            if op.get('fio'):
                headers['NAME'] = op['fio']
                break

    # Автозаполнение CONTEST из названия соревнования (PLUGIN_TITLE выбранного плагина),
    # если в отчете поле не указано или пустое.
    if plugin_title and not (headers.get('CONTEST') or '').strip():
        headers['CONTEST'] = plugin_title

    headers['REPORT_FORMAT'] = detect_report_format(headers)

    for req in required_headers:
        if req not in headers:
            missing_headers.append(req)

    return headers, qsos, missing_headers, text, operators


def update_cabrillo_header(file_path, edited_data):
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.read().splitlines()

    def _pick(*keys):
        """Первое непустое значение из переданных ключей edited_data."""
        for k in keys:
            val = edited_data.get(k)
            if val:
                return val
        return ''

    # Новые значения полей шапки (из формы подтверждения).
    rewriting = {
        'CALLSIGN': _pick('callsign', 'CALLSIGN', 'header_CALLSIGN'),
        'CONTEST': _pick('contest', 'CONTEST', 'header_CONTEST'),
        'LOCATION': _pick('region', 'location', 'LOCATION', 'operator_location'),
        'NAME': _pick('name', 'NAME', 'header_NAME'),
        'ADDRESS': _pick('address', 'ADDRESS', 'header_ADDRESS'),
        'EMAIL': _pick('email', 'EMAIL', 'header_EMAIL'),
        'CLUB': _pick('club', 'CLUB', 'header_CLUB'),
    }

    start_of_log = ''
    end_of_log = ''
    category_lines = []
    preserved_lines = []

    for line in lines:
        if not line.strip():
            continue

        line_clean = line.replace('\x00', '').strip()
        parts = line_clean.split(':', 1)
        norm_key = normalize_header_key(parts[0]) if len(parts) > 1 else ''

        if norm_key == 'START-OF-LOG':
            start_of_log = line_clean
        elif norm_key == 'END-OF-LOG':
            end_of_log = line_clean
        elif norm_key == 'CATEGORY-OPERATOR' or norm_key.startswith('CATEGORY'):
            category_lines.append(line_clean)
        elif norm_key in rewriting or norm_key in ['OPERATORS', 'OPERATOR']:
            continue  # поле перезаписывается значениями из формы
        else:
            preserved_lines.append(line_clean)

    # Собираем блок заголовка в стандартном порядке Cabrillo/Ermak.
    header_lines = [start_of_log if start_of_log else 'START-OF-LOG: 3.0']

    if rewriting.get('CONTEST'):
        header_lines.append(f"CONTEST: {rewriting['CONTEST']}")
    if rewriting.get('CALLSIGN'):
        header_lines.append(f"CALLSIGN: {rewriting['CALLSIGN']}")

    header_lines.extend(category_lines)

    for field in ('LOCATION', 'NAME', 'ADDRESS', 'EMAIL', 'CLUB'):
        if rewriting.get(field):
            header_lines.append(f"{field}: {rewriting[field]}")

    for op in edited_data.get('operators', []):
        parts = []
        if op.get('fio'):
            fio_formatted = op['fio'].replace(' ', ', ') if ',' not in op['fio'] else op['fio']
            parts.append(fio_formatted)
        if op.get('dob'): parts.append(op['dob'])
        if op.get('rank'): parts.append(op['rank'])
        if op.get('callsign'): parts.append(op['callsign'])
        if parts:
            header_lines.append(f"OPERATORS: {', '.join(parts)}")

    # Вставка заголовка перед первым QSO/END-OF-LOG.
    insert_idx = len(preserved_lines)
    for i, line in enumerate(preserved_lines):
        upper = line.upper()
        if upper.startswith('QSO:') or upper.startswith('END-OF-LOG:'):
            insert_idx = i
            break

    final_lines = preserved_lines[:insert_idx] + header_lines + preserved_lines[insert_idx:]
    final_lines.append(end_of_log if end_of_log else 'END-OF-LOG:')

    with open(file_path, 'w', encoding='utf-8') as f:
        for line in final_lines:
            f.write(line + '\n')