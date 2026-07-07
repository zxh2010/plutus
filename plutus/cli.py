"""`plutus <command>` dispatcher used by launchd and manual invocations.

    python -m plutus daemon      # background poller
    python -m plutus web         # web console
    python -m plutus ingest ...  # one-off ingest (passes through its own args)
"""
from __future__ import annotations

import sys

_USAGE = "usage: plutus {daemon|web|ingest} [args]"


def main(argv: list | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv.pop(0) if argv else ""
    if cmd == "daemon":
        from . import daemon
        daemon.main()
    elif cmd == "web":
        from .web import server
        server.main()
    elif cmd == "ingest":
        from . import ingest
        sys.argv = ["plutus ingest", *argv]   # ingest parses its own argv
        ingest.main()
    else:
        print(_USAGE, file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
