import threading

from flask import current_app

from app.models import db, Competition
from app.plugins import get_judge_entrypoint


def _run_judging_threaded(app, comp_id):
    """Выполняет судейство в фоновом потоке (V-08)."""
    with app.app_context():
        try:
            comp = Competition.query.get(comp_id)
            if comp is None:
                return
            entrypoint, error = get_judge_entrypoint(comp)
            if entrypoint is None:
                raise RuntimeError(error or 'Плагин судейства не найден')
            entrypoint(comp_id)
            comp = Competition.query.get(comp_id)
            if comp is not None:
                comp.is_judged = True
                comp.judging_status = 'done'
                comp.judging_message = 'Судейство завершено'
                db.session.commit()
        except Exception as e:
            db.session.rollback()
            try:
                comp = Competition.query.get(comp_id)
                if comp is not None:
                    comp.judging_status = 'error'
                    comp.judging_message = str(e)[:500]
                    db.session.commit()
            except Exception:
                db.session.rollback()
            current_app.logger.exception("Судейство соревнования #%s упало", comp_id)


def start_judging(comp_id):
    """Запускает судейство соревнования в фоновом потоке.

    Возвращает (ok, error_message). Повторный запуск во время работы
    отклоняется, чтобы не дублировать фоновые потоки.
    """
    app = current_app._get_current_object()
    comp = Competition.query.get(comp_id)
    if comp is None:
        return False, 'Соревнование не найдено.'
    if comp.judging_status == 'running':
        return False, 'Судейство уже запущено и выполняется в фоне.'

    comp.judging_status = 'running'
    comp.judging_message = 'Судейство выполняется...'
    db.session.commit()

    thread = threading.Thread(
        target=_run_judging_threaded, args=(app, comp_id), daemon=True
    )
    thread.start()
    return True, None