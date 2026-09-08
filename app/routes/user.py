from flask import Blueprint, request, render_template, current_app
from app.models import db, Competition, ReceivedLog
from app.ermak_parser import parse_ermak
from app.utils import get_categories_list
from datetime import datetime
import os

user_bp = Blueprint('user', __name__)

@user_bp.route('/')
def index():
    competitions = Competition.query.all()
    logs_by_comp = {}
    for comp in competitions:
        logs = ReceivedLog.query.filter_by(competition_id=comp.id).all()
        if logs:
            logs_by_comp[comp.name] = [log.callsign for log in logs]
            
    return render_template('index.html', competitions=competitions, logs_by_comp=logs_by_comp)

@user_bp.route('/upload', methods=['POST'])
def upload_log():
    comp_id = request.form.get('competition_id')
    comp = Competition.query.get_or_404(comp_id)
    
    file = request.files.get('logfile')
    if not file or not file.filename.lower().endswith(('.cbr', '.log', '.txt')):
        return "Недопустимый формат файла", 400
        
    content = file.read()
    headers, qsos, missing_headers, raw_text, operators = parse_ermak(content, comp.start_time, comp.end_time)
    
    now = datetime.now()
    status_code = 'OK'
    status_msg = ''
    
    if now > comp.deadline_time:
        status_code = 'CHECKLOG_ONLY'
        status_msg = 'Отчет загружен после окончания срока приема. Он будет принят только для контроля (Checklog).'
        
    # Парсим список категорий
    categories_list = get_categories_list(comp.categories)
    categories = {c: c for c in categories_list}
    
    # Если группы CHECKLOG нет, добавим ее явным образом
    has_checklog = any('CHECKLOG' in c.upper() for c in categories_list)
    if not has_checklog:
        categories['CHECKLOG'] = 'CHECKLOG - Отчет для контроля'
        
    return render_template('preview.html', 
                           contest=comp,
                           status_code=status_code,
                           status_msg=status_msg,
                           raw_text=raw_text,
                           missing_headers=missing_headers,
                           categories=categories,
                           headers=headers,
                           operators=operators,
                           qsos=qsos)

@user_bp.route('/confirm', methods=['POST'])
def confirm_upload():
    comp_id = request.form.get('contest_id')
    raw_text = request.form.get('raw_text')
    claimed_category = request.form.get('claimed_category', 'CHECKLOG')
    
    callsign = request.form.get('header_CALLSIGN', '').strip().upper()
    if not callsign:
        for line in raw_text.splitlines():
            line_str = line.strip()
            if line_str.upper().startswith('CALLSIGN:'):
                callsign = line_str.split(':', 1)[1].strip().upper()
                break
                
    if not callsign:
        return "Ошибка: Не удалось определить позывной участника", 400

    comp = Competition.query.get_or_404(comp_id)
    upload_folder = current_app.config['UPLOAD_FOLDER']
    safe_category = claimed_category.replace(' ', '_').replace('/', '_').replace(':', '_')
    dir_path = os.path.join(upload_folder, str(comp_id), safe_category)
    os.makedirs(dir_path, exist_ok=True)
    
    file_path = os.path.join(dir_path, f"{callsign}.cbr")
    
    with open(file_path, 'w', encoding='cp1251', errors='ignore') as f:
        f.write(raw_text)
        
    existing_log = ReceivedLog.query.filter_by(competition_id=comp.id, callsign=callsign).first()
    if existing_log:
        existing_log.category = claimed_category
        existing_log.file_path = file_path
        existing_log.upload_time = datetime.now()
    else:
        new_log = ReceivedLog(
            competition_id=comp.id,
            callsign=callsign,
            category=claimed_category,
            file_path=file_path
        )
        db.session.add(new_log)
        
    db.session.commit()
    return render_template('success.html', callsign=callsign, category=claimed_category, comp=comp)