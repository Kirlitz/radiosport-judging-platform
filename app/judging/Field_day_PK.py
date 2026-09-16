import math
import json
from datetime import datetime, timedelta
from app.models import db, Competition, ReceivedLog, QSO
from app.cabrillo import parse_cabrillo_file
from app.utils import get_official_logs, full_exchange, get_qso_orders

PLUGIN_TITLE = "Чемпионат Приморского края на УКВ"

# --- 1. ГЕОГРАФИЧЕСКИЙ МОДУЛЬ ДЛЯ УКВ ---

def qth_to_deg(qth: str) -> tuple:
    """Перевод QTH-локатора (например, PN53VG) в координаты (широта, долгота)"""
    qth = str(qth).strip().upper()
    if len(qth) < 6: 
        return 0.0, 0.0
    try:
        lon = float((ord(qth[0]) - ord('A')) * 20 - 180) + float((ord(qth[2]) - ord('0')) * 2) + float((ord(qth[4]) - ord('A')) * 5 / 60 + 2.5 / 60)
        lat = float((ord(qth[1]) - ord('A')) * 10 - 90) + float((ord(qth[3]) - ord('0'))) + float((ord(qth[5]) - ord('A')) * 2.5 / 60 + 1.25 / 60)
        return lat, lon
    except Exception: 
        return 0.0, 0.0

def calculate_distance(qth1: str, qth2: str) -> float:
    """Вычисление расстояния между двумя локаторами в километрах"""
    if not qth1 or not qth2: 
        return 0.0
    q1, q2 = str(qth1).strip().upper()[:6], str(qth2).strip().upper()[:6]
    if len(q1) < 6 or len(q2) < 6 or q1 == q2: 
        return 0.0
    
    lat1, lon1 = map(math.radians, qth_to_deg(q1))
    lat2, lon2 = map(math.radians, qth_to_deg(q2))
    d = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371.0 * (2 * math.asin(math.sqrt(d)))

def calculate_points(distance: float, band: str) -> int:
    """Расчет очков: 1 очко за 10 км (144МГц), x3 для 433МГц, x4 для 1296МГц"""
    clean_band = str(band).strip().upper()
    
    # Определение множителя диапазона
    if clean_band.startswith("14") or "2M" in clean_band:
        mult = 1
    elif clean_band.startswith("43") or "70CM" in clean_band:
        mult = 3
    elif clean_band.startswith("12") or "23CM" in clean_band:
        mult = 4
    else:
        mult = 1

    # Классическое математическое округление расстояния до целых километров
    rounded_distance = int(distance + 0.5)

    # Минимальное начисление, если участники в одном квадрате (или расстояние < 0.5 км)
    if rounded_distance == 0: 
        return mult
    
    # 1 очко за каждые полные/неполные 10 км (уже от округленного расстояния)
    return int(math.ceil(rounded_distance / 10.0) * mult)


# --- 2. БАЗОВЫЕ ФУНКЦИИ СУДЕЙСТВА ---

def calculate_tours(comp):
    tours_config = json.loads(comp.tours) if comp.tours else []
    tours = []
    tour_idx = 1
    
    for block in tours_config:
        try:
            b_start = datetime.strptime(block['start'], '%Y-%m-%dT%H:%M')
            b_end = datetime.strptime(block['end'], '%Y-%m-%dT%H:%M')
        except (ValueError, KeyError, TypeError):
            continue

        divide_by = block.get('divide_by', 'none')
        divide_val = block.get('divide_value', 0)
        
        if divide_by == 'duration' and divide_val > 0:
            curr = b_start
            step = timedelta(minutes=divide_val)
            while curr < b_end:
                nxt = min(curr + step, b_end)
                tours.append({'num': tour_idx, 'start': curr, 'end': nxt})
                tour_idx += 1
                curr = nxt
        elif divide_by == 'count' and divide_val > 0:
            total_sec = (b_end - b_start).total_seconds()
            step_sec = total_sec / divide_val
            curr = b_start
            for _ in range(divide_val):
                nxt = curr + timedelta(seconds=step_sec)
                tours.append({'num': tour_idx, 'start': curr, 'end': nxt})
                tour_idx += 1
                curr = nxt
        else:
            tours.append({'num': tour_idx, 'start': b_start, 'end': b_end})
            tour_idx += 1

    if not tours and getattr(comp, 'start_date', None) and getattr(comp, 'end_date', None):
        tours.append({'num': 1, 'start': comp.start_date, 'end': comp.end_date})
            
    return tours

def run_judging(comp_id):
    print(f"Запущено судейство (УКВ) для соревнования ID: {comp_id}")
    run_judging_vhf(comp_id)

def _norm(val):
    s = str(val).strip().upper()
    return str(int(s)) if s.isdigit() else s

