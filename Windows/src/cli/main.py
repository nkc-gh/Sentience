"""
SENTIENCE Shell entry point.
Re-exported from cli.shell.
"""

from cli.shell import COMMAND_META, SentienceShell, main

SentienceREPL = SentienceShell

__all__ = ["COMMAND_META", "SentienceREPL", "SentienceShell", "main"]
