import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# Ortam değişkeninden URL'yi alıyoruz
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@db:5432/agenda")

# 🎯 KRİTİK ZIRH: Celery (Multiprocessing) ve Asyncpg çakışmasını önlemek için NullPool kullanıyoruz.
# Böylece her task, havuzdaki eski/kilitli bağlantılar yerine yepyeni bir bağlantı açıp kapatır.
engine = create_async_engine(
    DATABASE_URL, 
    poolclass=NullPool, 
    echo=False,
    connect_args={"command_timeout": 5} # 5 saniye içinde cevap gelmezse düşer
)

AsyncSessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

# Dışarıdan doğrudan motoru (engine) kullanmak isteyen fonksiyonlar için (örneğin tabloları yaratırken)
# engine nesnesi zaten yukarıda global olarak dışa aktarılmış oldu.