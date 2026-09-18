import json
import os
import io
import re
import zipfile
import shutil
from collections import defaultdict
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, current_app, send_file, abort
from app.models import db, User, Permission, UserPermission, Competition, ReceivedLog, QSO, Operator
from app.cabrillo import parse_cabrillo_file
from app.auth import permission_required, get_current_user, generate_session_token, hash_session_token
from app import limiter, _client_ip
from app.plugins import get_available_plugins
from app.judge import start_judging
from app.timeutils import utcnow, parse_local_dt_input, dt_for_edit_form, utc_to_zone_str
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from app.utils import get_official_logs, get_grouped_results, get_categories_list, parse_categories, category_rules_map, out_of_category_reason
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# Логин: 3-50 символов, латинские буквы, цифры, "._-"
USERNAME_RE = re.compile(r'^[A-Za-z0-9_.-]{3,50}$')

# Допустимая длина названия зачетной группы (совпадает с admin_edit.html)
CATEGORY_MAX_LENGTH = 110

# Доступные тайм-зоны для конструктора соревнований (V-10)
TIMEZONE_LIST = [
    ('UTC', 'UTC (всемирное время)'),
    ('Europe/Kaliningrad', 'Калининград (UTC+2)'),
    ('Europe/Moscow', 'Москва (UTC+3)'),
    ('Europe/Samara', 'Самара (UTC+4)'),
    ('Asia/Yekaterinburg', 'Екатеринбург (UTC+5)'),
    ('Asia/Omsk', 'Омск (UTC+6)'),
    ('Asia/Novosibirsk', 'Новосибирск (UTC+7)'),
    ('Asia/Krasnoyarsk', 'Красноярск (UTC+7)'),
    ('Asia/Irkutsk', 'Иркутск (UTC+8)'),
    ('Asia/Yakutsk', 'Якутск (UTC+9)'),
    ('Asia/Vladivostok', 'Владивосток (UTC+10)'),
    ('Asia/Magadan', 'Магадан (UTC+11)'),
    ('Asia/Kamchatka', 'Камчатка (UTC+12)'),
]

# Учетные записи, которые нельзя удалить или лишить полных прав —
# "запасной" доступ к панели при любых манипуляциях с правами.
PROTECTED_USERNAMES = {'admin'}

# Заглушка для выравнивания времени ответа при несуществующем логине
# (защита от определения существования аккаунта по задержке).
_DUMMY_HASH = generate_password_hash('invalid-password-placeholder-for-timing')


def _validate_plugin(script_name, fallback_seed='champ_pk_hf'):
    """Белый список плагинов судейства (F3): допускается только имя файла,
    реально существующего в app/judging/. При недопустимом имени выбирается
    реально доступный плагин (сначала похожий на script/fallback_seed)."""
    allowed = {p['id'] for p in get_available_plugins()}
    if script_name in allowed:
        return script_name
    current_app.logger.warning(
        "Неизвестное имя плагина судейства '%s' подменено на доступный плагин",
        script_name,
    )
    if fallback_seed in allowed:
        return fallback_seed
    for preferred in ('champ_pk_hf',):
        if preferred in allowed:
            return preferred
    return sorted(allowed)[0] if allowed else ''


def _excel_safe(value):
    """Нейтрализация formula injection (F1, CWE-1236): значение, начинающееся
    с '=', '+', '-', '@', не должно интерпретироваться Excel как формула."""
    if isinstance(value, str) and value.startswith(('=', '+', '-', '@')):
        return "'" + value
    return value

