import os

from app import create_app
from config import Config, DevConfig

# Точка входа для Gunicorn (production):
#   FLASK_ENV=production gunicorn -w 3 -b 127.0.0.1:5000 run:app
# При этом SECRET_KEY обязан быть задан в переменной окружения (см. create_app).
#
# Локальная разработка: python run.py — используется DevConfig,
# переменная окружения SECRET_KEY не требуется.
config_class = Config if os.environ.get('FLASK_ENV') == 'production' else DevConfig
app = create_app(config_class)

if __name__ == '__main__':
    # Только локальная разработка: dev-сервер на 127.0.0.1,
    # сессия без Secure (HTTP).
    app.run(host='127.0.0.1', port=5000)