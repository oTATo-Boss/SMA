"""实测用的最小 MCP 服务：两个工具，用来验证按次挂载和工具白名单。"""

from mcp.server.mcpserver import MCPServer

server = MCPServer("spike")


@server.tool()
def spike_echo(text: str) -> str:
    """原样返回文字，并加上固定前缀。"""
    return f"ECHO-7319:{text}"


@server.tool()
def spike_hidden(text: str) -> str:
    """不该被暴露的工具：白名单生效时，模型看不到它。"""
    return f"HIDDEN:{text}"


if __name__ == "__main__":
    server.run()