def generate_ubn_text(log, comp):
    """Формирует UBN-протокол для участника в стиле QSO Tournament Studio"""
    qsos = QSO.query.filter_by(log_id=log.id).order_by(QSO.qso_datetime).all()
    
    operators_str = ", ".join([op.fio for op in log.operators if op.fio]) if log.operators else '-'
    
    lines = []
    lines.append("=" * 85)
    lines.append(f"ПРОТОКОЛ ПРОВЕРКИ И ОЧИСТКИ ОТЧЕТА (UBN)")
    lines.append(f"Соревнование: {comp.name}")
    lines.append(f"Позывной:    {log.callsign}")
    lines.append(f"Категория:    {log.category or '-'}")
    lines.append(f"Участник(и):  {operators_str}")
    lines.append("=" * 85)
    lines.append("")
    lines.append("СВОДНЫЕ РЕЗУЛЬТАТЫ:")
    lines.append(f"  Заявлено:    QSO: {log.claimed_qsos or 0:<4} | Очки: {log.claimed_qso_points or 0:<5} | Множитель: {log.claimed_mult or 0:<4} | Итог: {log.claimed_score or 0}")
    lines.append(f"  Подтверждено: QSO: {log.confirmed_qsos or 0:<4} | Очки: {log.confirmed_qso_points or 0:<5} | Множитель: {log.confirmed_mult or 0:<4} | Итог: {log.score or 0}")
    lines.append("-" * 85)
    lines.append("")
    lines.append("ПРОТОКОЛ СВЯЗЕЙ (QSO PROTOCOL):")
    lines.append(f"{'Дата/Время (UTC)':<17} {'Диап':<6} {'Вид':<4} {'Передано':<10} {'Позывной':<10} {'Принято':<10} {'Очки':<5} {'Множ':<5} {'Статус / Ошибка'}")
    lines.append("-" * 85)
    
    error_map = {
        'OUT_OF_TOUR': 'Вне тура',
        'RULE_5_MIN_VIOLATION': 'Нарушение правила 5 минут',
        'DUPLICATE_QSO': 'Повторная связь (Дубль)',
        'NIL_NOT_IN_LOG': 'Нет в отчете корреспондента (NIL)',
        'EXCHANGE_MISMATCH': 'Ошибка в контрольном номере',
    }

    # Зачетные ограничения группы участника: связи на запрещенных для группы
    # диапазонах/видах модуляции выводятся как «вне зачета».
    rules_map = category_rules_map(comp.categories)
    rules = rules_map.get(log.category) if log.category else None

    seen_mults = set()
    for q in qsos:
        dt_str = q.qso_datetime.strftime('%Y-%m-%d %H:%M') if q.qso_datetime else '----'
        sent_str = f"{q.rst_sent or ''} {q.nr_sent or ''}".strip()
        rcvd_str = f"{q.rst_rcvd or ''} {q.nr_rcvd or ''}".strip()
        
        if q.is_valid:
            pts = q.points if q.points is not None else (2 if q.band == '160m' else 1)
            mult_key = (q.band, q.corr_call)
            if mult_key not in seen_mults:
                seen_mults.add(mult_key)
                mult_pts = 5
            else:
                mult_pts = 0
            status = "OK"
        else:
            pts = 0
            mult_pts = 0
            out_reason = out_of_category_reason(rules, q.band, q.mode)
            if out_reason:
                status = out_reason
            else:
                status = error_map.get(q.error_reason, q.error_reason or "Ошибка")
            
        lines.append(f"{dt_str:<17} {q.band:<6} {q.mode:<4} {sent_str:<10} {q.corr_call:<10} {rcvd_str:<10} {pts:<5} {mult_pts:<5} {status}")
        
    lines.append("-" * 85)
    return "\n".join(lines)

def _login_bucket_key():
    """Ключ для rate limit логина: IP + логин. Распределённый брутфорс одного
    логина с разных IP всё равно упрётся в лимит по данной паре (F4)."""
    username = (request.form.get('username') or '').strip().lower()
    return f"{_client_ip()}|{username or '?'}"


def _invalidate_sessions(user):
    """Аннулирует все активные сессии пользователя (F8)."""
    user.session_token_hash = None


# ---------------------------------------------------------------------------
# Аутентификация
# ---------------------------------------------------------------------------
@admin_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per 15 minutes", methods=["POST"])
@limiter.limit("8 per 15 minutes", key_func=_login_bucket_key, methods=["POST"])
def login():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        user = User.query.filter_by(username=username).first()

        # Проверяем хэш и при несуществующем логине (заглушка), чтобы время
        # ответа не выдавало, зарегистрирован ли такой пользователь.
        hash_to_check = user.password_hash if user else _DUMMY_HASH
        password_ok = check_password_hash(hash_to_check, password)

        if user is None or not user.is_active or not password_ok:
            flash('Неверный логин или пароль', 'danger')
        else:
            # session.clear() до установки данных — защита от фиксации сессии
            session.clear()
            auth_token = generate_session_token()
            user.session_token_hash = hash_session_token(auth_token)
            session['user_id'] = user.id
            session['username'] = user.username
            session['auth_token'] = auth_token
            user.last_login_at = datetime.now()
            db.session.commit()
            flash('Успешный вход!', 'success')
            return redirect(url_for('admin.index'))
    return render_template('admin_login.html')

