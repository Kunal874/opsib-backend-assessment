"""Command line entry point: python -m app.cli <command>

  migrate    apply pending SQL migrations
"""
import argparse
import json

from app.config import load_settings
from app.db import migrate


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="apply pending SQL migrations")
    args = parser.parse_args(argv)

    settings = load_settings()
    if args.command == "migrate":
        print(json.dumps({"applied": migrate(settings.database_url)}))


if __name__ == "__main__":
    main()
