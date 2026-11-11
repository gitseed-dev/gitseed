"""Names for constraints the models don't name themselves (PKs, FKs, plain uniques
and indexes). There's no "ck" entry: check constraints are named explicitly, and a
ck convention would rename them. Imported first by models/__init__.py."""

from sqlmodel import SQLModel

SQLModel.metadata.naming_convention = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}