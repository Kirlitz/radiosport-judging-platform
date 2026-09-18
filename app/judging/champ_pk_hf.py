import json
from datetime import datetime, timedelta
from app.models import db, Competition, ReceivedLog, QSO
from app.cabrillo import parse_cabrillo_file
from app.utils import get_official_logs, full_exchange, get_qso_orders, category_rules_map, out_of_category_reason

PLUGIN_TITLE = "Чемпионат Приморского края на КВ - смесь"

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
    """
    Главная точка входа, вызываемая менеджером плагинов
    """
    print(f"Запущено судейство для соревнования ID: {comp_id}")
    run_judging_primorye(comp_id)


def _norm(val):
    """
    Нормализация значений для сравнения (убирает лишние нули в начале чисел)
    """
    s = str(val).strip().upper()
    return str(int(s)) if s.isdigit() else s


def _is_mirror(q, cand):
    """
    True, если связь cand в отчете корреспондента зеркальна нашей связи q:
    переданные нами RST+номер совпадают с принятыми корреспондентом и наоборот.
    """
    sent_ok = (_norm(q.rst_sent) == _norm(cand.rst_rcvd)) and (_norm(q.nr_sent) == _norm(cand.nr_rcvd))
    rcvd_ok = (_norm(q.rst_rcvd) == _norm(cand.rst_sent)) and (_norm(q.nr_rcvd) == _norm(cand.nr_sent))
    return sent_ok and rcvd_ok


