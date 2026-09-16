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


def parse_ermak(file_content, comp_start=None, comp_end=None):
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
    
    required_headers = ['CALLSIGN', 'CONTEST', 'CATEGORY-OPERATOR']

    for line in text.split('\n'):
        line_str = line.strip()
        if not line_str:
            continue

        if ':' in line_str and not line_str.upper().startswith('QSO:'):
            parts = line_str.split(':', 1)
            raw_key = parts[0]
            val = parts[1].replace('\xa0', ' ').strip() if len(parts) > 1 else ''
            
            key = normalize_header_key(raw_key)
            headers[key] = val
            
            if key in ['OPERATORS', 'OPERATOR']:
                op_data = parse_operator_line(val)
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

    for req in required_headers:
        if req not in headers:
            missing_headers.append(req)

    return headers, qsos, missing_headers, text, operators


def update_cabrillo_header(file_path, edited_data):
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.read().splitlines()

    new_lines = []
    
    callsign_val = edited_data.get('callsign') or edited_data.get('CALLSIGN') or edited_data.get('header_CALLSIGN')
    location_val = edited_data.get('region') or edited_data.get('location') or edited_data.get('LOCATION') or edited_data.get('operator_location')
    
    has_location_header = False

    for line in lines:
        if not line.strip():
            continue 
            
        line_clean = line.replace('\x00', '').strip()
        parts = line_clean.split(':', 1)
        if len(parts) > 1:
            raw_key = parts[0]
            norm_key = normalize_header_key(raw_key)
            
            if norm_key == 'CALLSIGN' and callsign_val:
                new_lines.append(f"CALLSIGN: {callsign_val}")
                continue
                
            if norm_key == 'LOCATION':
                has_location_header = True
                if location_val:
                    new_lines.append(f"LOCATION: {location_val}")
                continue

            if norm_key in ['OPERATORS', 'OPERATOR']:
                continue
            
        new_lines.append(line_clean)

    if not has_location_header and location_val:
        loc_insert_idx = len(new_lines)
        for i, line in enumerate(new_lines):
            line_upper = line.upper()
            if any(line_upper.startswith(k) for k in ['CATEGORY', 'OPERATORS:', 'OPERATOR:', 'SOAPBOX:', 'QSO:']):
                loc_insert_idx = i
                break
        new_lines.insert(loc_insert_idx, f"LOCATION: {location_val}")

    insert_idx = len(new_lines)
    for i, line in enumerate(new_lines):
        if line.upper().startswith('SOAPBOX:') or line.upper().startswith('QSO:'):
            insert_idx = i
            break

    ops_lines = []
    for op in edited_data.get('operators', []):
        parts = []
        if op.get('fio'): 
            fio_formatted = op['fio'].replace(' ', ', ') if ',' not in op['fio'] else op['fio']
            parts.append(fio_formatted)
        if op.get('dob'): parts.append(op['dob'])
        if op.get('rank'): parts.append(op['rank'])
        if op.get('callsign'): parts.append(op['callsign'])
        
        if parts:
            ops_lines.append(f"OPERATORS: {', '.join(parts)}")

    final_lines = new_lines[:insert_idx] + ops_lines + new_lines[insert_idx:]

    with open(file_path, 'w', encoding='utf-8') as f:
        for line in final_lines:
            f.write(line + '\n')