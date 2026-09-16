import json
import os
import io
import zipfile
import importlib
import shutil
from datetime import datetime
from collections import defaultdict
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, current_app, send_file, abort
from app.models import db, Admin, Competition, ReceivedLog, QSO, Operator
from app import limiter
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename
from app.utils import get_official_logs
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

def get_categories_list(categories_json):
    try:
        return json.loads(categories_json) if categories_json else []
    except Exception:
        return []

def get_available_plugins():
    """Сканирует директорию app/judging и возвращает список доступных плагинов."""
    judging_dir = os.path.join(current_app.root_path, 'judging')
    plugins = []
    
    if os.path.exists(judging_dir):
        for filename in os.listdir(judging_dir):
            if filename.endswith('.py') and not filename.startswith('__'):
                module_name = filename[:-3]
                title = module_name
                try:
                    mod = importlib.import_module(f'app.judging.{module_name}')
                    if hasattr(mod, 'PLUGIN_TITLE'):
                        title = mod.PLUGIN_TITLE
                except Exception as e:
                    current_app.logger.error(f"Ошибка загрузки плагина {module_name}: {e}")
                    
                plugins.append({
                    'id': module_name,
                    'title': title
                })
    return plugins

def get_grouped_results(comp):
    """Группирует отчеты участников по категориям для вывода и экспорта.
    Учитывает только официальные (последние) отчеты — дубли не считаются."""
    categories = get_categories_list(comp.categories)
    grouped = defaultdict(list)

    logs = get_official_logs(comp.id)
    logs.sort(key=lambda lg: (lg.score or 0, lg.confirmed_qsos or 0), reverse=True)

    for cat in categories:
        grouped[cat] = []
        
    for log in logs:
        cat = log.category if log.category in grouped else (log.category or 'Без категории')
        grouped[cat].append(log)
        
    return dict(grouped)

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
            status = error_map.get(q.error_reason, q.error_reason or "Ошибка")
            
        lines.append(f"{dt_str:<17} {q.band:<6} {q.mode:<4} {sent_str:<10} {q.corr_call:<10} {rcvd_str:<10} {pts:<5} {mult_pts:<5} {status}")
        
    lines.append("-" * 85)
    return "\n".join(lines)

@admin_bp.route('/')
def index():
    if not session.get('admin_logged'):
        return redirect(url_for('admin.login'))
    competitions = Competition.query.order_by(Competition.start_time.desc()).all()
    return render_template('admin_index.html', competitions=competitions)

@admin_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per 15 minutes", methods=["POST"])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        admin = Admin.query.filter_by(username=username).first()
        if admin and check_password_hash(admin.password_hash, password):
            session['admin_logged'] = True
            flash('Успешный вход!', 'success')
            return redirect(url_for('admin.index'))
        else:
            flash('Неверный логин или пароль', 'danger')
    return render_template('admin_login.html')

@admin_bp.route('/logout')
def logout():
    session.pop('admin_logged', None)
    return redirect(url_for('admin.login'))

@admin_bp.route('/add', methods=['GET', 'POST'])
def add_competition():
    if not session.get('admin_logged'): return redirect(url_for('admin.login'))
    
    if request.method == 'POST':
        categories_list = [c.strip() for c in request.form.getlist('categories[]') if c.strip()]
        bands_list = [b.strip() for b in request.form.getlist('bands[]') if isinstance(b, str) and b.strip()]
        modes_list = [m.strip() for m in request.form.getlist('modes[]') if isinstance(m, str) and m.strip()]

        block_starts = request.form.getlist('block_start[]')
        block_ends = request.form.getlist('block_end[]')
        divide_bys = request.form.getlist('divide_by[]')
        divide_values = request.form.getlist('divide_value[]')

        tours_list = []
        for s, e, db_type, db_val in zip(block_starts, block_ends, divide_bys, divide_values):
            if s and e and db_type in ('none', 'duration', 'count'):
                tours_list.append({
                    'start': s,
                    'end': e,
                    'divide_by': db_type,
                    'divide_value': int(db_val) if db_val and db_val.isdigit() else 0
                })

        new_comp = Competition(
            name=request.form.get('name'),
            start_time=datetime.strptime(request.form.get('start_time'), '%Y-%m-%dT%H:%M'),
            end_time=datetime.strptime(request.form.get('end_time'), '%Y-%m-%dT%H:%M'),
            deadline_time=datetime.strptime(request.form.get('deadline_time'), '%Y-%m-%dT%H:%M'),
            time_delta_allowed=request.form.get('time_delta', type=int, default=3),
            scoring_script_filename=request.form.get('scoring_script_filename', 'primorye_hf'),
            categories=json.dumps(categories_list, ensure_ascii=False),
            tours=json.dumps(tours_list, ensure_ascii=False),
            bands=json.dumps(bands_list, ensure_ascii=False),
            modes=json.dumps(modes_list, ensure_ascii=False)
        )
        db.session.add(new_comp)
        db.session.commit()
        return redirect(url_for('admin.index'))
        
    plugins = get_available_plugins()
    return render_template('admin_edit.html', comp=None, categories_list=[], plugins=plugins,
                           tours_data=[], bands_data=[], modes_data=[])