@admin_bp.route('/logout', methods=['POST'])
def logout():
    session.clear()
    flash('Вы вышли из системы.', 'info')
    return redirect(url_for('admin.login'))

# ---------------------------------------------------------------------------
# Соревнования
# ---------------------------------------------------------------------------
@admin_bp.route('/')
@permission_required('competitions.view')
def index():
    competitions = Competition.query.order_by(Competition.start_time.desc()).all()
    judging_running = any(c.judging_status == 'running' for c in competitions)
    return render_template('admin_index.html', competitions=competitions,
                           judging_running=judging_running)

def _valid_dt_local(value):
    """Проверка формата datetime-local (YYYY-MM-DDTHH:MM) — защита от записи
    произвольных строк в JSON туров (F2, self-XSS в admin_edit.html)."""
    if not value or not isinstance(value, str):
        return False
    try:
        datetime.strptime(value, '%Y-%m-%dT%H:%M')
        return True
    except ValueError:
        return False


def _form_timezone(form):
    """Определяет выбранную тайм-зону соревнования (V-10):
    'time_mode' == 'UTC' -> всегда UTC; иначе проверенный выбор из списка."""
    mode = form.get('time_mode')
    if mode == 'UTC':
        return 'UTC'
    tz_name = (form.get('comp_timezone') or 'UTC').strip()
    for tz_id, _label in TIMEZONE_LIST:
        if tz_id == tz_name:
            return tz_id
    return 'UTC'


def _parse_competition_form(tz_name='UTC'):
    """Разбор полей формы создания/редактирования соревнования.
    Диапазоны и виды модуляции задаются отдельно для КАЖДОЙ зачетной группы:
    поля cat_bands_<i>[] / cat_modes_<i>[] соответствуют категории с индексом i.
    Времена сеансов переводятся из выбранной зоны в UTC (V-10).
    При слишком длинном названии группы бросает ValueError."""
    raw_names = request.form.getlist('categories[]')
    categories_list = []
    for i, name in enumerate(raw_names):
        name = name.strip()
        if not name:
            continue
        if len(name) > CATEGORY_MAX_LENGTH:
            raise ValueError(
                f'Название зачетной группы не должно превышать {CATEGORY_MAX_LENGTH} '
                f'символов: "{name[:40]}..."'
            )
        bands_list = [b.strip() for b in request.form.getlist(f'cat_bands_{i}[]') if isinstance(b, str) and b.strip()]
        modes_list = [m.strip() for m in request.form.getlist(f'cat_modes_{i}[]') if isinstance(m, str) and m.strip()]
        categories_list.append({
            'name': name,
            'bands': bands_list,
            'modes': modes_list,
        })

    block_starts = request.form.getlist('block_start[]')
    block_ends = request.form.getlist('block_end[]')
    divide_bys = request.form.getlist('divide_by[]')
    divide_values = request.form.getlist('divide_value[]')

    tours_list = []
    for s, e, db_type, db_val in zip(block_starts, block_ends, divide_bys, divide_values):
        if s and e and db_type in ('none', 'duration', 'count') \
                and _valid_dt_local(s) and _valid_dt_local(e):
            tours_list.append({
                'start': parse_local_dt_input(s, tz_name).strftime('%Y-%m-%dT%H:%M'),
                'end': parse_local_dt_input(e, tz_name).strftime('%Y-%m-%dT%H:%M'),
                'divide_by': db_type,
                'divide_value': int(db_val) if db_val and db_val.isdigit() else 0
            })

    return {
        'categories': categories_list,
        'tours': tours_list,
    }

