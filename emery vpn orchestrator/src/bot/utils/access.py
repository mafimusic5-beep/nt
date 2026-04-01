from src.common.config import settings


def is_admin(telegram_id: int) -> bool:
    return telegram_id in settings.admin_id_list