def run_judging_vhf(comp_id):
    comp = Competition.query.get(comp_id)
    if not comp:
        print(f"Соревнование ID {comp_id} не найдено.")
        return
    
    # 1. Очистка старых связей
    QSO.query.filter_by(competition_id=comp_id).delete()
    db.session.commit()
    
    # 2. Загрузка отчетов и подготовка словаря локаторов
    # Только официальные (последние) отчеты: дубли не судятся и не засчитываются
    logs = get_official_logs(comp_id)
    tours = calculate_tours(comp)
    num_tours = len(tours)
    
    log_map = {}
    for lg in logs:
        qth = getattr(lg, 'location', getattr(lg, 'qth', getattr(lg, 'grid_locator', '')))
        log_map[lg.callsign.upper()] = qth
    
    for log in logs:
        parsed_qsos = parse_cabrillo_file(log.file_path, log.callsign)
        
        claimed_q_pts = 0
        my_qth = log_map.get(log.callsign.upper(), '')
        
        for q in parsed_qsos:
            band = str(q.get('band', '')).strip().lower()
            mode = str(q.get('mode', '')).strip().upper()
            my_call = str(q.get('my_call', '')).strip().upper()
            corr_call = str(q.get('corr_call', '')).strip().upper()
            nr_sent = str(q.get('nr_sent', '')).strip().upper()
            nr_rcvd = str(q.get('nr_rcvd', '')).strip().upper()
            rst_sent = str(q.get('rst_sent', '')).strip().upper()
            rst_rcvd = str(q.get('rst_rcvd', '')).strip().upper()

            corr_qth = log_map.get(corr_call, '')
            dist = calculate_distance(my_qth, corr_qth)
            pts = calculate_points(dist, band)
            claimed_q_pts += pts
            
            tour_num = 0
            qso_time = q['qso_datetime']
            
            for idx, t in enumerate(tours):
                is_last_tour = (idx == num_tours - 1)
                if t['start'] <= qso_time < t['end'] or (is_last_tour and qso_time == t['end']):
                    tour_num = t['num']
                    break
            
            qso_db = QSO(
                competition_id=comp_id,
                log_id=log.id,
                my_call=my_call,
                corr_call=corr_call,
                qso_datetime=qso_time,
                band=band,
                mode=mode,
                rst_sent=rst_sent,
                nr_sent=nr_sent,
                rst_rcvd=rst_rcvd,
                nr_rcvd=nr_rcvd,
                tour_num=tour_num,
                is_valid=(tour_num > 0),
                error_reason='' if tour_num > 0 else 'Связь вне времени тура'
            )
            db.session.add(qso_db)
            
        log.claimed_qsos = len(parsed_qsos)
        log.claimed_qso_points = claimed_q_pts
        log.claimed_mult = 0
        log.claimed_score = claimed_q_pts

    db.session.commit()

    # 3. Перекрестная проверка (Кросс-чек)
    time_delta_mins = getattr(comp, 'time_delta_allowed', 3) or 3
    time_delta = timedelta(minutes=time_delta_mins)
    
    candidate_qsos = QSO.query.filter_by(competition_id=comp_id, is_valid=True).all()
    qso_orders = get_qso_orders(comp_id)
    
    for q in candidate_qsos:
        partner_qsos = QSO.query.filter(
            QSO.competition_id == comp_id,
            QSO.my_call == q.corr_call,
            QSO.band == q.band,
            QSO.mode == q.mode,
            QSO.tour_num > 0,
            QSO.qso_datetime >= q.qso_datetime - time_delta,
            QSO.qso_datetime <= q.qso_datetime + time_delta
        ).order_by(QSO.qso_datetime).all()

        exact = None
        partner_wrong_call = None
        for cand in partner_qsos:
            if cand.corr_call == q.my_call:
                exact = cand
                break
            sent_ok = (_norm(q.rst_sent) == _norm(cand.rst_rcvd)) and (_norm(q.nr_sent) == _norm(cand.nr_rcvd))
            rcvd_ok = (_norm(q.rst_rcvd) == _norm(cand.rst_sent)) and (_norm(q.nr_rcvd) == _norm(cand.nr_sent))
            if sent_ok and rcvd_ok:
                partner_wrong_call = cand

        if exact is None:
            # 1. Корреспондент записал наш позывной с ошибкой
            if partner_wrong_call is not None:
                q.is_valid = False
                q.error_reason = (
                    f"Корреспондент ошибся в позывном (записал {partner_wrong_call.corr_call} "
                    f"вместо {q.my_call}) ({q.corr_call} QSO: "
                    f"{qso_orders.get(partner_wrong_call.log_id, {}).get(partner_wrong_call.id, '?')})"
                )
                continue

            # 2. Мы записали позывной корреспондента с ошибкой:
            #    ищем в других отчетах связь, где наш позывной записан верно
            real_qsos = QSO.query.filter(
                QSO.competition_id == comp_id,
                QSO.corr_call == q.my_call,
                QSO.band == q.band,
                QSO.mode == q.mode,
                QSO.tour_num > 0,
                QSO.qso_datetime >= q.qso_datetime - time_delta,
                QSO.qso_datetime <= q.qso_datetime + time_delta
            ).all()
            real_cand = None
            for cand in real_qsos:
                if cand.my_call == q.corr_call:
                    continue
                sent_ok = (_norm(q.rst_sent) == _norm(cand.rst_rcvd)) and (_norm(q.nr_sent) == _norm(cand.nr_rcvd))
                rcvd_ok = (_norm(q.rst_rcvd) == _norm(cand.rst_sent)) and (_norm(q.nr_rcvd) == _norm(cand.nr_sent))
                if sent_ok and rcvd_ok:
                    real_cand = cand
                    break
            if real_cand is not None:
                q.is_valid = False
                q.error_reason = (
                    f"Неправильный позывной: реальный позывной корреспондента {real_cand.my_call} "
                    f"(в отчете записан {q.corr_call}) ({real_cand.my_call} QSO: "
                    f"{qso_orders.get(real_cand.log_id, {}).get(real_cand.id, '?')})"
                )
                continue

            # 3. Связь не найдена вовсе
            q.is_valid = False
            if q.corr_call not in log_map:
                q.error_reason = 'Нет отчета корреспондента'
            else:
                q.error_reason = 'Нет связи в отчете корреспондента'
            continue

        sent_ok = (_norm(q.rst_sent) == _norm(exact.rst_rcvd)) and (_norm(q.nr_sent) == _norm(exact.nr_rcvd))
        rcvd_ok = (_norm(q.rst_rcvd) == _norm(exact.rst_sent)) and (_norm(q.nr_rcvd) == _norm(exact.nr_sent))

        if sent_ok and rcvd_ok:
            continue

        q.is_valid = False
        messages = []
        if not sent_ok:
            messages.append(
                f"Ошибка корреспондента (переданный {full_exchange(q.rst_sent, q.nr_sent)} "
                f"/ принятый корреспондентом {full_exchange(exact.rst_rcvd, exact.nr_rcvd)})"
            )
        if not rcvd_ok:
            messages.append(
                f"Ошибка в принятом номере (переданный корреспондентом {full_exchange(exact.rst_sent, exact.nr_sent)} "
                f"/ записанный в отчете {full_exchange(q.rst_rcvd, q.nr_rcvd)})"
            )
        q.error_reason = "; ".join(messages)

    db.session.commit()

    # 4. Проверка внутренних правил (Повторы и правило 2 минут)
    for log in logs:
        user_qsos = QSO.query.filter_by(log_id=log.id).order_by(QSO.qso_datetime, QSO.id).all()
        order = {q.id: i + 1 for i, q in enumerate(user_qsos)}
        
        seen_in_tour = {}
        current_tour = None
        
        prev_corr = None
        prev_id = None
        prev_time = None
        
        for q in user_qsos:
            if q.tour_num == 0:
                continue

            if q.tour_num != current_tour:
                current_tour = q.tour_num
                seen_in_tour.clear()
                prev_corr = None
                prev_id = None
                prev_time = None

            corr = q.corr_call
            key = (q.tour_num, corr, q.band)
            first_in_tour = seen_in_tour.get(key)

            if first_in_tour is not None:
                if q.is_valid:
                    q.is_valid = False
                    q.error_reason = f'Повторная связь на диапазоне (QSO: {order[first_in_tour]})'
            
            if prev_corr == corr:
                if prev_time and (q.qso_datetime - prev_time) < timedelta(minutes=2):
                    if q.is_valid:
                        q.is_valid = False
                        q.error_reason = f'Нарушение правила 2 минут между повторными связями (предыдущая связь QSO: {order.get(prev_id)})'
            
            if q.is_valid:
                seen_in_tour.setdefault(key, q.id)
                
            prev_corr = corr
            prev_id = q.id
            prev_time = q.qso_datetime

    db.session.commit()

    # 5. Подсчет подтвержденных очков на основе дистанций
    for log in logs:
        valid_qsos = QSO.query.filter_by(log_id=log.id, is_valid=True).order_by(QSO.qso_datetime).all()
        log.confirmed_qsos = len(valid_qsos)
        
        conf_q_pts = 0
        my_qth = log_map.get(log.callsign.upper(), '')
        
        for q in valid_qsos:
            corr_qth = log_map.get(q.corr_call, '')
            dist = calculate_distance(my_qth, corr_qth)
            base_points = calculate_points(dist, q.band)
            
            conf_q_pts += base_points
            q.points = base_points
                
        log.confirmed_qso_points = conf_q_pts
        log.confirmed_mult = 0
        log.score = conf_q_pts
        
    comp.is_judged = True
    db.session.commit()
    print(f"Судейство УКВ для соревнования ID {comp_id} успешно завершено.")