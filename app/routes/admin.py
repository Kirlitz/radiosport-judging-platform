import json
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from app.models import db, Admin, Competition, ReceivedLog
from app.judging import run_judging_primorye

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

def get_categories_list(categories_json):
    try:
        return json.loads(categories_json) if categories_json else []
    except Exception:
        return []

@admin_bp.route('/')
def index():
    if not session.get('admin_logged'):
        return redirect(url_for('admin.login'))
    competitions = Competition.query.order_by(Competition.start_time.desc()).all()
    return render_template('admin_index.html', competitions=competitions)

@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        admin = Admin.query.filter_by(username=username).first()
        if admin and admin.password_hash == password:
            session['admin_logged'] = True
            return redirect(url_for('admin.index'))
        flash('Неверный логин или пароль')
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
        
    return render_template('admin_edit.html', comp=None, categories_list=[])

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
        comp.scoring_script_filename = request.form.get('scoring_script_filename', 'primorye_hf')
        
        comp.categories = json.dumps(categories_list, ensure_ascii=False)
        comp.tours = json.dumps(tours_list, ensure_ascii=False)
        comp.bands = json.dumps(bands_list, ensure_ascii=False)
        comp.modes = json.dumps(modes_list, ensure_ascii=False)
        
        db.session.commit()
        return redirect(url_for('admin.index'))
        
    categories_list = get_categories_list(comp.categories)
    return render_template('admin_edit.html', comp=comp, categories_list=categories_list)

@admin_bp.route('/delete/<int:comp_id>')
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
    if comp.scoring_script_filename == 'primorye_hf':
        run_judging_primorye(comp.id)
    return redirect(url_for('admin.index'))