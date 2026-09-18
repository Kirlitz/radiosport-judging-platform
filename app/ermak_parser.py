import re
from datetime import datetime

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


def parse_operator_line(line_val):
    if not line_val or not str(line_val).strip():
        return None
        
    try:
        tokens = [t.strip('.') for t in re.split(r'[\.,\s]+', str(line_val).strip()) if t.strip('.')]
        if not tokens:
            return None
            
        fio_parts = []
        dob = ""
        rank = ""
        callsign = ""
        
        dob_idx = -1
        for i, t in enumerate(tokens):
            if re.match(r'^\d{1,2}[./-]\d{1,2}[./-]\d{2,4}$', t) or re.match(r'^\d{4}$', t):
                dob_idx = i
                break
        
        if dob_idx != -1:
            fio_parts = tokens[:dob_idx]
            dob = tokens[dob_idx]
            remaining = tokens[dob_idx+1:]
        else:
            stop_idx = len(tokens)
            known_ranks = {'МС', 'КМС', 'ЗМС', 'Б/Р', 'БР', '1', '2', '3', 'МСМК', '1К', '2К', '3К'}
            for i, t in enumerate(tokens):
                if t.upper() in known_ranks or re.search(r'\d', t):
                    stop_idx = i
                    break
            fio_parts = tokens[:stop_idx]
            remaining = tokens[stop_idx:]
            
        fio = " ".join(fio_parts)
        
        if len(remaining) >= 2:
            rank = remaining[0]
            callsign = remaining[1]
        elif len(remaining) == 1:
            if re.search(r'[A-Za-z]', remaining[0]) and len(remaining[0]) >= 3:
                callsign = remaining[0]
            else:
                rank = remaining[0]
                
        return {
            'fio': fio, 'name': fio, 'full_name': fio, 'fio_operatora': fio,
            'dob': dob, 'birth_date': dob, 'birthdate': dob, 'date_of_birth': dob,
            'rank': rank, 'sport_rank': rank, 'sport_category': rank, 'category': rank,
            'callsign': callsign, 'call': callsign, 'personal_callsign': callsign
        }
    except Exception:
        return {
            'fio': line_val, 'name': line_val, 'full_name': line_val, 'fio_operatora': line_val,
            'dob': '', 'birth_date': '', 'birthdate': '', 'date_of_birth': '',
            'rank': '', 'sport_rank': '', 'sport_category': '', 'category': '',
            'callsign': '', 'call': '', 'personal_callsign': ''
        }


# Ключевые слова шапки, для которых допускается запись без двоеточия
# (например "LOCATION PK01" или "EMAIL user@mail.ru"). Ключ — нормализованный.
_KNOWN_HEADER_KEYS = frozenset({
    'START-OF-LOG', 'END-OF-LOG', 'CALLSIGN', 'CONTEST',
    'CATEGORY-OPERATOR', 'CATEGORY-BAND', 'CATEGORY-MODE',
    'CATEGORY-ASSISTED', 'CATEGORY-POWER', 'LOCATION', 'NAME',
    'ADDRESS', 'EMAIL', 'CLUB', 'SOAPBOX', 'OPERATORS', 'OPERATOR',
    'CLAIMED-SCORE', 'CREATED-BY', 'QSO'
})

def parse_ermak(file_content, comp_start=None, comp_end=None, plugin_title=None):
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

        elif line_str.upper().startswith('QSO:'):
            tokens = line_str.split()
            if len(tokens) >= 8:
                freq = tokens[1]
                mode = tokens[2].upper()
                date_str = tokens[3]
                time_str = tokens[4]
                my_call = tokens[5].upper()

                if len(tokens) >= 11:
                    his_call = tokens[-3].upper()
                    rst_s = tokens[6]
                    exch_s = tokens[7]
                    rst_r = tokens[-2]
                    exch_r = tokens[-1]
                elif len(tokens) == 10:
                    his_call = tokens[-3].upper()
                    rst_s = tokens[6]
                    exch_s = tokens[7]
                    rst_r = "59"
                    exch_r = tokens[-1]
                else:
                    his_call = tokens[-2].upper() if len(tokens) == 9 else tokens[7].upper()
                    rst_s = "59"
                    exch_s = tokens[6] if len(tokens) > 6 else ""
                    rst_r = "59"
                    exch_r = tokens[-1]

                if mode in ['SSB', 'FM', 'AM']:
                    mode_display = 'PH'
                elif mode == 'CW':
                    mode_display = 'CW'
                else:
                    mode_display = mode

                status_info = {'is_error': False, 'text': '1'}

                try:
                    dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H%M")
                    if comp_start and comp_end and not (comp_start <= dt <= comp_end):
                        status_info = {'is_error': True, 'text': 'Out of competition time'}
                except ValueError:
                    status_info = {'is_error': True, 'text': 'Invalid Date/Time format'}

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
                    'raw_line': line_str
                })

    # Автозаполнение CONTEST из названия соревнования (PLUGIN_TITLE выбранного плагина),
    # если в отчете поле не указано или пустое.
    if plugin_title and not (headers.get('CONTEST') or '').strip():
        headers['CONTEST'] = plugin_title

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