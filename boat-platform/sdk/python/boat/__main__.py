"""Entry point for `python3 -m boat`.

With subcommand arguments  → one-shot CLI (boat/cmd.py)
Without arguments          → interactive REPL (boat/cli.py)
"""

import sys

_SUBCOMMANDS = {"can", "pdu", "eth", "db"}

if __name__ == "__main__":
    # Strip the module name if it appears (python -m boat can send ...)
    # Find the first positional arg (skip flags like --db, --gateway and their values).
    argv = sys.argv[1:]
    first_positional = None
    skip_next = False
    for a in argv:
        if skip_next:
            skip_next = False
            continue
        if a in ("--db", "--gateway"):
            skip_next = True   # next token is the flag's value, not a subcommand
            continue
        if not a.startswith("-"):
            first_positional = a
            break

    if first_positional in _SUBCOMMANDS:
        from boat.cmd import main
        sys.exit(main())
    else:
        from boat.cli import main
        main()
