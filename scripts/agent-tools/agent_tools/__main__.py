"""Versioned JSON output for the shared local helper commands."""

import argparse
import json
import sys

from . import __version__
from .doctor import diagnose
from .runner import run_command
from .search import search


class UsageError(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = Parser(description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    subs = parser.add_subparsers(dest="command", required=True)
    doctor = subs.add_parser("doctor", help="Read-only repository and executable preflight")
    doctor.add_argument("--repo", default=".")
    doctor.add_argument("--require", action="append", default=[], metavar="EXECUTABLE")
    run = subs.add_parser("run", help="Run foreground argv with bounded output and a deadline")
    run.add_argument("--cwd", default=".")
    run.add_argument("--timeout", type=float, default=120)
    run.add_argument("--max-output-bytes", type=int, default=65_536, help="Per-stream capture cap")
    run.add_argument("argv", nargs=argparse.REMAINDER)
    find = subs.add_parser("search", help="Bounded literal ripgrep search with coverage status")
    find.add_argument("--root", required=True)
    find.add_argument("--literal", required=True, dest="pattern")
    find.add_argument("--limit", type=int, default=40)
    find.add_argument("--max-files", type=int, default=2000)
    find.add_argument("--max-bytes", type=int, default=33_554_432, help="Selected file-size budget (snapshot)")
    find.add_argument("--max-file-bytes", type=int, default=1_048_576)
    find.add_argument("--max-output-bytes", type=int, default=65_536)
    find.add_argument("--timeout", type=float, default=10)
    find.add_argument("--hidden", action="store_true")
    find.add_argument("--follow", action="store_true", help="Follow links whose targets stay inside the root")
    find.add_argument("--glob", action="append", default=[], dest="globs")
    try:
        args = parser.parse_args(argv)
    except UsageError:
        print(json.dumps({"schema_version": 1,
                          "command": argv[0] if argv and argv[0] in {"doctor", "run", "search"} else None,
                          "status": "error", "error_code": "invalid_cli_arguments",
                          "message": "Use agent-tools <command> --help for arguments."}, separators=(",", ":")))
        return 2
    try:
        if args.command == "doctor":
            result, code = diagnose(args.repo, args.require)
        elif args.command == "run":
            command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
            result, code = run_command(command, cwd=args.cwd, timeout=args.timeout,
                                       max_output_bytes=args.max_output_bytes)
        else:
            values = vars(args).copy()
            values.pop("command")
            result, code = search(**values)
    except KeyboardInterrupt:
        result, code = {"status": "interrupted", "error_code": "interrupted"}, 130
    print(json.dumps({"schema_version": 1, "command": args.command, **result},
                     ensure_ascii=True, allow_nan=False, separators=(",", ":")))
    return code


if __name__ == "__main__":
    sys.exit(main())
