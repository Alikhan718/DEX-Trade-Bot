import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.database import Setting, User, UserSettings

logger = logging.getLogger(__name__)


async def create_initial_user_settings(user_id: int, session: AsyncSession):
    """
    Creates initial settings for a user by ensuring all default settings from `settings`
    are present in `user_settings` for the given user_id.
    """
    # Check if the user exists
    stmt = select(User).where(User.telegram_id == user_id)
    result = await session.execute(stmt)
    user = result.unique().scalar_one_or_none()
    if not user:
        logger.error(f"Error: user with id:'{user_id}', not found")
        raise Exception('User not found')

    # Fetch all settings
    settings_query = await session.execute(select(Setting))
    all_settings = settings_query.scalars().all()

    # Fetch existing user settings
    user_settings_query = await session.execute(
        select(UserSettings.setting_id).where(UserSettings.user_id == user.id)
    )
    existing_setting_ids = {us[0] for us in user_settings_query.fetchall()}

    # Find missing settings
    missing_settings = [setting for setting in all_settings if setting.id not in existing_setting_ids]

    # Create missing user settings
    for setting in missing_settings:
        user_setting = UserSettings(
            user_id=user.id,
            setting_id=setting.id,
            value=setting.default_value  # Use the default value from `settings`
        )
        session.add(user_setting)
        logger.info(f"Added missing setting '{setting.name}' for user {user_id}")

    # Commit the changes
    await session.commit()
    logger.info(f"Initial user settings created/updated for user {user_id}")


async def get_user_settings(user_id: int, session: AsyncSession):
    """
    Retrieves all settings for a given user.
    """
    stmt = select(UserSettings, Setting)\
        .join(UserSettings.setting)\
        .join(UserSettings.user)\
        .where(
            User.telegram_id == user_id,
        )
    result = await session.execute(stmt)
    user_settings = result.scalars().all()

    settings_dict = {
        user_setting.setting.slug: user_setting.value for user_setting in user_settings
    }

    logger.info(f"Retrieved settings for user {user_id}")
    return settings_dict


async def get_user_setting(user_id: int, setting_slug: str, session: AsyncSession):
    """
    Retrieves a specific setting for a given user by setting slug.
    """
    stmt = (
        select(UserSettings.value)
        .join(UserSettings.setting)
        .join(UserSettings.user)
        .where(
            User.telegram_id == user_id,
            Setting.slug == setting_slug,
        )
    )
    print("STATEMENT", stmt)
    print("SLUG", setting_slug)
    result = await session.execute(stmt)
    user_setting = result.scalar_one_or_none()

    if not user_setting:
        logger.error(f"Setting '{setting_slug}' not found for user {user_id}")
        raise Exception(f"Setting '{setting_slug}' not found for user {user_id}")

    logger.info(f"Retrieved setting '{setting_slug}' for user {user_id}")
    return user_setting


async def update_user_setting(user_id: int, setting_slug: str, new_value, session: AsyncSession):
    """
    Если настройка существует - обновляем.
    Если нет - создаём новую запись.
    """
    stmt = (
        select(UserSettings)
        .join(UserSettings.setting)
        .join(UserSettings.user)
        .where(
            User.telegram_id == user_id,
            Setting.slug == setting_slug,
        )
    )
    result = await session.execute(stmt)
    user_setting = result.scalar_one_or_none()

    if not user_setting:
        # Пытаемся найти глобальную настройку
        setting_stmt = select(Setting).where(Setting.slug == setting_slug)
        setting_obj = await session.scalar(setting_stmt)
        if not setting_obj:
            logger.error(f"Global setting '{setting_slug}' not found in Setting table")
            raise Exception(f"Global setting '{setting_slug}' not found")

        # Ищем пользователя
        user_stmt = select(User).where(User.telegram_id == user_id)
        user_obj = await session.scalar(user_stmt)
        if not user_obj:
            logger.error(f"User with telegram_id '{user_id}' not found")
            raise Exception(f"User with telegram_id '{user_id}' not found")

        # Создаём новую запись
        user_setting = UserSettings(
            user_id=user_obj.id,
            setting_id=setting_obj.id,
            value=new_value
        )
        session.add(user_setting)
        logger.info(f"Created new user setting '{setting_slug}' for user {user_id} = {new_value}")
    else:
        user_setting.value = new_value
        session.add(user_setting)
        logger.info(f"Updated setting '{setting_slug}' for user {user_id} to {new_value}")

    await session.commit()
    return user_setting.value


