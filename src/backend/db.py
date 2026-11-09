import os

from sqlalchemy import MetaData, Table, URL, create_engine


engine = create_engine(
    URL.create(
        "postgresql+psycopg",
        username="alo_app",
        password=os.environ["DB_PASSWORD"],
        host="127.0.0.1",
        port=5432,
        database="alo_db",
    ),
    pool_pre_ping=True,
    connect_args={"connect_timeout": 5},
)
metadata = MetaData()


def load_tables(connection):
    # Reuse the installed schema; this never creates or alters tables.
    for name in ("files", "sites", "locations", "schedules", "schedule_sources", "schedule_tasks",
                 "schedule_task_locations", "gt_datasets", "model_versions", "daily_records", "weekly_reports",
                 "question_archives"):
        Table(name, metadata, autoload_with=connection, resolve_fks=False)
    return metadata.tables
