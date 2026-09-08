from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)

class Competition(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
    deadline_time = db.Column(db.DateTime, nullable=False)
    time_delta_allowed = db.Column(db.Integer, default=3)
    
    # Поля с корректными отступами (ровно 4 пробела):
    tours = db.Column(db.Text, default='[]') # JSON список объектов [{'start': '...', 'end': '...'}]
    bands = db.Column(db.Text, default='[]') # JSON список ['160m', '80m']
    modes = db.Column(db.Text, default='[]') # JSON список ['CW', 'SSB']
    
    scoring_script_filename = db.Column(db.String(100))
    is_judged = db.Column(db.Boolean, default=False)
    
    # Категории (хранятся как JSON-список строк)
    categories = db.Column(db.Text, nullable=False, default='[]')

class ReceivedLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    competition_id = db.Column(db.Integer, db.ForeignKey('competition.id'))
    callsign = db.Column(db.String(20), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    file_path = db.Column(db.String(255), nullable=False)
    upload_time = db.Column(db.DateTime, default=datetime.utcnow)