@admin_bp.route('/add', methods=['GET', 'POST'])
@permission_required('competitions.create')
def add_competition():
    if request.method == 'POST':
        tz_name = _form_timezone(request.form)
        try:
            data = _parse_competition_form(tz_name)
            start_time = parse_local_dt_input(request.form.get('start_time'), tz_name)
            end_time = parse_local_dt_input(request.form.get('end_time'), tz_name)
            deadline_time = parse_local_dt_input(request.form.get('deadline_time'), tz_name)
        except ValueError as e:
            flash(f'Ошибка в данных соревнования: {e}', 'danger')
            return redirect(url_for('admin.add_competition'))

        new_comp = Competition(
            name=request.form.get('name'),
            start_time=start_time,
            end_time=end_time,
            deadline_time=deadline_time,
            time_delta_allowed=request.form.get('time_delta', type=int, default=3),
            scoring_script_filename=_validate_plugin(request.form.get('scoring_script_filename', 'champ_pk_hf')),
            timezone=tz_name,
            categories=json.dumps(data['categories'], ensure_ascii=False),
            tours=json.dumps(data['tours'], ensure_ascii=False)
        )
        db.session.add(new_comp)
        db.session.commit()
        return redirect(url_for('admin.index'))

    plugins = get_available_plugins()
    return render_template('admin_edit.html', comp=None, categories_list=[], plugins=plugins,
                           tours_data=[], timezone_list=TIMEZONE_LIST,
                           start_display='', end_display='', deadline_display='',
                           comp_timezone='UTC')

@admin_bp.route('/edit/<int:comp_id>', methods=['GET', 'POST'])
@permission_required('competitions.edit')
def edit_competition(comp_id):
    comp = Competition.query.get_or_404(comp_id)

    if request.method == 'POST':
        tz_name = _form_timezone(request.form)
        try:
            data = _parse_competition_form(tz_name)
            start_time = parse_local_dt_input(request.form.get('start_time'), tz_name)
            end_time = parse_local_dt_input(request.form.get('end_time'), tz_name)
            deadline_time = parse_local_dt_input(request.form.get('deadline_time'), tz_name)
        except ValueError as e:
            flash(f'Ошибка в данных соревнования: {e}', 'danger')
            return redirect(url_for('admin.edit_competition', comp_id=comp.id))

        comp.name = request.form.get('name')
        comp.start_time = start_time
        comp.end_time = end_time
        comp.deadline_time = deadline_time
        comp.time_delta_allowed = request.form.get('time_delta', type=int, default=3)
        comp.scoring_script_filename = _validate_plugin(request.form.get('scoring_script_filename', 'champ_pk_hf'))
        comp.timezone = tz_name

        comp.categories = json.dumps(data['categories'], ensure_ascii=False)
        comp.tours = json.dumps(data['tours'], ensure_ascii=False)

        db.session.commit()
        return redirect(url_for('admin.index'))

    tz_name = (comp.timezone or 'UTC')
    categories_list = parse_categories(comp.categories)
    plugins = get_available_plugins()
    tours_raw = json.loads(comp.tours) if comp.tours else []
    tours_data = [
        {
            **t,
            'start': utc_to_zone_str(t.get('start', ''), tz_name),
            'end': utc_to_zone_str(t.get('end', ''), tz_name),
        }
        for t in tours_raw if isinstance(t, dict)
    ]
    return render_template('admin_edit.html', comp=comp, categories_list=categories_list,
                           plugins=plugins, tours_data=tours_data, timezone_list=TIMEZONE_LIST,
                           start_display=dt_for_edit_form(comp.start_time, tz_name),
                           end_display=dt_for_edit_form(comp.end_time, tz_name),
                           deadline_display=dt_for_edit_form(comp.deadline_time, tz_name),
                           comp_timezone=comp.timezone or 'UTC')

