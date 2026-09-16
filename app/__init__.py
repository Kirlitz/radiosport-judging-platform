import ipaddress
import os

from flask import Flask, request
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from config import Config, DevConfig

# Импортируем объект базы данных из models.py
from app.models import db

# IP-адреса доверенных обратных прокси (nginx на этом же хосте).
# Расширяется через переменную окружения, если прокси на других хостах:
#   TRUSTED_PROXY_IPS=10.0.0.5,10.0.0.6
def _parse_trusted_proxies():
    raw = os.environ.get('TRUSTED_PROXY_IPS', '127.0.0.1, ::1')
    return {item.strip() for item in raw.split(',') if item.strip()}

TRUSTED_PROXIES = _parse_trusted_proxies()

# За доверенным прокси реальный IP клиента — это ПОСЛЕДНЯЯ запись
# X-Forwarded-For (ту добавляет первый прокси сам, например nginx c
# $proxy_add_x_forwarded_for). Записи ДО нее клиент может подделывать —
# они игнорируются. Без прокси заголовок вообще не читается.
# ВАЖНО: приложение должно быть доступно только доверенным прокси
# (gunicorn на 127.0.0.1, наружу только порт nginx).
def _client_ip():
    remote = get_remote_address()
    if remote in TRUSTED_PROXIES:
        xff = request.headers.get('X-Forwarded-For')
        if xff:
            entries = [e.strip() for e in xff.split(',') if e.strip()]
            candidate = entries[-1] if entries else ''
            try:
                ipaddress.ip_address(candidate)
                return candidate
            except ValueError:
                pass
    return remote

limiter = Limiter(key_func=_client_ip)

def create_app(config_class=Config):
    # Создаем экземпляр Flask-приложения
    app = Flask(__name__)

    # Загружаем конфигурацию из класса Config (в файле config.py)
    app.config.from_object(config_class)

    # Production-конфиг требует явного SECRET_KEY: без заданного ключа
    # известный статический секрет позволил бы подделать админ-сессию.
    if config_class is Config and not app.config.get('SECRET_KEY'):
        raise RuntimeError(
            'SECRET_KEY не задан. Установите переменную окружения SECRET_KEY '
            'или запустите локальную разработку через DevConfig (python run.py).'
        )

    # Защита от CSRF для всех POST-запросов
    csrf = CSRFProtect(app)

    # Rate limiting (защита от брутфорса /admin/login)
    # Хранилище счётчиков — in-memory (на воркер); при необходимости — redis через RATELIMIT_STORAGE_URI
    app.config["RATELIMIT_STORAGE_URI"] = "memory://"
    limiter.init_app(app)

    @app.errorhandler(429)
    def too_many_requests(e):
        return (
            "<!DOCTYPE html><html lang='ru'><head><meta charset='UTF-8'>"
            "<title>Слишком много попыток</title></head>"
            "<body style='font-family:sans-serif;max-width:420px;margin:100px auto;'>"
            "<h2>Слишком много попыток входа</h2>"
            "<p>Повторите попытку через 15 минут.</p>"
            "</body></html>"
        ), 429

    # Инициализируем базу данных для этого приложения
    db.init_app(app)

    # Импортируем Blueprints (модули маршрутов)
    from app.routes.user import user_bp
    from app.routes.admin import admin_bp
    from app.routes.main import main_bp

    # Регистрируем модули в приложении
    app.register_blueprint(user_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(main_bp)
    
    # Создаем таблицы в базе данных (если их еще нет)
    with app.app_context():
        db.create_all()

    return app