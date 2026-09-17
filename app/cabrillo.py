import re
from datetime import datetime

from app.utils import normalize_mode

def freq_to_band(freq_str):
    """
    Универсальная нормализация диапазона. Корректно переводит 
    как МГц (3.5), так и кГц (3500) в стандартизированные значения (80m).
    """
    try:
        f = float(freq_str)
        if f < 100:  # Значения вроде 1.8, 3.5, 7.0, 14, 21, 28
            f = f * 1000
            
        if 1800 <= f <= 2000: return '160m'
        if 3500 <= f <= 3800: return '80m'
        if 7000 <= f <= 7300: return '40m'
        if 14000 <= f <= 14350: return '20m'
        if 21000 <= f <= 21450: return '15m'
        if 28000 <= f <= 29700: return '10m'
        if 144000 <= f <= 146000 or 144 <= f <= 146: return '2m'
        if 430000 <= f <= 440000 or 430 <= f <= 440: return '70cm'
    except ValueError:
        pass
    
    # Для УКВ (144, 430, 432) возвращаем как есть, убирая пробелы
    return str(freq_str).strip()

def read_file_with_fallback(file_path):
    """
    Универсальное чтение файла. Пытается прочитать в cp1251 (Ермак),
    если не выходит — падает назад на utf-8. Автоматически нормализует переводы строк.
    """
    for enc in ['windows-1251', 'utf-8']:
        try:
            with open(file_path, 'r', encoding=enc, newline=None) as f:
                return f.readlines()
        except UnicodeDecodeError:
            continue
    # Если совсем всё плохо, читаем с игнорированием ошибок
    with open(file_path, 'r', encoding='utf-8', errors='ignore', newline=None) as f:
        return f.readlines()

def parse_cabrillo_header(file_path):
    header = {
        'name': '-',
        'birth_year': '-',
        'rank': '-',
        'location': '-'
    }
    
    lines = read_file_with_fallback(file_path)
    
    for line in lines:
        clean_line = line.strip()
        if clean_line.upper().startswith('QSO:'):
            break
            
        if clean_line.upper().startswith('NAME:'):
            val = clean_line.split(':', 1)[1].strip()
            # Обработка разделителей (запятая или точка)
            parts = [p.strip() for p in re.split(r'[,\.]', val) if p.strip()]
            if len(parts) >= 1: header['name'] = parts[0]
            if len(parts) >= 2: header['birth_year'] = parts[1]
            if len(parts) >= 3: header['rank'] = parts[2]
            if len(parts) >= 4: header['location'] = parts[3]
            
        elif clean_line.upper().startswith('OPERATORS:') and header['name'] == '-':
            # Ловушка для логов UR5EQF, где всё пишется в OPERATORS через точку
            val = clean_line.split(':', 1)[1].strip()
            parts = [p.strip() for p in val.split('.') if p.strip()]
            if len(parts) >= 1: header['name'] = parts[0]
            if len(parts) >= 2: header['birth_year'] = parts[1]
            if len(parts) >= 3: header['rank'] = parts[2]
            
        elif clean_line.upper().startswith('LOCATION:'):
            if header['location'] == '-':
                header['location'] = clean_line.split(':', 1)[1].strip()
                
    return header

def parse_cabrillo_file(file_path, my_callsign):
    qsos = []
    lines = read_file_with_fallback(file_path)

    for line in lines:
        clean_line = line.strip()
        # ИСПРАВЛЕНО: убрана жесткая привязка к началу строки, теперь re.match игнорирует пробелы перед QSO:
        if not clean_line.upper().startswith('QSO:'):
            continue
            
        parts = clean_line.split()
        if len(parts) < 10:  # Минимальное количество полей в Cabrillo/Ермак
            continue
        
        freq, mode = parts[1], normalize_mode(parts[2])
        date_str, time_str = parts[3], parts[4]
        
        try:
            if len(time_str) == 4:
                dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H%M")
            else:
                dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except:
            continue
            
        # ИСПРАВЛЕНО: Извлекаем данные с конца строки, так как в середине может быть 
        # или не быть локатора (63LE / 53wc), что смещает индексы с начала строки.
        # Структура конца строки Ермака ВСЕГДА: ... [ПОЗЫВНОЙ_КОРР] [RST_ПРИНЯТ] [НОМЕР_ПРИНЯТ]
        nr_rcvd = parts[-1]
        rst_rcvd = parts[-2]
        corr_call = parts[-3].upper().strip()
        
        # Номера переданные (находятся перед позывным корреспондента)
        # Ищем позицию корреспондента и берем элементы перед ней
        corr_idx = parts.index(parts[-3])
        nr_sent = parts[corr_idx - 1]
        rst_sent = parts[corr_idx - 2]
            
        qsos.append({
            'my_call': my_callsign.upper().strip(),
            'band': freq_to_band(freq),
            'mode': mode,
            'qso_datetime': dt,
            'rst_sent': rst_sent,
            'nr_sent': nr_sent,
            'corr_call': corr_call,
            'rst_rcvd': rst_rcvd,
            'nr_rcvd': nr_rcvd
        })
    return qsos
