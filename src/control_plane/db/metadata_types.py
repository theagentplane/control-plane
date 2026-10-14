"""The single SQLAlchemy ``MetaData`` every table is attached to.

The naming convention gives every constraint a stable name, which Alembic needs to alter
constraints on SQLite (batch mode) and to drop them on other databases.
"""

from sqlalchemy import MetaData

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)
"""Schema owned by Alembic: change it only together with a new migration revision."""
