import re
from datetime import datetime

def freq_to_band(freq_str):
    try:
        freq = int(re.sub(r'\D', '', freq_str))
        if 1800 <= freq <= 2000 or freq == 1800 or freq == 160: return '160m'
        if 3500 <= freq <= 3800 or freq == 3500 or freq == 80: return '80m'
        if 7000 <= freq <= 7200 or freq == 7000 or freq == 40: return '40m'
        if 14000 <= freq <= 14350 or freq == 14000 or freq == 20: return '20m'
        if 21000 <= freq <= 21450 or freq == 21000 or freq == 15: return '15m'
        if 28000 <= freq <= 29700 or freq == 28000 or freq == 10: return '10m'
    except:
        pass
    return str(freq_str).lower()

def parse_cabrillo_file(file_path, my_callsign):
    qsos = []
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception:
        return qsos

    for line in lines:
        if not line.startswith('QSO:'):
            continue
        parts = line.split()
        if len(parts) < 11:
            continue
        
        freq, mode = parts[1], parts[2].upper()
        date_str, time_str = parts[3], parts[4]
        
        try:
            if len(time_str) == 4:
                dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H%M")
            else:
                dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except:
            continue
            
        qsos.append({
            'my_call': my_callsign.upper().strip(),
            'band': freq_to_band(freq),
            'mode': mode,
            'qso_datetime': dt,
            'rst_sent': parts[6],
            'nr_sent': parts[7],
            'corr_call': parts[8].upper().strip(),
            'rst_rcvd': parts[9],
            'nr_rcvd': parts[10]
        })
    return qsos