from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def utcnow():
    """Текущее время в UTC как «наивное» значение (без tzinfo).

    Всё время в системе хранится и сравнивается в UTC (наивное) — время
    связей из логов, окна туров и дедлайны. Локальная дата сервера не
    должна влиять на эти сравнения (V-10).
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


# База tzdata (IANA) доступна не на всех системах (на Windows — только после
# установки пакета tzdata). Если её нет, перевод зон невозможен, и время
# трактуется как UTC (без сдвига) — приложение остаётся работоспособным.
try:
    _UTC = ZoneInfo('UTC')
    _TZDATA_AVAILABLE = True
except ZoneInfoNotFoundError:
    _UTC = timezone.utc
    _TZDATA_AVAILABLE = False


def utc_minutes(value):
    """Сдвиг зоны value (например 'Europe/Vladivostok') от UTC в минутах."""
    if not _TZDATA_AVAILABLE:
        return 0
    try:
        zone = ZoneInfo(value)
        today = utcnow().astimezone(zone)
        utcoffset = today.utcoffset() or timedelta(0)
        return int(utcoffset.total_seconds() // 60)
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        return 0


def parse_local_dt_input(value, tz_name):
    """Значение datetime-local из формы (+ тайм-зона из конструктора) -> UTC naive.

    Если выбрана зона (не UTC), входной «местный» datetime переводится в UTC.
    Без базы tzdata перевод невозможен — значение принимается как UTC.
    Григорий в конструкторе: UTC или местное время + выбор пояса (V-10).
    """
    naive = datetime.strptime(value, '%Y-%m-%dT%H:%M')
    tz_name = (tz_name or '').strip()
    if not _TZDATA_AVAILABLE or not tz_name or tz_name.upper() in ('UTC', 'GMT'):
        return naive
    try:
        return naive.replace(tzinfo=ZoneInfo(tz_name)).astimezone(_UTC).replace(tzinfo=None)
    except (ZoneInfoNotFoundError, ValueError):
        return naive


def dt_for_edit_form(utc_naive, tz_name):
    """Красивое (обратное) представление для datetime-local в форме редактирования.

    Хранится в UTC naive; показываем в выбранной зоне. Для UTC — без сдвига.
    """
    if utc_naive is None:
        return ''
    tz_name = (tz_name or '').strip()
    if not _TZDATA_AVAILABLE or not tz_name or tz_name.upper() in ('UTC', 'GMT'):
        return utc_naive.strftime('%Y-%m-%dT%H:%M')
    try:
        local = utc_naive.replace(tzinfo=_UTC).astimezone(ZoneInfo(tz_name))
        return local.replace(tzinfo=None).strftime('%Y-%m-%dT%H:%M')
    except (ZoneInfoNotFoundError, ValueError):
        return utc_naive.strftime('%Y-%m-%dT%H:%M')


def zone_str_to_utc(value, tz_name):
    """Строка datetime-local ('YYYY-MM-DDTHH:MM') в выбранной зоне -> UTC naive."""
    return parse_local_dt_input(value, tz_name)


def utc_to_zone_str(value_utc_naive, tz_name):
    """UTC naive (datetime или строка 'YYYY-MM-DDTHH:MM') -> та же строка в зоне."""
    if isinstance(value_utc_naive, str):
        try:
            value_utc_naive = datetime.strptime(value_utc_naive, '%Y-%m-%dT%H:%M')
        except ValueError:
            return value_utc_naive
    return dt_for_edit_form(value_utc_naive, tz_name)