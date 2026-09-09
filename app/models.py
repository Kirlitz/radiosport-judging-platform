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
    tours = db.Column(db.Text, default='[]')
    bands = db.Column(db.Text, default='[]')
    modes = db.Column(db.Text, default='[]')
    scoring_script_filename = db.Column(db.String(100), default='primorye_hf')
    is_judged = db.Column(db.Boolean, default=False)
    categories = db.Column(db.Text, nullable=False, default='[]')

class ReceivedLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    competition_id = db.Column(db.Integer, db.ForeignKey('competition.id'))
    callsign = db.Column(db.String(20), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    file_path = db.Column(db.String(255), nullable=False)
    upload_time = db.Column(db.DateTime, default=datetime.utcnow)
    
    confirmed_qsos = db.Column(db.Integer, default=0)
    score = db.Column(db.Integer, default=0)

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
    error_reason = db.Column(db.String(100), default='')
    points = db.Column(db.Integer, default=0)