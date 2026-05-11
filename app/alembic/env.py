import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# 1. Bizim proje ayarlarımızı ve modellerimizi içeri alıyoruz
from app.core.config import settings
from app.models.base_class import Base
# Bu import, tüm tabloların görünmesi için şarttır:
from app.db.base import *
config = context.config

# Loglama ayarları
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 2. Hedef Metadata (Modellerimiz)
target_metadata = Base.metadata

# 3. Veritabanı URL'sini Config'den alıyoruz (alembic.ini yerine)
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

def run_migrations_offline() -> None:
    """Çevrimdışı (bağlantısız) migration modu."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()

def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()

async def run_migrations_online() -> None:
    """Çevrimiçi (bağlantılı) migration modu."""
    # Asenkron motoru oluştur
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()

if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())