def run_judging_primorye(comp_id):
    comp = Competition.query.get(comp_id)
    if not comp:
        print(f"Соревнование ID {comp_id} не найдено.")
        return
    
    # 1. Очистка старых связей
    QSO.query.filter_by(competition_id=comp_id).delete()
    db.session.commit()
    
    # 2. Загрузка и первичный парсинг отчетов
    # Только официальные (последние) отчеты: дубли не судятся и не засчитываются
    logs = get_official_logs(comp_id)
    tours = calculate_tours(comp)
    num_tours = len(tours)
    rules_map = category_rules_map(comp.categories)
    
    for log in logs:
        rules = rules_map.get(log.category) if log.category else None
        parsed_qsos = parse_cabrillo_file(log.file_path, log.callsign)
        
        claimed_q_pts = 0
        claimed_mult_pts = 0
        claimed_seen_corrs = set()
        
        for q in parsed_qsos:
            band = str(q.get('band', '')).strip().lower()
            mode = str(q.get('mode', '')).strip().upper()
            my_call = str(q.get('my_call', '')).strip().upper()
            corr_call = str(q.get('corr_call', '')).strip().upper()
            nr_sent = str(q.get('nr_sent', '')).strip().upper()
            nr_rcvd = str(q.get('nr_rcvd', '')).strip().upper()
            rst_sent = str(q.get('rst_sent', '')).strip().upper()
            rst_rcvd = str(q.get('rst_rcvd', '')).strip().upper()

            base_p = 2 if band == '160m' else 1
            claimed_q_pts += base_p
            
            corr_key = (band, corr_call)
            if corr_key not in claimed_seen_corrs:
                claimed_seen_corrs.add(corr_key)
                claimed_mult_pts += 5
            
            tour_num = 0
            qso_time = q['qso_datetime']
            
            # Определение тура (полуинтервалы)
            for idx, t in enumerate(tours):
                is_last_tour = (idx == num_tours - 1)
                if t['start'] <= qso_time < t['end'] or (is_last_tour and qso_time == t['end']):
                    tour_num = t['num']
                    break

            is_valid = tour_num > 0
            error_reason = '' if is_valid else 'Связь вне времени тура'
            # Связи на диапазонах/видах модуляции, не входящих в зачетную группу
            # участника, не учитываются (0 очков), но остаются в протоколе
            # как «вне зачета».
            if is_valid:
                cat_reason = out_of_category_reason(rules, band, mode)
                if cat_reason:
                    is_valid = False
                    error_reason = cat_reason

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
                is_valid=is_valid,
                error_reason=error_reason
            )
            db.session.add(qso_db)
            
        log.claimed_qsos = len(parsed_qsos)
        log.claimed_qso_points = claimed_q_pts
        log.claimed_mult = claimed_mult_pts
        log.claimed_score = claimed_q_pts + claimed_mult_pts

    db.session.commit()

    # 3. Перекрестная проверка (Кросс-чек)
    time_delta_mins = getattr(comp, 'time_delta_allowed', 3) or 3
    time_delta = timedelta(minutes=time_delta_mins)

    official_calls = {lg.callsign.upper() for lg in logs}

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
        exact_time_diff = None
        exact_matches = False
        partner_wrong_call = None
        for cand in partner_qsos:
            if cand.corr_call == q.my_call:
                # Среди всех QSO корреспондента с нашим позывным в окне времени
                # выбираем настоящее зеркало: ближайшее по времени, и предпочтительно
                # с полностью совпадающими RST+номером (повторные связи в разных
                # турах не должны «перехватывать» друг друга).
                diff = abs((cand.qso_datetime - q.qso_datetime).total_seconds())
                matches = _is_mirror(q, cand)
                if (exact is None
                        or (matches and not exact_matches)
                        or (matches == exact_matches and diff < exact_time_diff)):
                    exact = cand
                    exact_time_diff = diff
                    exact_matches = matches
                continue
            if _is_mirror(q, cand) and partner_wrong_call is None:
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
            real_cand_diff = None
            for cand in real_qsos:
                if cand.my_call == q.corr_call:
                    continue
                if _is_mirror(q, cand):
                    diff = abs((cand.qso_datetime - q.qso_datetime).total_seconds())
                    if real_cand is None or diff < real_cand_diff:
                        real_cand = cand
                        real_cand_diff = diff
            if real_cand is not None:
                q.is_valid = False
                q.error_reason = (
                    f"Неправильный позывной: реальный позывной корреспондента {real_cand.my_call} "
                    f"(в отчете записан {q.corr_call}) ({real_cand.my_call} QSO: "
                    f"{qso_orders.get(real_cand.log_id, {}).get(real_cand.id, '?')})"
                )
                continue

            q.is_valid = False
            if q.corr_call not in official_calls:
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

    # 4. Проверка внутренних правил (Правило 5 минут + Повторы)
    for log in logs:
        # Берем ВСЕ связи участника в хронологическом порядке
        user_qsos = QSO.query.filter_by(log_id=log.id).order_by(QSO.qso_datetime).all()
        order = {q.id: i + 1 for i, q in enumerate(user_qsos)}
        
        seen_in_tour = {}
        last_corr = None
        last_id = None
        last_qso_time = None
        current_tour = None
        
        for q in user_qsos:
            if q.tour_num == 0:
                continue

            if q.tour_num != current_tour:
                current_tour = q.tour_num
                last_corr = None
                last_id = None
                last_qso_time = None
                seen_in_tour.clear()

            corr = q.corr_call
            
            # Проверка правила 5 минут
            is_5_min_violation = False
            if corr == last_corr and last_qso_time is not None:
                time_diff = (q.qso_datetime - last_qso_time).total_seconds() / 60.0
                if time_diff < 5.0:
                    is_5_min_violation = True
            
            # Проверка повторных связей
            key = (q.tour_num, corr, q.band, q.mode)
            first_in_tour = seen_in_tour.get(key)

            if first_in_tour is not None:
                if q.is_valid:
                    q.is_valid = False
                    q.error_reason = f'Повторная связь на диапазоне (QSO: {order[first_in_tour]})'
            elif is_5_min_violation:
                if q.is_valid:
                    q.is_valid = False
                    q.error_reason = f'Нарушение правила 5 минут (предыдущая связь с этим корреспондентом QSO: {order.get(last_id)})'
            
            # ВАЖНО: Добавляем в "засчитанные связи тура" ТОЛЬКО если она в итоге осталась валидной!
            # Если кросс-чек забраковал связь ранее (is_valid == False), она не считается дублем для будущих связей.
            if q.is_valid:
                seen_in_tour.setdefault(key, q.id)
                
            # А вот этого корреспондента записываем как "последнего" в любом случае - 
            # даже ошибочная связь прерывает цепочку для правила 5 минут.
            last_corr = corr
            last_id = q.id
            last_qso_time = q.qso_datetime

    db.session.commit()

    # 5. Подсчет подтвержденных очков
    for log in logs:
        valid_qsos = QSO.query.filter_by(log_id=log.id, is_valid=True).order_by(QSO.qso_datetime).all()
        log.confirmed_qsos = len(valid_qsos)
        
        conf_q_pts = 0
        conf_mult_pts = 0
        conf_seen_corrs = set()
        
        for q in valid_qsos:
            base_points = 2 if q.band == '160m' else 1
            conf_q_pts += base_points
            q.points = base_points
            
            corr_key = (q.band, q.corr_call)
            if corr_key not in conf_seen_corrs:
                conf_seen_corrs.add(corr_key)
                conf_mult_pts += 5
                
        log.confirmed_qso_points = conf_q_pts
        log.confirmed_mult = conf_mult_pts
        log.score = conf_q_pts + conf_mult_pts
        
    comp.is_judged = True
    db.session.commit()
    print(f"Судейство для соревнования ID {comp_id} успешно завершено.")