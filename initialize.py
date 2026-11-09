"""Explicit schema initialization for development or external Render PostgreSQL."""
from sqlalchemy import select
from config import Settings
from database import Base, make_database
from models import BotState


def initialize():
    engine, factory = make_database(Settings().database_url.get_secret_value())
    Base.metadata.create_all(engine)
    with factory.begin() as db:
        if db.get(BotState, 1) is None:
            db.add(BotState(id=1, paused=False, update_offset=0))
    engine.dispose()
    print("Esquema MUSSETT inicializado sin eliminar datos.")


if __name__ == "__main__":
    initialize()
