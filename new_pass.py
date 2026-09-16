import getpass
import os
import secrets

from app import create_app
from config import DevConfig
from app.models import db, Admin
from werkzeug.security import generate_password_hash

if not os.environ.get('SECRET_KEY'):
    os.environ['SECRET_KEY'] = secrets.token_urlsafe(32)

app = create_app(DevConfig)

with app.app_context():
    username = input("Введите логин администратора [admin]: ").strip() or 'admin'
    admin_user = Admin.query.filter_by(username=username).first()
    
    is_new = False
    if not admin_user:
        print(f"Пользователь '{username}' не найден. Будет создан новый аккаунт.")
        admin_user = Admin(username=username)
        is_new = True

    new_password = getpass.getpass(f"Введите новый пароль для '{username}': ")
    confirm_password = getpass.getpass("Повторите новый пароль: ")
    
    if new_password == confirm_password:
        admin_user.password_hash = generate_password_hash(new_password)
        if is_new:
            db.session.add(admin_user)
        db.session.commit()
        
        status = "создан" if is_new else "обновлен"
        print(f"Успешно! Аккаунт '{username}' {status}, пароль уставновлен.")
    else:
        print("Ошибка: Пароли не совпадают. Изменения отменены.")