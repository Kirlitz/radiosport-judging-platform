import re
from datetime import datetime

def parse_operator_line(line_val):
    """
    Интеллектуальный разбор строки с данными оператора.
    Автоматически выделяет ФИО, дату рождения, разряд и позывной.
    """
    tokens = [t for t in re.split(r'[,\s]+', line_val.strip()) if t]
    
    fio_parts = []
    dob = ""
    rank = ""
    callsign = ""
    
    dob_idx = -1
    for i, t in enumerate(tokens):
        if re.match(r'\d{1,2}[./-]\d{1,2}[./-]\d{2,4}', t) or re.match(r'^\d{4}$', t):
            dob_idx = i
            break
    
    if dob_idx != -1:
        fio_parts = tokens[:dob_idx]
        dob = tokens[dob_idx]
        remaining = tokens[dob_idx+1:]
    else:
        stop_idx = len(tokens)
        known_ranks = {'МС', 'КМС', 'ЗМС', 'Б/Р', 'БР', '1', '2', '3', 'МСМК'}
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
        if re.search(r'\d', remaining[0]) and len(remaining[0]) >= 3:
            callsign = remaining[0]
        else:
            rank = remaining[0]
            
    return {
        'fio': fio, 'name': fio, 'full_name': fio, 'fio_operatora': fio,
        'dob': dob, 'birth_date': dob, 'birthdate': dob, 'date_of_birth': dob,
        'rank': rank, 'sport_rank': rank, 'sport_category': rank, 'category': rank,
        'callsign': callsign, 'call': callsign, 'personal_callsign': callsign
    }


def parse_ermak(file_content, comp_start=None, comp_end=None):
    if isinstance(file_content, bytes):
        text = None
        encodings = ['utf-8', 'cp1251', 'cp866', 'maccyrillic', 'latin-1']
        for encoding in encodings:
            try:
                text = file_content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text = file_content.decode('utf-8', errors='replace')
    else:
        text = str(file_content)

    headers = {}
    qsos = []
    missing_headers = []
    operators = [] 
    
    required_headers = ['CALLSIGN:', 'CONTEST:', 'CATEGORY-OPERATOR:']

    for line in text.splitlines():
        line_str = line.strip()
        if not line_str:
            continue

        if ':' in line_str and not line_str.upper().startswith('QSO:'):
            parts = line_str.split(':', 1)
            key = parts[0].strip().upper()
            val = parts[1].strip() if len(parts) > 1 else ''
            headers[key] = val
            
            if key in ['OPERATORS', 'OPERATOR']:
                operators.append(parse_operator_line(val))

        elif line_str.upper().startswith('QSO:'):
            tokens = line_str.split()
            if len(tokens) >= 11:
                freq = tokens[1]
                mode = tokens[2].upper()
                date_str = tokens[3]
                time_str = tokens[4]
                my_call = tokens[5].upper()
                rst_s = tokens[6]
                exch_s = tokens[7]
                his_call = tokens[8].upper()
                rst_r = tokens[9]
                exch_r = tokens[10]

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
                    'mode': mode_display,
                    'date': date_str,
                    'time': time_str,
                    'my_call': my_call,
                    'his_call': his_call,
                    
                    # Точные ключи из вашего HTML-шаблона для вывода контрольных номеров
                    'my_rst': rst_s,
                    'my_exch': exch_s,
                    'his_rst': rst_r,
                    'his_exch': exch_r,
                    
                    # Сохранены для обратной совместимости с логикой судейства
                    'rst_s': rst_s,
                    'exch_s': exch_s,
                    'rst_r': rst_r,
                    'exch_r': exch_r,
                    
                    'points': 1, 
                    'status_info': status_info,
                    'raw_line': line_str
                })

    for req in required_headers:
        clean_req = req.replace(':', '')
        if clean_req not in headers:
            missing_headers.append(clean_req)

    return headers, qsos, missing_headers, text, operators