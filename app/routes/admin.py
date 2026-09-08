from flask import Blueprint, render_template, request, redirect, url_for, session, current_app, send_file
from app.models import db, Admin, Competition, ReceivedLog
from app.crosscheck import run_crosscheck
from app.utils import get_categories_list
from datetime import datetime
import json
import os
import io
import zipfile
from werkzeug.security import generate_password_hash, check_password_hash

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        admin = Admin.query.filter_by(username=username).first()
        if not admin and username == 'admin' and password == 'admin123':
            hashed = generate_password_hash('admin123')
            admin = Admin(username='admin', password_hash=hashed)
            db.session.add(admin)
            db.session.commit()

        if admin and check_password_hash(admin.password_hash, password):
            session['admin_logged'] = True
            return redirect(url_for('admin.index'))
        return "Неверный логин или пароль", 401
    return render_template('admin_login.html')

@admin_bp.route('/logout')
def logout():
    session.pop('admin_logged', None)
    return redirect(url_for('admin.login'))

@admin_bp.route('/')
def index():
    if not session.get('admin_logged'): return redirect(url_for('admin.login'))
    competitions = Competition.query.all()
    
    # Формируем словарь со списком категорий для отображения
    comp_categories = {comp.id: get_categories_list(comp.categories) for comp in competitions}
    
    return render_template('admin.html', competitions=competitions, comp_categories=comp_categories)

# СОЗДАНИЕ СОРЕВНОВАНИЯ
@admin_bp.route('/add', methods=['GET', 'POST'])
def add_competition():
    if not session.get('admin_logged'): return redirect(url_for('admin.login'))
    
    if request.method == 'POST':
        categories_list = [c.strip() for c in request.form.getlist('categories[]') if c.strip()]
        bands_list = request.form.getlist('bands[]')
        modes_list = request.form.getlist('modes[]')
        
        # Сборка сеансов (блоков) и правил их разбивки на туры
        block_starts = request.form.getlist('block_start[]')
        block_ends = request.form.getlist('block_end[]')
        divide_bys = request.form.getlist('divide_by[]')
        divide_values = request.form.getlist('divide_value[]')
        
        tours_list = []
        for s, e, db_type, db_val in zip(block_starts, block_ends, divide_bys, divide_values):
            if s and e:
                tours_list.append({
                    'start': s,
                    'end': e,
                    'divide_by': db_type,  # 'none', 'duration' или 'count'
                    'divide_value': int(db_val) if db_val and db_val.isdigit() else 0
                })
        
        new_comp = Competition(
            name=request.form.get('name'),
            start_time=datetime.strptime(request.form.get('start_time'), '%Y-%m-%dT%H:%M'),
            end_time=datetime.strptime(request.form.get('end_time'), '%Y-%m-%dT%H:%M'),
            deadline_time=datetime.strptime(request.form.get('deadline_time'), '%Y-%m-%dT%H:%M'),
            time_delta_allowed=request.form.get('time_delta', type=int, default=3),
            categories=json.dumps(categories_list, ensure_ascii=False),
            tours=json.dumps(tours_list, ensure_ascii=False),
            bands=json.dumps(bands_list, ensure_ascii=False),
            modes=json.dumps(modes_list, ensure_ascii=False)
        )
        db.session.add(new_comp)
        db.session.commit()
        return redirect(url_for('admin.index'))
        
    return render_template('admin_edit.html', comp=None, categories_list=[])


# РЕДАКТИРОВАНИЕ СОРЕВНОВАНИЯ
@admin_bp.route('/edit/<int:comp_id>', methods=['GET', 'POST'])
def edit_competition(comp_id):
    if not session.get('admin_logged'): return redirect(url_for('admin.login'))
    comp = Competition.query.get_or_404(comp_id)
    
    if request.method == 'POST':
        categories_list = [c.strip() for c in request.form.getlist('categories[]') if c.strip()]
        bands_list = request.form.getlist('bands[]')
        modes_list = request.form.getlist('modes[]')
        
        block_starts = request.form.getlist('block_start[]')
        block_ends = request.form.getlist('block_end[]')
        divide_bys = request.form.getlist('divide_by[]')
        divide_values = request.form.getlist('divide_value[]')
        
        tours_list = []
        for s, e, db_type, db_val in zip(block_starts, block_ends, divide_bys, divide_values):
            if s and e:
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
        
        comp.categories = json.dumps(categories_list, ensure_ascii=False)
        comp.tours = json.dumps(tours_list, ensure_ascii=False)
        comp.bands = json.dumps(bands_list, ensure_ascii=False)
        comp.modes = json.dumps(modes_list, ensure_ascii=False)
        
        db.session.commit()
        return redirect(url_for('admin.index'))
        
    # Парсим JSON-строку категорий в список Python для передачи в шаблон
    try:
        categories_list = json.loads(comp.categories) if comp.categories else []
    except:
        categories_list = []
        
    return render_template('admin_edit.html', comp=comp, categories_list=categories_list)

@admin_bp.route('/delete/<int:comp_id>', methods=['POST'])
def delete_competition(comp_id):
    if not session.get('admin_logged'): return redirect(url_for('admin.login'))
    comp = Competition.query.get_or_404(comp_id)
    ReceivedLog.query.filter_by(competition_id=comp.id).delete()
    db.session.delete(comp)
    db.session.commit()
    return redirect(url_for('admin.index'))

@admin_bp.route('/judge/<int:comp_id>')
def judge_competition(comp_id):
    if not session.get('admin_logged'): return redirect(url_for('admin.login'))
    comp = Competition.query.get_or_404(comp_id)
    results = run_crosscheck(comp, current_app.config['UPLOAD_FOLDER'])
    if isinstance(results, dict) and 'error' in results:
        return results['error'], 400

    comp.is_judged = True
    db.session.commit()
    return render_template('admin_results.html', comp=comp, results=results)

@admin_bp.route('/download_ubn/<int:comp_id>/<callsign>')
def download_ubn(comp_id, callsign):
    if not session.get('admin_logged'): return redirect(url_for('admin.login'))
    comp = Competition.query.get_or_404(comp_id)
    results = run_crosscheck(comp, current_app.config['UPLOAD_FOLDER'])
    if callsign in results:
        file_stream = io.BytesIO(results[callsign]['ubn_text'].encode('cp1251', errors='ignore'))
        return send_file(file_stream, as_attachment=True, download_name=f"{callsign}.ubn", mimetype='text/plain')
    return "UBN файл не найден", 404

@admin_bp.route('/download_all_ubn/<int:comp_id>')
def download_all_ubn(comp_id):
    if not session.get('admin_logged'): return redirect(url_for('admin.login'))
    comp = Competition.query.get_or_404(comp_id)
    results = run_crosscheck(comp, current_app.config['UPLOAD_FOLDER'])
    
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        for callsign, data in results.items():
            zf.writestr(f"{callsign}.ubn", data['ubn_text'].encode('cp1251', errors='ignore'))
            
    memory_file.seek(0)
    return send_file(memory_file, as_attachment=True, download_name=f"UBN_{comp.name.replace(' ', '_')}.zip", mimetype='application/zip')