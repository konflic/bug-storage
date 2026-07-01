"""Bug database service.

A small FastAPI service that stores Kubernetes (and general) bug reports in a
relational database. Designed to run on SQLite today and migrate to Postgres
later by only changing the DATABASE_URL environment variable.
"""

__version__ = "0.1.0"
