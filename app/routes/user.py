import re
import os
import json
import secrets
from datetime import datetime, timedelta
from flask import Blueprint, request, render_template, current_app
from werkzeug.utils import secure_filename
from app import limiter # Импортируем лимитер
from app.models import db, Competition, ReceivedLog, Operator, PendingUpload
from app.ermak_parser import parse_ermak, update_cabrillo_header
from app.utils import get_categories_list

user_bp = Blueprint('user', __name__)

# Время жизни сессии загрузки между /upload и /confirm
PENDING_TTL = timedelta(minutes=30)

# Максимальная длина названия зачетной группы (должна совпадать с admin_edit.html)
CATEGORY_MAX_LENGTH = 110

def sanitize_text(text, max_length=100):
    """
    Очищает строку от потенциально опасных символов, используемых в XSS, SSTI и RCE.
    Оставляет только буквы, цифры, пробелы, дефисы и базовые знаки препинания.
    """
    if not text:
        return ""
    # Вырезаем скобки (всех видов), кавычки, пайпы, точку с запятой, обратные слэши
    clean_text = re.sub(r'[<>{}\[\]|;\'"\\]', '', text)
    return clean_text.strip()[:max_length]

@user_bp.route('/')
def index():
    competitions = Competition.query.all()
    logs_by_comp = {}
    for comp in competitions:
        logs = ReceivedLog.query.filter_by(competition_id=comp.id).all()
        if logs:
            logs_by_comp[comp.name] = list(dict.fromkeys(log.callsign for log in logs))
            
    return render_template('index.html', competitions=competitions, logs_by_comp=logs_by_comp)

@user_bp.route('/upload', methods=['POST'])
@limiter.limit("10 per minute") # Ограничение: не более 10 загрузок в минуту с одного IP
def upload_log():
    comp_id = request.form.get('competition_id')
    # get_or_404 сам защищает от SQLi, так как преобразует ввод в ID
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

    # Сохраняем отчет НА СЕРВЕРЕ и выдаем одноразовый токен.
    # Текст отчета клиенту обратно не отправляется — /confirm будет
    # читать его только из этой записи (защита от подмены чужих отчетов).
    token = secrets.token_urlsafe(32)
    pending = PendingUpload(
        token=token,
        competition_id=comp.id,
        raw_text=raw_text,
        headers=json.dumps(headers, ensure_ascii=False)
    )
    db.session.add(pending)

    # Подчистка истекших сессий (раз в новую загрузку, дёшево для SQLite)
    PendingUpload.query.filter(
        PendingUpload.created_at < datetime.now() - timedelta(hours=2)
    ).delete(synchronize_session=False)

    db.session.commit()

    return render_template('preview.html',
                           contest=comp,
                           status_code=status_code,
                           status_msg=status_msg,
                           upload_token=token,
                           missing_headers=missing_headers,
                           categories=categories,
                           headers=headers,
                           operators=operators,
                           qsos=qsos)

