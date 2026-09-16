from flask import Blueprint, render_template, session, redirect, url_for
from app.models import Competition, ReceivedLog
from app.utils import get_official_logs

main_bp = Blueprint('main', __name__)

@main_bp.route('/results/<int:comp_id>')
def show_results(comp_id):
    # Проверка: доступ только для авторизованных администраторов
    if not session.get('admin_logged'):
        return redirect(url_for('admin.login'))

    comp = Competition.query.get_or_404(comp_id)
    # Только официальные (последние) отчеты — дубли не попадают в протокол
    logs = get_official_logs(comp_id)

    # 1. Группировка отчетов текущего соревнования по категориям
    grouped_results = {}
    for log in logs:
        cat = log.category if log.category else "Без категории"
        if cat not in grouped_results:
            grouped_results[cat] = []
        grouped_results[cat].append(log)
        
    # 2. Безопасная сортировка участников по очкам и подтвержденным QSO
    for cat in grouped_results:
        grouped_results[cat].sort(
            key=lambda x: (
                x.score if x.score is not None else 0,
                x.confirmed_qsos if x.confirmed_qsos is not None else 0,
                x.claimed_qsos if x.claimed_qsos is not None else 0
            ),
            reverse=True
        )
        
    # 3. Формируем logs_by_comp для нижней карточки
    all_competitions = Competition.query.all()
    logs_by_comp = {}
    for c in all_competitions:
        c_logs = ReceivedLog.query.filter_by(competition_id=c.id).all()
        if c_logs:
            logs_by_comp[c.name] = list(dict.fromkeys(l.callsign for l in c_logs))
        
    return render_template(
        'results.html', 
        comp=comp, 
        grouped_results=grouped_results,
        logs_by_comp=logs_by_comp
    )