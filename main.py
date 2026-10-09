"""Entry point for PostgreSQL MCP Server.

Running ``python main.py`` is equivalent to ``python -m pg_mcp``: both start
the FastMCP server over stdio transport with full lifespan management.
"""

from pg_mcp.__main__ import main

if __name__ == "__main__":
    main()