@admin_bp.route('/delete/<int:comp_id>', methods=['POST'])
@permission_required('competitions.delete')
def delete_competition(comp_id):
    comp = Competition.query.get_or_404(comp_id)
    
    logs = ReceivedLog.query.filter_by(competition_id=comp.id).all()
    log_ids = [log.id for log in logs]
    
    for log in logs:
        if hasattr(log, 'file_path') and log.file_path:
            if os.path.exists(log.file_path):
                try:
                    os.remove(log.file_path)
                except Exception as e:
                    current_app.logger.error(f"Ошибка удаления файла {log.file_path}: {e}")
                    
    comp_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], str(comp.id))
    if os.path.exists(comp_dir):
        shutil.rmtree(comp_dir, ignore_errors=True)

    if log_ids:
        Operator.query.filter(Operator.log_id.in_(log_ids)).delete(synchronize_session=False)
        QSO.query.filter(QSO.log_id.in_(log_ids)).delete(synchronize_session=False)
        
    ReceivedLog.query.filter_by(competition_id=comp.id).delete(synchronize_session=False)
    
    db.session.delete(comp)
    db.session.commit()
    
    current_app.logger.warning(
        "Соревнование #%s удалено администратором %s",
        comp.id, get_current_user().username
    )
    flash('Соревнование, все отчеты, операторы и файлы физически удалены.', 'success')
    return redirect(url_for('admin.index'))

@admin_bp.route('/logs/<int:comp_id>')
@permission_required('competitions.logs')
def logs_list(comp_id):
    """Список ВСЕХ поданных отчетов соревнования (включая дубли).
    Официальным считается последний по времени — именно он участвует в судействе."""
    comp = Competition.query.get_or_404(comp_id)
    logs = ReceivedLog.query.filter_by(competition_id=comp.id).order_by(
        ReceivedLog.upload_time.asc(), ReceivedLog.id.asc()
    ).all()
    official_ids = {lg.id for lg in get_official_logs(comp.id)}
    return render_template('admin_logs.html', comp=comp, logs=logs, official_ids=official_ids)

@admin_bp.route('/delete_log/<int:log_id>', methods=['POST'])
@permission_required('competitions.logs')
def delete_log(log_id):
    """Удаление одной подачи: файл + связи + операторы + запись.
    Остальные отчеты (в том числе дубли других участников) не затрагиваются."""
    log = ReceivedLog.query.get_or_404(log_id)
    comp_id = log.competition_id
    callsign = log.callsign

    if log.file_path and os.path.exists(log.file_path):
        try:
            os.remove(log.file_path)
        except OSError as e:
            current_app.logger.error(f"Ошибка удаления файла {log.file_path}: {e}")

    QSO.query.filter_by(log_id=log.id).delete(synchronize_session=False)
    Operator.query.filter_by(log_id=log.id).delete(synchronize_session=False)
    db.session.delete(log)
    db.session.commit()

    flash(f'Отчет {callsign} (#{log.id}) удален.', 'success')
    return redirect(url_for('admin.logs_list', comp_id=comp_id))

@admin_bp.route('/participants/<int:comp_id>')
@permission_required('competitions.logs')
def participants(comp_id):
    """Список участников соревнования: позывной, ФИО, дата рождения, разряд, адрес, email.
    Для станций с несколькими операторами — строки операторов выровнены друг под другом."""
    comp = Competition.query.get_or_404(comp_id)
    logs = get_official_logs(comp.id)
    logs.sort(key=lambda lg: (lg.callsign or '').upper())
    return render_template('admin_participants.html', comp=comp, logs=logs)

@admin_bp.route('/missing/<int:comp_id>')
@permission_required('competitions.logs')
def missing_participants(comp_id):
    """Отсутствующие: корреспонденты, которых упоминали в отчетах, но сами отчет не прислали.
    Первый столбец — позывной, второй — в скольких официальных отчетах он упоминается.
    Работает по сохраненным файлам отчетов, не требуя предварительного судейства."""
    comp = Competition.query.get_or_404(comp_id)
    official_logs = get_official_logs(comp.id)
    submitted = {log.callsign.strip().upper() for log in official_logs if log.callsign}

    mentions = defaultdict(set)
    for log in official_logs:
        if not log.file_path or not os.path.exists(log.file_path):
            continue
        try:
            parsed_qsos = parse_cabrillo_file(log.file_path, log.callsign)
        except Exception:
            current_app.logger.exception(f"Не удалось разобрать отчет {log.file_path}")
            continue
        for q in parsed_qsos:
            call = (q.get('corr_call') or '').strip().upper()
            if not call or call in submitted:
                continue
            mentions[call].add(log.id)

    missing = sorted(((call, len(log_ids)) for call, log_ids in mentions.items()),
                     key=lambda x: (-x[1], x[0]))
    return render_template('admin_missing.html', comp=comp, missing=missing)

