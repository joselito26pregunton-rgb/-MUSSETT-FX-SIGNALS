from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def make_database(url: str):
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    options = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, pool_pre_ping=True, connect_args=options, hide_parameters=True)
    return engine, sessionmaker(engine, expire_on_commit=False)


if __name__ == "__main__":
    import models  # noqa: F401
    from config import Settings
    engine, _ = make_database(Settings().database_url.get_secret_value())
    Base.metadata.create_all(engine)
    print("Esquema creado o comprobado. No se han eliminado datos.")
