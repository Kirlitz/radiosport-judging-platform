import json
from datetime import datetime, timedelta
from app.models import db, Competition, ReceivedLog, QSO
from app.cabrillo import parse_cabrillo_file

def calculate_tours(comp):
    tours_config = json.loads(comp.tours) if comp.tours else []
    tours = []
    tour_idx = 1
    
    for block in tours_config:
        try:
            b_start = datetime.strptime(block['start'], '%Y-%m-%dT%H:%M')
            b_end = datetime.strptime(block['end'], '%Y-%m-%dT%H:%M')
        except (ValueError, KeyError):
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
            
    return tours

def run_judging_primorye(comp_id):
    comp = Competition.query.get(comp_id)
    if not comp:
        return
    
    # 1. Очистка старых связей
    QSO.query.filter_by(competition_id=comp_id).delete()
    db.session.commit()
    
    # 2. Загрузка и первичный парсинг отчетов
    logs = ReceivedLog.query.filter_by(competition_id=comp_id).all()
    tours = calculate_tours(comp)
    
    for log in logs:
        log.confirmed_qsos = 0
        log.score = 0
        parsed_qsos = parse_cabrillo_file(log.file_path, log.callsign)
        
        for q in parsed_qsos:
            tour_num = 0
            for t in tours:
                if t['start'] <= q['qso_datetime'] <= t['end']:
                    tour_num = t['num']
                    break
            
            qso_db = QSO(
                competition_id=comp_id,
                log_id=log.id,
                my_call=q['my_call'],
                corr_call=q['corr_call'],
                qso_datetime=q['qso_datetime'],
                band=q['band'],
                mode=q['mode'],
                rst_sent=q['rst_sent'],
                nr_sent=q['nr_sent'],
                rst_rcvd=q['rst_rcvd'],
                nr_rcvd=q['nr_rcvd'],
                tour_num=tour_num,
                is_valid=(tour_num > 0),
                error_reason='' if tour_num > 0 else 'OUT_OF_TOUR'
            )
            db.session.add(qso_db)
    db.session.commit()

    # 3. Проверка внутренних правил (5 минут и повторы)
    for log in logs:
        user_qsos = QSO.query.filter_by(log_id=log.id, is_valid=True).order_by(QSO.qso_datetime).all()
        
        seen_in_tour = set()
        last_corr = None
        last_qso_time = None
        
        for q in user_qsos:
            if q.corr_call == last_corr and last_qso_time:
                time_diff = (q.qso_datetime - last_qso_time).total_seconds() / 60.0
                if time_diff < 5.0:
                    q.is_valid = False
                    q.error_reason = 'RULE_5_MIN_VIOLATION'
                    continue
            
            last_corr = q.corr_call
            last_qso_time = q.qso_datetime
            
            key = (q.tour_num, q.corr_call, q.band, q.mode)
            if key in seen_in_tour:
                q.is_valid = False
                q.error_reason = 'DUPLICATE_QSO'
            else:
                seen_in_tour.add(key)
    db.session.commit()

    # 4. Перекрестная проверка (Кросс-чек)
    time_delta = timedelta(minutes=comp.time_delta_allowed)
    all_valid_qsos = QSO.query.filter_by(competition_id=comp_id, is_valid=True).all()
    
    for q in all_valid_qsos:
        matching_qso = QSO.query.filter(
            QSO.competition_id == comp_id,
            QSO.my_call == q.corr_call,
            QSO.corr_call == q.my_call,
            QSO.band == q.band,
            QSO.mode == q.mode,
            QSO.qso_datetime >= q.qso_datetime - time_delta,
            QSO.qso_datetime <= q.qso_datetime + time_delta
        ).first()
        
        if not matching_qso:
            q.is_valid = False
            q.error_reason = 'NIL_NOT_IN_LOG'
        elif q.nr_sent != matching_qso.nr_rcvd or q.nr_rcvd != matching_qso.nr_sent:
            q.is_valid = False
            q.error_reason = 'EXCHANGE_MISMATCH'

    db.session.commit()

    # 5. Подсчет очков
    for log in logs:
        valid_qsos = QSO.query.filter_by(log_id=log.id, is_valid=True).order_by(QSO.qso_datetime).all()
        log.confirmed_qsos = len(valid_qsos)
        
        total_score = 0
        seen_corrs_per_band = set()
        
        for q in valid_qsos:
            base_points = 2 if q.band == '160m' else 1
            
            corr_key = (q.band, q.corr_call)
            bonus_points = 0
            if corr_key not in seen_corrs_per_band:
                seen_corrs_per_band.add(corr_key)
                bonus_points = 5
                
            q.points = base_points + bonus_points
            total_score += q.points
            
        log.score = total_score
        
    comp.is_judged = True
    db.session.commit()