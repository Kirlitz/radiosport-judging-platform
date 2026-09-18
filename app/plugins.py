import importlib
import os

from flask import current_app

from app.qso_parser import DEFAULT_EXCHANGE_SPEC


def get_available_plugins():
    """Сканирует директорию app/judging и возвращает список доступных плагинов."""
    judging_dir = os.path.join(current_app.root_path, 'judging')
    plugins = []

    if os.path.exists(judging_dir):
        for filename in os.listdir(judging_dir):
            if filename.endswith('.py') and not filename.startswith('__'):
                module_name = filename[:-3]
                title = module_name
                try:
                    mod = importlib.import_module(f'app.judging.{module_name}')
                    if hasattr(mod, 'PLUGIN_TITLE'):
                        title = mod.PLUGIN_TITLE
                except Exception as e:
                    current_app.logger.error(f"Ошибка загрузки плагина {module_name}: {e}")
                plugins.append({'id': module_name, 'title': title})
    return plugins


def get_plugin_module(script_name):
    """Импортирует плагин судейства ТОЛЬКО по имени из белого списка
    (реально существующего файла в app/judging/). Возвращает модуль или None."""
    allowed = {p['id'] for p in get_available_plugins()}
    name = (script_name or '').strip()
    if name not in allowed:
        current_app.logger.warning("Попытка импорта неизвестного плагина '%s'", name)
        return None
    try:
        return importlib.import_module(f'app.judging.{name}')
    except Exception as e:
        current_app.logger.error(f"Ошибка импорта плагина {name}: {e}")
        return None


def get_plugin_title(comp):
    """Возвращает PLUGIN_TITLE выбранного для соревнования плагина (или пустую строку)."""
    if comp is None:
        return ''
    module = get_plugin_module(comp.scoring_script_filename)
    return getattr(module, 'PLUGIN_TITLE', '') if module else ''


def get_plugin_exchange_spec(comp):
    """Состав обмена (QSO_EXCHANGE_SPEC) плагина или состав по умолчанию
    (RST + контрольный номер на обеих сторонах)."""
    module = get_plugin_module(comp.scoring_script_filename) if comp is not None else None
    spec = getattr(module, 'QSO_EXCHANGE_SPEC', None) if module else None
    return spec or DEFAULT_EXCHANGE_SPEC


def get_judge_entrypoint(comp):
    """Возвращает вызываемую функцию судейства плагина или None."""
    module = get_plugin_module(comp.scoring_script_filename)
    if module is None:
        return None, None
    script_name = comp.scoring_script_filename
    if hasattr(module, 'run_judging'):
        return module.run_judging, None
    if hasattr(module, f'run_judging_{script_name}'):
        return getattr(module, f'run_judging_{script_name}'), None
    return None, (
        f'Ошибка: В плагине {script_name} не найдена функция run_judging(comp_id)'
    )