@user_bp.route('/confirm', methods=['POST'])
@limiter.limit("15 per minute") # Ограничение на подтверждение формы
def confirm_upload():
    comp_id = request.form.get('contest_id')
    comp = Competition.query.get_or_404(comp_id)

    # Текстом отчета доверяем ТОЛЬКО серверной копии из сессии /upload.
    # Клиент больше не отправляет raw_text и не может подложить чужой позывной
    # с произвольным содержимым.
    upload_token = request.form.get('upload_token', '')
    pending = PendingUpload.query.filter_by(token=upload_token).first()
    if pending is None or pending.used or pending.competition_id != comp.id:
        return "Ошибка: Сессия загрузки не найдена или уже использована. Загрузите файл отчета заново.", 400
    if datetime.now() - pending.created_at > PENDING_TTL:
        return "Ошибка: Сессия загрузки истекла. Загрузите файл отчета заново.", 400

    raw_text = pending.raw_text
    try:
        headers = json.loads(pending.headers or '{}')
    except (json.JSONDecodeError, TypeError):
        headers = {}

    # Перепарсиваем отчет: проверяем согласованность позывного и считаем QSO
    _, qsos, _, _, _ = parse_ermak(raw_text, comp.start_time, comp.end_time)

    # Собираем всех операторов из формы (участник дополняет данные о своей станции)
    operators = []

    # Данные первого (основного) оператора (пропускаем через санитайзер)
    op1_name = sanitize_text(request.form.get('operator_name', ''))
    op1_dob = sanitize_text(request.form.get('operator_birth_year', ''), 20)
    op1_rank = sanitize_text(request.form.get('operator_rank', ''), 50)
    op1_call = sanitize_text(request.form.get('op_1_call', ''), 20).upper()

    if op1_name:
        operators.append({
            'fio': op1_name,
            'dob': op1_dob,
            'rank': op1_rank,
            'callsign': op1_call
        })

    # Данные дополнительных операторов (от 2 до 4)
    for i in range(2, 5):
        op_fio = sanitize_text(request.form.get(f'op_{i}_fio', ''))
        if op_fio:
            operators.append({
                'fio': op_fio,
                'dob': sanitize_text(request.form.get(f'op_{i}_dob', ''), 20),
                'rank': sanitize_text(request.form.get(f'op_{i}_rank', ''), 50),
                'callsign': sanitize_text(request.form.get(f'op_{i}_call', ''), 20).upper()
            })

    # Регион
    location = sanitize_text(request.form.get('operator_location', ''), 150)

    # Позывной берем из заголовка отчета (серверная копия).
    # Поле формы учитывается только если заголовка нет — именно для этого
    # случая preview.html показывает его как редактируемое.
    callsign = (headers.get('CALLSIGN') or '').strip().upper()
    if not callsign:
        callsign = sanitize_text(request.form.get('header_CALLSIGN', ''), 20).upper()

    if not callsign:
        return "Ошибка: Не удалось определить позывной участника", 400

    # СТРОГАЯ ВАЛИДАЦИЯ ПОЗЫВНОГО: Только латиница, цифры и слеш. Длина от 3 до 20 символов.
    if not re.match(r'^[A-Z0-9/]{3,20}$', callsign):
        current_app.logger.warning(f"Попытка инъекции или неверный позывной: {callsign}")
        return "Ошибка: Недопустимый формат позывного. Допустимы только латинские буквы, цифры и символ дроби.", 400

    # Проверка согласованности: ВСЕ связи отчета должны быть на заявленном
    # позывном. Иначе это чужой лог, присланный под чужим позывным.
    if not qsos:
        return "Ошибка: В отчете не найдено ни одной связи (QSO)", 400
    foreign_calls = {q['my_call'] for q in qsos if q.get('my_call')} - {callsign}
    if foreign_calls:
        current_app.logger.warning(
            f"Отчет под позывным {callsign} содержит чужие связи: {sorted(foreign_calls)}"
        )
        return "Ошибка: В отчете найдены связи, записанные на другой позывной", 400

    # Категория проверяется строго по списку соревнования.
    # После дедлайна зачетная группа вынужденно CHECKLOG.
    now = datetime.now()
    if now > comp.deadline_time:
        claimed_category = 'CHECKLOG'
    else:
        claimed_category = sanitize_text(request.form.get('claimed_category', ''), CATEGORY_MAX_LENGTH)
        if claimed_category not in get_categories_list(comp.categories):
            return "Ошибка: Недопустимая зачетная группа для этого соревнования", 400

    upload_folder = current_app.config['UPLOAD_FOLDER']

    # Дополнительная очистка категории для файловой системы
    safe_category = secure_filename(claimed_category.replace(' ', '_').replace('/', '_'))
    if not safe_category: safe_category = "UNKNOWN_CAT"

    dir_path = os.path.join(upload_folder, str(comp.id), safe_category)
    os.makedirs(dir_path, exist_ok=True)

    # Очистка имени файла для безопасности (защита от Path Traversal)
    safe_callsign = secure_filename(callsign.replace('/', '-').replace('\\', '-'))
    if not safe_callsign:
        safe_callsign = "UNKNOWN_CALL"

    # Формируем список объектов Operator для базы данных
    op_objects = []
    for op in operators:
        if op.get('fio'):
            op_objects.append(Operator(
                fio=op['fio'],
                dob=op.get('dob') or '-',
                rank=op.get('rank') or '-',
                callsign=op.get('callsign') or ''
            ))

    # 1. Новая запись для КАЖДОЙ подачи: старые отчеты не затираются,
    #    а сохраняются. Лишние (дубли, чужие) администратор удаляет в админке.
    new_log = ReceivedLog(
        competition_id=comp.id,
        callsign=callsign,
        category=claimed_category,
        file_path='',
        location=location if location else '-',
        claimed_qsos=len(qsos),
        operators=op_objects
    )
    db.session.add(new_log)
    db.session.flush()  # чтобы получить id для уникального имени файла

    # 2. Уникальное имя файла: <позывной>_<id_отчета>.cbr — коллизий не бывает
    file_path = os.path.join(dir_path, f"{safe_callsign}_{new_log.id}.cbr")

    # 3. Записываем исходный текст
    with open(file_path, 'w', encoding='utf-8', errors='ignore') as f:
        f.write(raw_text)

    # 4. Обновляем шапку сохраненной копии
    update_cabrillo_header(file_path, {
        'callsign': callsign,
        'location': location,
        'operators': operators
    })
    new_log.file_path = file_path

    # 5. Сессия одноразовая: повторно подтвердить ее нельзя
    pending.used = True
    PendingUpload.query.filter(
        (PendingUpload.used.is_(True)) &
        (PendingUpload.created_at < datetime.now() - timedelta(hours=1))
    ).delete(synchronize_session=False)

    db.session.commit()
    return render_template('success.html', callsign=callsign, category=claimed_category, comp=comp)