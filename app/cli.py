"""Command line entry point: python -m app.cli <command>

  migrate              apply pending SQL migrations
  load-fixtures DIR    import every supplied CSV from DIR through the same importer the API uses
"""
import argparse
import json
from pathlib import Path

from app.config import load_settings
from app.db import connect, migrate
from app.ingestion.importer import LOAD_ORDER, import_csv
from app.log import configure_logging


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="apply pending SQL migrations")
    load = commands.add_parser("load-fixtures", help="import all six CSVs from a directory")
    load.add_argument("directory", type=Path)
    args = parser.parse_args(argv)

    settings = load_settings()
    configure_logging(settings.log_level)  # JSON logs go to stderr; results below go to stdout

    if args.command == "migrate":
        print(json.dumps({"applied": migrate(settings.database_url)}))
    elif args.command == "load-fixtures":
        with connect(settings.database_url) as conn:
            for name in LOAD_ORDER:
                path = args.directory / f"{name}.csv"
                report = import_csv(conn, settings.tenant_id, name, path.read_bytes(), path.name)
                print(json.dumps({"dataset": name, "status": report["status"], **report["counts"]}))


if __name__ == "__main__":
    main()
