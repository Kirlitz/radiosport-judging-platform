import hashlib
import hmac
import secrets
from functools import wraps

from flask import g, session, redirect, url_for, flash, abort

from app.models import db, User, Permission

# Каталог прав по умолчанию. Перечень легко расширить: достаточно добавить
# пару (код, описание) — запись появится в справочнике прав при следующем
# старте приложения и станет доступна для назначения пользователям.
DEFAULT_PERMISSIONS = [
    # Управление пользователями
    ('users.view',          'Просмотр списка пользователей'),
    ('users.create',        'Создание пользователей'),
    ('users.edit',          'Редактирование пользователей (логин, пароль, статус)'),
    ('users.permissions',   'Назначение и отзыв прав пользователей'),
    ('users.delete',        'Удаление пользователей'),
    # Управление соревнованиями
    ('competitions.view',   'Просмотр панели и списка соревнований'),
    ('competitions.create', 'Создание соревнований'),
    ('competitions.edit',   'Редактирование соревнований'),
    ('competitions.delete', 'Удаление соревнований'),
    ('competitions.judge',  'Запуск судейства соревнований'),
    ('competitions.logs',   'Просмотр и удаление отчетов участников'),
    ('competitions.export', 'Просмотр и экспорт результатов (UBN, Excel)'),
]


def get_current_user():
    """Возвращает текущего авторизованного пользователя или None.

    Кэшируется в flask.g на время запроса. Если аккаунт удален или
    заблокирован (is_active=False), считается неавторизованным.
    Дополнительно сверяет токен сессии (auth_token) с хешем в БД:
    при смене пароля/деактивации все старые сессии становятся недействительными.
    """
    if 'current_user' in g:
        return g.get('current_user')
    uid = session.get('user_id')
    user = db.session.get(User, uid) if uid else None
    if user is None or not user.is_active:
        user = None
    elif not verify_session_token(session.get('auth_token'), user.session_token_hash):
        user = None
    g.current_user = user
    return user


def generate_session_token():
    """Новый случайный токен сессии (хранится в cookie сессии)."""
    return secrets.token_urlsafe(32)


def hash_session_token(token):
    """Хеш токена для хранения в БД (не храним сам токен)."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def verify_session_token(token, token_hash):
    """Проверка токена сессии против хеша из БД (constant-time сравнение)."""
    if not token or not token_hash:
        return False
    return hmac.compare_digest(hash_session_token(token), token_hash)


def seed_default_permissions():
    """Наполняет справочник прав данными по умолчанию (идемпотентно)."""
    existing = {p.code for p in Permission.query.all()}
    for code, description in DEFAULT_PERMISSIONS:
        if code not in existing:
            db.session.add(Permission(code=code, description=description))
    db.session.commit()


def login_required(view):
    """Декоратор: доступ только для авторизованных пользователей."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if get_current_user() is None:
            flash('Требуется вход в систему.', 'warning')
            return redirect(url_for('admin.login'))
        return view(*args, **kwargs)
    return wrapped


def permission_required(code):
    """Декоратор: доступ при наличии конкретного права (или суперправ)."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = get_current_user()
            if user is None:
                flash('Требуется вход в систему.', 'warning')
                return redirect(url_for('admin.login'))
            if not user.has_permission(code):
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator