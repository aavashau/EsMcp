import shutil
from mcp import StdioServerParameters


def get_server_params() -> StdioServerParameters:
    """Return parameters for spawning the MCP server subprocess via stdio."""
    uv = shutil.which("uv") or "/home/aavash/snap/code/235/.local/bin/uv"
    return StdioServerParameters(
        command=uv,
        args=["run", "mcp-server"],
    )