@admin_bp.route('/judge/<int:comp_id>', methods=['POST'])
@permission_required('competitions.judge')
def judge_competition(comp_id):
    comp = Competition.query.get_or_404(comp_id)
    ok, error = start_judging(comp.id)
    if ok:
        flash(f'Судейство соревнования "{comp.name}" запущено в фоне. '
              'Страница результатов обновится после завершения.', 'success')
    else:
        flash(error or 'Не удалось запустить судейство.', 'danger')
    return redirect(url_for('admin.index'))

@admin_bp.route('/download_ubn/<int:comp_id>')
@permission_required('competitions.export')
def download_ubn_archive(comp_id):
    comp = Competition.query.get_or_404(comp_id)
    logs = get_official_logs(comp.id)

    if not logs:
        flash("Нет загруженных отчетов для формирования UBN файлов.", "warning")
        return redirect(url_for('admin.index'))
        
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        for log in logs:
            ubn_content = generate_ubn_text(log, comp)
            safe_call = secure_filename(log.callsign) if log.callsign else 'UNKNOWN_CALL'
            file_name = f"{safe_call}.txt"
            zf.writestr(file_name, ubn_content.encode('utf-8'))
            
    memory_file.seek(0)
    safe_comp_name = "".join(c for c in comp.name if c.isalnum() or c in (' ', '_', '-')).strip()
    archive_name = f"UBN_{safe_comp_name}_{comp.id}.zip"
    
    return send_file(
        memory_file,
        mimetype='application/zip',
        as_attachment=True,
        download_name=archive_name
    )

@admin_bp.route('/export_excel/<int:comp_id>')
@permission_required('competitions.export')
def export_excel(comp_id):
    comp = Competition.query.get_or_404(comp_id)
    
    if not comp.is_judged:
        abort(400, description="Соревнование еще не отсужено.")

    grouped_results = get_grouped_results(comp)

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    header_font = Font(bold=True)
    header_fill = PatternFill(start_color="D3D3D3", end_color="D3D3D3", fill_type="solid")
    center_aligned_text = Alignment(horizontal="center", vertical="center")

    # Уникальные, валидные имена листов Excel (макс. 31 символ, запрещены \ / ? * [ ] :)
    used_sheet_names = set()

    def _unique_sheet_name(base):
        sanitized = "".join('-' if ch in '\\/?*[]:' else ch for ch in base).strip() or 'Категория'
        name = sanitized[:31]
        candidate = name
        n = 2
        while candidate in used_sheet_names:
            suffix = f" ({n})"
            candidate = name[:31 - len(suffix)] + suffix
            n += 1
        used_sheet_names.add(candidate)
        return candidate

    for category, logs in grouped_results.items():
        if not logs:
            continue
            
        ws = wb.create_sheet(title=_unique_sheet_name(str(category)))
        
        headers = [
            "Место", "Позывной", "ФИО", "Год рождения", "Разряд", "Субъект РФ",
            "Заявлено: Связей", "Заявлено: Очки QSO", "Заявлено: Доп. очки", "Заявлено: Всего",
            "Подтверждено: Связей", "Подтверждено: Очки QSO", "Подтверждено: Доп. очки", "Подтверждено: Всего",
            "% подтверждения связей"
        ]
        ws.append(headers)

        for col_num, cell in enumerate(ws[1], 1):
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center_aligned_text

        for index, log in enumerate(logs, start=1):
            claimed = log.claimed_qsos or 0
            confirmed = log.confirmed_qsos or 0
            percent = round((confirmed / claimed * 100), 2) if claimed > 0 else 0

            fios = ", ".join([op.fio for op in log.operators if op.fio]) or '-'
            dobs = ", ".join([op.dob for op in log.operators if op.dob]) or '-'
            ranks = ", ".join([op.rank for op in log.operators if op.rank]) or '-'

            row = [
                index,
                _excel_safe(log.callsign),
                _excel_safe(fios),
                _excel_safe(dobs),
                _excel_safe(ranks),
                _excel_safe(log.location or '-'),
                claimed,
                log.claimed_qso_points or 0,
                log.claimed_mult or 0,
                log.claimed_score or 0,
                confirmed,
                log.confirmed_qso_points or 0,
                log.confirmed_mult or 0,
                log.score or 0,
                f"{percent}%"
            ]
            ws.append(row)
            
        for col in ws.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = (max_length + 2)
            ws.column_dimensions[column].width = adjusted_width

    if not wb.sheetnames:
        ws = wb.create_sheet("Пусто")
        ws.append(["Нет отчетов или результатов"])

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name=f"results_{comp.id}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# ---------------------------------------------------------------------------
# Управление пользователями
# ---------------------------------------------------------------------------
def _validate_username(username, exclude_user=None):
    if not username:
        return 'Логин не может быть пустым.'
    if not USERNAME_RE.match(username):
        return 'Логин: 3-50 символов, допускаются латинские буквы, цифры, "._-".'
    query = User.query.filter_by(username=username)
    if exclude_user is not None:
        query = query.filter(User.id != exclude_user.id)
    if query.first():
        return f'Пользователь "{username}" уже существует.'
    return None


