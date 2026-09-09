from flask import Flask
from config import Config

# Импортируем объект базы данных из models.py
from app.models import db

def create_app(config_class=Config):
    # Создаем экземпляр Flask-приложения
    app = Flask(__name__)
    
    # Загружаем конфигурацию из класса Config (в файле config.py)
    app.config.from_object(config_class)

    # Инициализируем базу данных для этого приложения
    db.init_app(app)

    # Импортируем Blueprints (модули маршрутов)
    from app.routes.user import user_bp
    from app.routes.admin import admin_bp

    # Регистрируем модули в приложении
    app.register_blueprint(user_bp)
    app.register_blueprint(admin_bp)

    # Создаем таблицы в базе данных (если их еще нет)
    with app.app_context():
        db.create_all()

    return app