from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    # ИЗМЕНЕНО: 255 символов для совместимости с современными алгоритмами (scrypt, pbkdf2)
    password_hash = db.Column(db.String(255), nullable=False)

class Competition(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
    deadline_time = db.Column(db.DateTime, nullable=False)
    time_delta_allowed = db.Column(db.Integer, default=3)
    tours = db.Column(db.Text, default='[]')
    bands = db.Column(db.Text, default='[]')
    modes = db.Column(db.Text, default='[]')
    scoring_script_filename = db.Column(db.String(100), default='primorye_hf')
    is_judged = db.Column(db.Boolean, default=False)
    categories = db.Column(db.Text, nullable=False, default='[]')

class ReceivedLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # ИЗМЕНЕНО: Добавлен index=True для ускорения загрузки главной страницы
    competition_id = db.Column(db.Integer, db.ForeignKey('competition.id'), index=True)
    callsign = db.Column(db.String(20), nullable=False)
    category = db.Column(db.String(100), nullable=False)
    file_path = db.Column(db.String(255), nullable=False)
    # ИЗМЕНЕНО: db.func.now() вместо datetime.utcnow (не вызывает предупреждений в новых версиях Python)
    upload_time = db.Column(db.DateTime, default=db.func.now())
    
    # ИЗМЕНЕНО: String(150), чтобы соответствовать логике в user.py и вмещать длинные адреса
    location = db.Column(db.String(150), default='-')
    
    # СВЯЗЬ: Один лог -> Много операторов
    # cascade="all, delete-orphan" автоматически удалит старых операторов при обновлении лога
    operators = db.relationship('Operator', backref='log', lazy=True, cascade="all, delete-orphan")
    
    # Заявленные результаты
    claimed_qsos = db.Column(db.Integer, default=0)
    claimed_qso_points = db.Column(db.Integer, default=0)
    claimed_mult = db.Column(db.Integer, default=0)
    claimed_score = db.Column(db.Integer, default=0)
    
    # Подтвержденные результаты судейства
    confirmed_qsos = db.Column(db.Integer, default=0)
    confirmed_qso_points = db.Column(db.Integer, default=0)
    confirmed_mult = db.Column(db.Integer, default=0)
    score = db.Column(db.Integer, default=0)

# Временное хранилище загруженного отчета между /upload и /confirm.
# Позволяет не отправлять текст отчета клиенту обратно и не доверять
# данным из формы подтверждения (защита от подмены чужих отчетов).
class PendingUpload(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    competition_id = db.Column(db.Integer, db.ForeignKey('competition.id'), nullable=False, index=True)
    raw_text = db.Column(db.Text, nullable=False)
    headers = db.Column(db.Text, default='{}')
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False)
    used = db.Column(db.Boolean, default=False, nullable=False)

# НОВАЯ ТАБЛИЦА: Операторы
class Operator(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    log_id = db.Column(db.Integer, db.ForeignKey('received_log.id'), index=True)
    fio = db.Column(db.String(100), nullable=False)
    dob = db.Column(db.String(20), default='-')
    # ИЗМЕНЕНО: Увеличено до 50, чтобы не падать при получении звания из формы (в user.py лимит 50)
    rank = db.Column(db.String(50), default='-')
    callsign = db.Column(db.String(20), default='') # Добавлено, так как форма его собирает

class QSO(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    competition_id = db.Column(db.Integer, db.ForeignKey('competition.id'), index=True)
    log_id = db.Column(db.Integer, db.ForeignKey('received_log.id'), index=True)
    my_call = db.Column(db.String(20), nullable=False, index=True)
    corr_call = db.Column(db.String(20), nullable=False, index=True)
    qso_datetime = db.Column(db.DateTime, nullable=False)
    band = db.Column(db.String(10), nullable=False)
    mode = db.Column(db.String(10), nullable=False)
    rst_sent = db.Column(db.String(10))
    nr_sent = db.Column(db.String(20))
    rst_rcvd = db.Column(db.String(10))
    nr_rcvd = db.Column(db.String(20))
    
    tour_num = db.Column(db.Integer, default=0)
    is_valid = db.Column(db.Boolean, default=False)
    # ИЗМЕНЕНО: До 255 на всякий случай для длинных описаний ошибок парсера/судейства
    error_reason = db.Column(db.String(255), default='')
    points = db.Column(db.Integer, default=0)