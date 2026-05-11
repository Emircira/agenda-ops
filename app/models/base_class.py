from typing import Any
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import MetaData

# İsimlendirme kuralı (Constraint naming convention)
# Bu kısım çok önemlidir; Alembic migration oluştururken 
# "constraint ismi çakışması" yaşamamak için standart bir yapı kurar.
naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=naming_convention)

    # Otomatik tablo ismi (ClassName -> classname)
    # Örn: UserProfile class'ı -> userprofile tablosu olur.
    @property
    def __tablename__(cls) -> str:
        return cls.__name__.lower()