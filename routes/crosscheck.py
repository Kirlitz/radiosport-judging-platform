import os
from datetime import datetime
from app.ermak_parser import parse_ermak

def run_crosscheck(comp, upload_folder):
    comp_dir = os.path.join(upload_folder, str(comp.id))
    if not os.path.exists(comp_dir):
        return {"error": "Нет загруженных отчетов для этого соревнования"}

    # 1. Собираем все логи по категориям
    logs_data = {} # {callsign: {'category': cat, 'qsos': [...], 'raw_lines': [...]}}
    
    for cat_name in os.listdir(comp_dir):
        cat_path = os.path.join(comp_dir, cat_name)
        if not os.path.isdir(cat_path):
            continue
        for filename in os.listdir(cat_path):
            if filename.endswith('.cbr'):
                callsign = filename[:-4].upper()
                file_path = os.path.join(cat_path, filename)
                with open(file_path, 'rb') as f:
                    content = f.read()
                headers, qsos, missing, raw_text, ops = parse_ermak(content, comp.start_time, comp.end_time)
                logs_data[callsign] = {
                    'category': cat_name,
                    'qsos': qsos,
                    'raw_text': raw_text
                }

    # 2. Перекрестная проверка (Cross-checking)
    results = {}
    delta_minutes = comp.time_delta_allowed

    for callA, dataA in logs_data.items():
        ubn_lines = [f"UBN REPORT FOR: {callA} [{dataA['category']}]", "="*40, ""]
        valid_qsos = 0
        total_points = 0

        for idx, qsoA in enumerate(dataA['qsos'], 1):
            callB = qsoA['his_call'].upper()
            dateA, timeA = qsoA['date'], qsoA['time']
            bandA = qsoA['freq']
            modeA = qsoA['mode']

            if qsoA['status_info']['is_error']:
                ubn_lines.append(f"QSO {idx}: {dateA} {timeA} {callB} - ОШИБКА: {qsoA['status_info']['text']}")
                continue

            # Ищем отчет корреспондента callB
            if callB not in logs_data:
                # Корреспондент не прислал отчет (NIL - Not in Log)
                # По правилам некоторых соревнований это проверяется по базам или снижает очки
                ubn_lines.append(f"QSO {idx}: {dateA} {timeA} {callB} - NIL (Корреспондент не прислал отчет)")
                continue

            # Ищем встречную связь у B
            dataB = logs_data[callB]
            matched = False
            for qsoB in dataB['qsos']:
                if qsoB['his_call'].upper() == callA:
                    # Сверяем диапазон / вид
                    if qsoB['mode'] != modeA:
                        continue
                    
                    # Сверяем время с учетом допустимой разницы (delta)
                    try:
                        dtA = datetime.strptime(f"{dateA} {timeA}", "%Y-%m-%d %H%M")
                        dtB = datetime.strptime(f"{qsoB['date']} {qsoB['time']}", "%Y-%m-%d %H%M")
                        diff_min = abs((dtA - dtB).total_seconds()) / 60.0
                        
                        if diff_min <= delta_minutes:
                            matched = True
                            break
                    except ValueError:
                        continue

            if matched:
                valid_qsos += 1
                total_points += 1 # Базовое начисление 1 очко за связь
            else:
                ubn_lines.append(f"QSO {idx}: {dateA} {timeA} {callB} - БУСТ / Расхождение времени более {delta_minutes} мин")

        ubn_lines.append("")
        ubn_lines.append(f"ИТОГО ПОДТВЕРЖДЕНО СВЯЗЕЙ: {valid_qsos}")
        ubn_lines.append(f"ИТОГО НАЧИСЛЕНО ОЧКОВ: {total_points}")

        results[callA] = {
            'valid_qsos': valid_qsos,
            'points': total_points,
            'ubn_text': "\n".join(ubn_lines)
        }

    return results