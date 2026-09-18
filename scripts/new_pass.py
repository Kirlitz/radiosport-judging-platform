import getpass
import os
import secrets
import sys

# Скрипт лежит в scripts/: добавляем корень проекта в sys.path,
# чтобы работали импорты app/ и config.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from config import DevConfig
from app.models import db, User, Permission, UserPermission
from app.auth import DEFAULT_PERMISSIONS
from werkzeug.security import generate_password_hash

if not os.environ.get('SECRET_KEY'):
    os.environ['SECRET_KEY'] = secrets.token_urlsafe(32)

app = create_app(DevConfig)

with app.app_context():
    username = input("Введите логин администратора [admin]: ").strip() or 'admin'
    user = User.query.filter_by(username=username).first()

    is_new = False
    if not user:
        print(f"Пользователь '{username}' не найден. Будет создан новый аккаунт с полными правами.")
        user = User(username=username, is_superuser=True, is_active=True)
        is_new = True

    new_password = getpass.getpass(f"Введите новый пароль для '{username}': ")
    confirm_password = getpass.getpass("Повторите новый пароль: ")

    if new_password == confirm_password:
        if len(new_password) < app.config.get('MIN_PASSWORD_LENGTH', 8):
            print(f"Ошибка: Пароль должен содержать не менее "
                  f"{app.config.get('MIN_PASSWORD_LENGTH', 8)} символов. Изменения отменены.")
        else:
            user.password_hash = generate_password_hash(new_password)
            if is_new:
                db.session.add(user)
                db.session.flush()
                # Новому аккаунту выдаем все права из справочника
                for code, _ in DEFAULT_PERMISSIONS:
                    if Permission.query.filter_by(code=code).first():
                        db.session.add(UserPermission(user_id=user.id, permission_code=code))
            db.session.commit()

            status = "создан" if is_new else "обновлен"
            print(f"Успешно! Аккаунт '{username}' {status}, пароль установлен.")
    else:
        print("Ошибка: Пароли не совпадают. Изменения отменены.")