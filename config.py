import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

class Config:
    # Production: ключ обязан быть задан в переменной окружения SECRET_KEY.
    # При импорте класса падать нельзя (иначе не запустится даже DevConfig),
    # поэтому здесь берём значение без исключения, а строгую проверку
    # выполняет create_app() (см. app/__init__.py) при использовании Config.
    SECRET_KEY = os.environ.get('SECRET_KEY', '')

    # Cookie сессии: в production отправляется ТОЛЬКО по HTTPS (Secure),
    # не доступна из JS (HttpOnly), SameSite=Lax против CSRF.
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', '1') == '1'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    # Добавлен timeout=20 для избежания ошибки "database is locked" при конкурентных запросах
    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(BASE_DIR, 'data', 'judging.db') + '?timeout=20'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Файлы хранятся ВНЕ папки static, чтобы исключить прямое скачивание
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'data', 'logs')
    SCRIPTS_FOLDER = os.path.join(BASE_DIR, 'data', 'scripts')
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # Ограничение 5 МБ на файл


class DevConfig(Config):
    # Локальная разработка: python run.py (dev-сервер работает по HTTP,
    # поэтому Secure-флаг у сессии отключен). SECRET_KEY получает dev-значение
    # по умолчанию, если переменная окружения не задана.
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-not-for-production')
    SESSION_COOKIE_SECURE = False