def _validate_new_password(password, confirm):
    min_len = current_app.config.get('MIN_PASSWORD_LENGTH', 8)
    if password != confirm:
        return 'Пароли не совпадают.'
    if len(password) < min_len:
        return f'Пароль слишком короткий: минимум {min_len} символов.'
    return None


def _sync_permissions(user, wanted_codes):
    """Приводит набор прав пользователя к переданному списку (с фильтром по справочнику)."""
    allowed = {p.code for p in Permission.query.all()}
    wanted = set(wanted_codes) & allowed
    current = {up.permission_code for up in user.user_permissions}

    for code in wanted - current:
        db.session.add(UserPermission(user_id=user.id, permission_code=code))
    for up in list(user.user_permissions):
        if up.permission_code not in wanted:
            db.session.delete(up)


def _guard_superuser_removal(target):
    """Проверки перед лишением полных прав / деактивацией / удалением суперпользователя."""
    actor = get_current_user()
    if target.id == actor.id:
        return 'Нельзя изменить статус самого себя.'
    if target.username in PROTECTED_USERNAMES:
        return f'Нельзя лишить полных прав защищенного администратора "{target.username}".'
    if target.is_superuser and User.query.filter_by(is_superuser=True).count() <= 1:
        return 'Нельзя лишить полных прав последнего суперпользователя.'
    return None


def _guard_user_delete(target):
    actor = get_current_user()
    if target.id == actor.id:
        return 'Нельзя удалить самого себя.'
    if target.username in PROTECTED_USERNAMES:
        return f'Нельзя удалить защищенного администратора "{target.username}".'
    if target.is_superuser and User.query.filter_by(is_superuser=True).count() <= 1:
        return 'Нельзя удалить последнего суперпользователя.'
    return None


def _form_data_for(user, form=None):
    """Значения полей для формы: при ошибке берем из form, иначе из объекта."""
    if form is not None:
        return {
            'username': form.get('username', ''),
            'is_active': form.get('is_active') == 'on',
            'is_superuser': form.get('is_superuser') == 'on',
            'permissions': set(form.getlist('permissions[]')),
        }
    if user is None:
        return {'username': '', 'is_active': True, 'is_superuser': False, 'permissions': set()}
    return {
        'username': user.username,
        'is_active': user.is_active,
        'is_superuser': user.is_superuser,
        'permissions': user.permission_codes(),
    }


def _render_user_form(user, form_data, error_code=200):
    permissions = Permission.query.order_by(Permission.code).all()
    can_manage_permissions = get_current_user().has_permission('users.permissions')
    return render_template(
        'admin_user_edit.html',
        user=user,
        permissions=permissions,
        form_data=form_data,
        can_manage_permissions=can_manage_permissions,
    ), error_code