@admin_bp.route('/edit/<int:comp_id>', methods=['GET', 'POST'])
def edit_competition(comp_id):
    if not session.get('admin_logged'): return redirect(url_for('admin.login'))
    comp = Competition.query.get_or_404(comp_id)
    
    if request.method == 'POST':
        categories_list = [c.strip() for c in request.form.getlist('categories[]') if c.strip()]
        bands_list = [b.strip() for b in request.form.getlist('bands[]') if isinstance(b, str) and b.strip()]
        modes_list = [m.strip() for m in request.form.getlist('modes[]') if isinstance(m, str) and m.strip()]

        block_starts = request.form.getlist('block_start[]')
        block_ends = request.form.getlist('block_end[]')
        divide_bys = request.form.getlist('divide_by[]')
        divide_values = request.form.getlist('divide_value[]')

        tours_list = []
        for s, e, db_type, db_val in zip(block_starts, block_ends, divide_bys, divide_values):
            if s and e and db_type in ('none', 'duration', 'count'):
                tours_list.append({
                    'start': s,
                    'end': e,
                    'divide_by': db_type,
                    'divide_value': int(db_val) if db_val and db_val.isdigit() else 0
                })
        
        comp.name = request.form.get('name')
        comp.start_time = datetime.strptime(request.form.get('start_time'), '%Y-%m-%dT%H:%M')
        comp.end_time = datetime.strptime(request.form.get('end_time'), '%Y-%m-%dT%H:%M')
        comp.deadline_time = datetime.strptime(request.form.get('deadline_time'), '%Y-%m-%dT%H:%M')
        comp.time_delta_allowed = request.form.get('time_delta', type=int, default=3)
        comp.scoring_script_filename = request.form.get('scoring_script_filename', 'primorye_hf')
        
        comp.categories = json.dumps(categories_list, ensure_ascii=False)
        comp.tours = json.dumps(tours_list, ensure_ascii=False)
        comp.bands = json.dumps(bands_list, ensure_ascii=False)
        comp.modes = json.dumps(modes_list, ensure_ascii=False)
        
        db.session.commit()
        return redirect(url_for('admin.index'))
        
    categories_list = get_categories_list(comp.categories)
    plugins = get_available_plugins()
    tours_data = json.loads(comp.tours) if comp.tours else []
    bands_data = json.loads(comp.bands) if comp.bands else []
    modes_data = json.loads(comp.modes) if comp.modes else []
    return render_template('admin_edit.html', comp=comp, categories_list=categories_list, plugins=plugins,
                           tours_data=tours_data, bands_data=bands_data, modes_data=modes_data)

@admin_bp.route('/delete/<int:comp_id>', methods=['POST'])
def delete_competition(comp_id):
    if not session.get('admin_logged'): 
        return redirect(url_for('admin.login'))
    
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
    
    flash('Соревнование, все отчеты, операторы и файлы физически удалены.', 'success')
    return redirect(url_for('admin.index'))

@admin_bp.route('/logs/<int:comp_id>')
def logs_list(comp_id):
    """Список ВСЕХ поданных отчетов соревнования (включая дубли).
    Официальным считается последний по времени — именно он участвует в судействе."""
    if not session.get('admin_logged'):
        return redirect(url_for('admin.login'))
    comp = Competition.query.get_or_404(comp_id)
    logs = ReceivedLog.query.filter_by(competition_id=comp.id).order_by(
        ReceivedLog.upload_time.asc(), ReceivedLog.id.asc()
    ).all()
    official_ids = {lg.id for lg in get_official_logs(comp.id)}
    return render_template('admin_logs.html', comp=comp, logs=logs, official_ids=official_ids)

@admin_bp.route('/delete_log/<int:log_id>', methods=['POST'])
def delete_log(log_id):
    """Удаление одной подачи: файл + связи + операторы + запись.
    Остальные отчеты (в том числе дубли других участников) не затрагиваются."""
    if not session.get('admin_logged'):
        return redirect(url_for('admin.login'))
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

@admin_bp.route('/judge/<int:comp_id>', methods=['POST'])
def judge_competition(comp_id):
    if not session.get('admin_logged'): return redirect(url_for('admin.login'))
    comp = Competition.query.get_or_404(comp_id)
    script_name = comp.scoring_script_filename
    
    try:
        module = importlib.import_module(f'app.judging.{script_name}')
        
        if hasattr(module, 'run_judging'):
            module.run_judging(comp.id)
        elif hasattr(module, f'run_judging_{script_name}'):
            getattr(module, f'run_judging_{script_name}')(comp.id)
        else:
            flash(f'Ошибка: В плагине {script_name} не найдена функция run_judging(comp_id)', 'danger')
            return redirect(url_for('admin.index'))
            
        comp.is_judged = True
        db.session.commit()
        flash(f'Судейство соревнований "{comp.name}" успешно выполнено!', 'success')
    except Exception as e:
        flash(f'Ошибка при выполнении судейства плагином {script_name}: {str(e)}', 'danger')
        
    return redirect(url_for('admin.index'))

@admin_bp.route('/download_ubn/<int:comp_id>')
def download_ubn_archive(comp_id):
    if not session.get('admin_logged'):
        return redirect(url_for('admin.login'))
        
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
def export_excel(comp_id):
    if not session.get('admin_logged'):
        return redirect(url_for('admin.login'))

    comp = Competition.query.get_or_404(comp_id)
    
    if not comp.is_judged:
        abort(400, description="Соревнование еще не отсужено.")

    grouped_results = get_grouped_results(comp)

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    header_font = Font(bold=True)
    header_fill = PatternFill(start_color="D3D3D3", end_color="D3D3D3", fill_type="solid")
    center_aligned_text = Alignment(horizontal="center", vertical="center")

    for category, logs in grouped_results.items():
        if not logs:
            continue
            
        safe_title = str(category)[:31].replace("/", "-").replace("\\", "-")
        ws = wb.create_sheet(title=safe_title)
        
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
                log.callsign,
                fios,
                dobs,
                ranks,
                log.location or '-',
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