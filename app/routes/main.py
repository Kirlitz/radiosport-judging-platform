from flask import Blueprint, render_template
from app.models import Competition, ReceivedLog
from app.utils import get_grouped_results
from app.auth import permission_required

main_bp = Blueprint('main', __name__)


def _logs_by_comp():
    """Позывные участников по соревнованиям для нижней карточки.
    Один запрос ко всем логам вместо N+1 (V-08)."""
    comps = Competition.query.all()
    comp_ids = {c.id for c in comps}
    logs_by_comp = {}
    buckets = {}
    for lg in ReceivedLog.query.all():
        if lg.competition_id not in comp_ids:
            continue
        bucket = buckets.setdefault(lg.competition_id, [])
        call = (lg.callsign or '').strip().upper()
        if call and call not in bucket:
            bucket.append(call)
    for c in comps:
        calls = buckets.get(c.id)
        if calls:
            logs_by_comp[c.name] = calls
    return logs_by_comp


@main_bp.route('/results/<int:comp_id>')
@permission_required('competitions.export')
def show_results(comp_id):
    comp = Competition.query.get_or_404(comp_id)
    grouped_results = get_grouped_results(comp)

    return render_template(
        'results.html',
        comp=comp,
        grouped_results=grouped_results,
        logs_by_comp=_logs_by_comp()
    )