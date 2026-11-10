from alembic import context

from dashboard.database import load_database_settings, make_engine


def run() -> None:
    engine = make_engine(load_database_settings())
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=None)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run()
