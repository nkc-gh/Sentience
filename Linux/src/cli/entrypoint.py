"""
SENTIENCE CLI Entrypoint.

Provides the `sentience` console command.
Starts/connects daemon, opens interactive shell.
"""

import sys


def main():
    """Entry point: `sentience` command."""
    from cli.shell import SentienceShell
    shell = SentienceShell()
    shell.run()


if __name__ == "__main__":
    main()
