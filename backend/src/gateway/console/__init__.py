"""Package marker: the read-only console MCP lives here."""

from gateway.console.server import ConsoleVerifier, build_console
from gateway.console.tokens import BadToken, mint, read

__all__ = ["BadToken", "ConsoleVerifier", "build_console", "mint", "read"]
