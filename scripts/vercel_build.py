import os
import subprocess
import sys

from app.db.session import normalize_database_url


def main() -> int:
    raw_url = os.getenv("REROUTE_DATABASE_URL", "")
    normalized = normalize_database_url(raw_url)
    if not normalized.startswith(("postgresql://", "postgresql+psycopg2://")):
        print("Vercel build: no supported PostgreSQL database configured; skipping migrations.")
        return 0

    print("Vercel build: applying Alembic migrations to configured PostgreSQL database.")
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