@admin_bp.route('/users')
@permission_required('users.view')
def users():
    user_list = User.query.order_by(
        User.is_superuser.desc(), User.username.asc()
    ).all()
    return render_template('admin_users.html', users=user_list)


@admin_bp.route('/users/create', methods=['GET', 'POST'])
@permission_required('users.create')
@limiter.limit("20 per hour", methods=["POST"])
def user_create():
    if request.method == 'POST':
        form = request.form
        username = (form.get('username') or '').strip()
        password = form.get('password') or ''
        password2 = form.get('password_confirm') or ''

        error = _validate_username(username) or _validate_new_password(password, password2)
        if error:
            flash(error, 'danger')
            return _render_user_form(None, _form_data_for(None, form), 400)

        user = User(username=username, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.flush()

        actor = get_current_user()
        if actor.has_permission('users.permissions'):
            if form.get('is_superuser') == 'on':
                user.is_superuser = True
            _sync_permissions(user, form.getlist('permissions[]'))

        db.session.commit()
        current_app.logger.warning(
            "Пользователь '%s' создан администратором %s (superuser=%s)",
            username, actor.username, user.is_superuser,
        )
        flash(f'Пользователь "{username}" создан.', 'success')
        return redirect(url_for('admin.users'))

    return _render_user_form(None, _form_data_for(None))


@admin_bp.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@permission_required('users.edit')
def user_edit(user_id):
    target = User.query.get_or_404(user_id)
    actor = get_current_user()

    if request.method == 'POST':
        form = request.form
        new_username = (form.get('username') or '').strip()
        password = form.get('password') or ''
        password2 = form.get('password_confirm') or ''

        error = _validate_username(new_username, exclude_user=target)
        if not error and password:
            error = _validate_new_password(password, password2)
        if error:
            flash(error, 'danger')
            return _render_user_form(target, _form_data_for(target, form), 400)

        changes = []
        if new_username != target.username:
            changes.append(f'логин → "{new_username}"')
        target.username = new_username

        if password:
            target.password_hash = generate_password_hash(password)
            _invalidate_sessions(target)
            changes.append('пароль')

        # Активность можно менять, только если это не лишает систему
        # единственного действующего суперпользователя.
        new_active = form.get('is_active') == 'on'
        if new_active != target.is_active:
            if not new_active:
                guard = _guard_superuser_removal(target)
                if guard:
                    flash(guard, 'danger')
                else:
                    target.is_active = False
                    _invalidate_sessions(target)
                    changes.append('деактивация')
            else:
                target.is_active = True
                changes.append('активация')

        # Полные права и набор разрешений может менять только тот,
        # у кого есть право users.permissions.
        if actor.has_permission('users.permissions'):
            new_super = form.get('is_superuser') == 'on'
            if new_super != target.is_superuser:
                if not new_super:
                    guard = _guard_superuser_removal(target)
                    if guard:
                        flash(guard, 'danger')
                    else:
                        target.is_superuser = False
                        changes.append('лишение полных прав')
                else:
                    target.is_superuser = True
                    changes.append('выдача полных прав')
            _sync_permissions(target, form.getlist('permissions[]'))

        if changes:
            db.session.commit()
            current_app.logger.warning(
                "Изменения пользователя '%s' администратором %s: %s",
                target.username, actor.username, ', '.join(changes),
            )
            flash(f'Пользователь "{new_username}" обновлен: ' + ', '.join(changes) + '.', 'success')
        else:
            db.session.commit()
            flash('Изменений не внесено.', 'info')
        return redirect(url_for('admin.users'))

    return _render_user_form(target, _form_data_for(target))


@admin_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@permission_required('users.delete')
def user_delete(user_id):
    target = User.query.get_or_404(user_id)

    guard = _guard_user_delete(target)
    if guard:
        flash(guard, 'danger')
        return redirect(url_for('admin.users'))

    username = target.username
    actor = get_current_user()
    db.session.delete(target)
    db.session.commit()
    current_app.logger.warning(
        "Пользователь '%s' удален администратором %s",
        username, actor.username,
    )
    flash(f'Пользователь "{username}" удален.', 'success')
    return redirect(url_for('admin.users'))