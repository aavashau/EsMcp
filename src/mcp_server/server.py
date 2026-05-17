from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Healthcare ES MCP")

from .tools import register_tools  # noqa: E402
register_tools(mcp)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
