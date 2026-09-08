import os
from datetime import datetime
from app.ermak_parser import parse_ermak

def format_band(freq_str):
    """Преобразует частоту в обозначение диапазона (например, 3510 -> 80m)."""
    try:
        freq = float(freq_str)
        if 1800 <= freq <= 2000 or freq == 1.8: return "160m"
        if 3500 <= freq <= 3800 or freq == 3.5: return "80m"
        if 7000 <= freq <= 7200 or freq == 7: return "40m"
        if 14000 <= freq <= 14350 or freq == 14: return "20m"
        if 21000 <= freq <= 21450 or freq == 21: return "15m"
        if 28000 <= freq <= 29700 or freq == 28: return "10m"
    except ValueError:
        pass
    return f"{freq_str}m"

def run_crosscheck(comp, upload_folder):
    comp_dir = os.path.join(upload_folder, str(comp.id))
    if not os.path.exists(comp_dir):
        return {"error": "Нет загруженных отчетов для этого соревнования"}

    logs_data = {}
    
    # Сбор всех отчетов участников
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

    results = {}
    delta_minutes = comp.time_delta_allowed

    for callA, dataA in logs_data.items():
        ubn_lines = [
            f"PROTOCOL UBN REPORT FOR: {callA}",
            f"CONTEST: {comp.name}",
            f"CATEGORY: {dataA['category']}",
            "=" * 120
        ]

        seen_qsos = {}  # Для отслеживания повторов (Duplicate)
        seen_callsigns_by_band = set()  # Для определения новых позывных (+5 extra points)
        
        valid_qsos = 0
        total_points = 0
        total_extra_points = 0

        for idx, qsoA in enumerate(dataA['qsos'], 1):
            callB = qsoA['his_call'].upper()
            dateA, timeA = qsoA['date'], qsoA['time']
            freqA = qsoA['freq']
            modeA = qsoA['mode']
            rst_sA, exch_sA = qsoA['rst_s'], qsoA['exch_s']
            rst_rA, exch_rA = qsoA['rst_r'], qsoA['exch_r']
            band_str = format_band(freqA)

            # Форматирование префикса строки QSO
            prefix = f"QSO {idx:>3} : {freqA:<5}\t{modeA:<2}\t{dateA}\t{timeA}\t{callA}\t{rst_sA}\t{exch_sA}\t{callB}\t{rst_rA}\t{exch_rA}"

            # 1. Проверка на повторную связь (Duplicate)
            qso_key = (callB, band_str)
            if qso_key in seen_qsos:
                first_qso_num = seen_qsos[qso_key]
                ubn_lines.append(f"{prefix}\t-\t-\t-\tDuplicate [QSO {first_qso_num}]")
                continue

            # 2. Проверка на ошибки формата/времени вне рамок соревнования
            if qsoA['status_info']['is_error']:
                ubn_lines.append(f"{prefix}\t-\t-\t-\t{qsoA['status_info']['text']}")
                continue

            # 3. Корреспондент не прислал отчет (NoLog)
            if callB not in logs_data:
                ubn_lines.append(f"{prefix}\t-\t-\t-\tNoLog [{callB}]")
                continue

            dataB = logs_data[callB]
            matched_qsoB = None
            qsoB_index = None
            partner_bad_call = None
            
            # Поиск зеркальной связи в отчете корреспондента
            for idxB, qsoB in enumerate(dataB['qsos'], 1):
                try:
                    dtA = datetime.strptime(f"{dateA} {timeA}", "%Y-%m-%d %H%M")
                    dtB = datetime.strptime(f"{qsoB['date']} {qsoB['time']}", "%Y-%m-%d %H%M")
                    diff_min = abs((dtA - dtB).total_seconds()) / 60.0
                except ValueError:
                    continue

                if diff_min <= delta_minutes:
                    # Корреспондент записал наш позывной верно
                    if qsoB['his_call'].upper() == callA:
                        matched_qsoB = qsoB
                        qsoB_index = idxB
                        break
                    # Ошибка в позывном у партнера (например записал RC0L вместо RX0L)
                    elif not partner_bad_call:
                        partner_bad_call = (qsoB['his_call'].upper(), idxB, int(diff_min))

            # Если корреспондент ошибся в вашем позывном
            if not matched_qsoB and partner_bad_call:
                bad_call, idx_b, diff_m = partner_bad_call
                ubn_lines.append(f"{prefix}\t-\t-\t-\t[{callB} QSO:{idx_b}], Partner bad callsign ({bad_call}/{callA} [{diff_m}])")
                continue

            # Связь отсутствует в отчете корреспондента (NIL)
            if not matched_qsoB:
                ubn_lines.append(f"{prefix}\t-\t-\t-\t[{callB}], Not in log (NIL)")
                continue

            # 4. Проверка совпадения принятого контрольного номера (Receive error)
            sent_by_B = f"{matched_qsoB['rst_s']} {matched_qsoB['exch_s']}"
            rcvd_by_A = f"{rst_rA} {exch_rA}"

            if rcvd_by_A != sent_by_B:
                ubn_lines.append(f"{prefix}\t-\t-\t-\t[{callB} QSO:{qsoB_index}], Receive error ({rcvd_by_A}/{sent_by_B})")
                continue

            # 5. Связь полностью подтверждена!
            seen_qsos[qso_key] = idx
            valid_qsos += 1
            pts = 1
            total_points += pts

            # Расчет бонусных очков за новый позывной на диапазоне
            band_call_key = f"{callB}_{band_str}"
            if band_call_key not in seen_callsigns_by_band:
                seen_callsigns_by_band.add(band_call_key)
                extra_pts = 5
                total_extra_points += extra_pts
                ubn_lines.append(f"{prefix}\t{pts}\t{extra_pts}\t-\tNew Callsign '{callB}' at band {band_str} (+{extra_pts} extra points)")
            else:
                ubn_lines.append(f"{prefix}\t{pts}\t-\t-\tConfirmed")

        ubn_lines.extend([
            "=" * 120,
            f"SUMMARY STATS:",
            f"  TOTAL CLAIMED QSOS:     {len(dataA['qsos'])}",
            f"  CONFIRMED QSOS:         {valid_qsos}",
            f"  QSO POINTS:             {total_points}",
            f"  EXTRA BONUS POINTS:     {total_extra_points}",
            f"  FINAL SCORE:            {total_points + total_extra_points}",
            "=" * 120
        ])

        results[callA] = {
            'valid_qsos': valid_qsos,
            'points': total_points + total_extra_points,
            'ubn_text': "\n".join(ubn_lines)
        }

    return results