"""
SENTIENCE CLI Entrypoint.

Provides the `sentience` console command.
Starts/connects daemon, opens interactive shell.
"""

import sys


def main():
    """Entry point: `sentience` command."""
    # Windows PowerShell can start Python with a legacy cp1252 stream even
    # when the terminal itself supports Unicode.  Rich's banner and panels
    # contain Unicode glyphs, so normalize the streams before importing the
    # CLI modules that create their Console instances.
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError):
                pass
    from cli.shell import SentienceShell
    shell = SentienceShell()
    shell.run()


if __name__ == "__main__":
    main()
