import ipaddress
import os

from flask import Flask, request, render_template
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
    # Хранилище счётчиков — in-memory (на воркер); для production можно задать
    # RATELIMIT_STORAGE_URI=redis://... в переменной окружения (F5)
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

    # Безопасные HTTP-заголовки на все ответы
    @app.after_request
    def set_security_headers(response):
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'DENY')
        response.headers.setdefault('Referrer-Policy', 'same-origin')
        response.headers.setdefault(
            'Content-Security-Policy',
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' data:; font-src 'self' https://cdn.jsdelivr.net; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        response.headers.setdefault(
            'Permissions-Policy', 'camera=(), geolocation=(), microphone=()'
        )
        if app.config.get('SESSION_COOKIE_SECURE'):
            response.headers.setdefault(
                'Strict-Transport-Security', 'max-age=31536000; includeSubDomains'
            )
        return response

    # Доступ в шаблонах к текущему пользователю (для показа ссылок и кнопок
    # в зависимости от прав).
    @app.context_processor
    def inject_current_user():
        from app.auth import get_current_user
        return {'current_user': get_current_user()}

    @app.errorhandler(403)
    def forbidden(e):
        return render_template('403.html'), 403

    # Импортируем Blueprints (модули маршрутов)
    from app.routes.user import user_bp
    from app.routes.admin import admin_bp
    from app.routes.main import main_bp

    # Регистрируем модули в приложении
    app.register_blueprint(user_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(main_bp)
    
    # Создаем таблицы в базе данных (если их еще нет).
    # db.create_all() создает таблицы во всех привязках: соревнования —
    # в judging.db, пользователи (User/Permission/UserPermission) — в users.db.
    with app.app_context():
        db.create_all()
        _ensure_session_token_column(app)
        seed_users_db(app)

    return app


def _ensure_session_token_column(app):
    """Одноразовая миграция: добавляет колонку session_token_hash в users.db.

    create_all() не добавляет колонки в существующую таблицу, поэтому при
    обновлении снизу-вверх выполняем ALTER TABLE (идемпотентно).
    """
    from sqlalchemy import inspect, text
    try:
        bind = db.get_engine(app, bind='users')
        inspector = inspect(bind)
        if 'user' not in inspector.get_table_names():
            return
        columns = {c['name'] for c in inspector.get_columns('user')}
        if 'session_token_hash' not in columns:
            with bind.begin() as conn:
                conn.execute(text('ALTER TABLE "user" ADD COLUMN session_token_hash VARCHAR(64)'))
    except Exception:
        app.logger.exception("Не удалось выполнить миграцию session_token_hash")


def seed_users_db(app):
    """Одноразовая подготовка базы пользователей.

    1. Наполняет справочник прав (Permission) каталогом по умолчанию.
    2. Если users.db пуст, а в judging.db остались аккаунты из старой
       таблицы admin (до разделения баз), переносит их как суперпользователей
       с полными правами. Признаки: admin и ua0lid.
    """
    from sqlalchemy import inspect
    from app.auth import seed_default_permissions
    from app.models import User, UserPermission, Permission

    seed_default_permissions()

    try:
        inspector = inspect(db.engine)
        if 'admin' not in inspector.get_table_names():
            return
        if User.query.count() > 0:
            return

        rows = db.session.execute(
            db.text("SELECT id, username, password_hash FROM admin")
        ).fetchall()
        if not rows:
            return

        all_codes = {p.code for p in Permission.query.all()}
        for row in rows:
            if not row.username or not row.password_hash:
                continue
            user = User(
                username=row.username,
                password_hash=row.password_hash,
                is_superuser=True,
                is_active=True,
            )
            db.session.add(user)
            db.session.flush()
            for code in all_codes:
                db.session.add(UserPermission(user_id=user.id, permission_code=code))
            app.logger.info(
                "Аккаунт '%s' перенесен из judging.db в users.db (полные права)",
                row.username,
            )
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Не удалось перенести старых администраторов в users.db")