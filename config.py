import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

class Config:
    SECRET_KEY = 'super-secret-key-change-it'
    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(BASE_DIR, 'data', 'judging.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Файлы хранятся ВНЕ папки static, чтобы исключить прямое скачивание
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'data', 'logs')
    SCRIPTS_FOLDER = os.path.join(BASE_DIR, 'data', 'scripts')
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # Ограничение 5 МБ на файл