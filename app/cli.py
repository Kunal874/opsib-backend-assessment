"""Command line entry point: python -m app.cli <command>

  migrate                      apply pending SQL migrations
  load-fixtures DIR            import every supplied CSV from DIR through the same importer the API uses
  run-detection [--as-of ISO]  evaluate every PO line (default: the frozen EVALUATION_TIME)
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

from app.config import load_settings
from app.db import connect, migrate
from app.detection.service import run_detection
from app.ingestion.importer import LOAD_ORDER, import_csv
from app.log import configure_logging


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="apply pending SQL migrations")
    load = commands.add_parser("load-fixtures", help="import all six CSVs from a directory")
    load.add_argument("directory", type=Path)
    detect = commands.add_parser("run-detection", help="evaluate every PO line and persist findings")
    detect.add_argument("--as-of", type=datetime.fromisoformat, help="override the frozen evaluation time")
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
    elif args.command == "run-detection":
        with connect(settings.database_url) as conn:
            result = run_detection(conn, settings, args.as_of)
        print(json.dumps({k: result[k] for k in ("run_id", "as_of", "counts", "exceptions")}))
        for line in result["lines"]:
            print(json.dumps({k: line[k] for k in ("po_number", "line_number", "outcome", "reason", "variance_qty")}))


if __name__ == "__main__":
    main()
