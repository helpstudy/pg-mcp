"""MCP 客户端底座: 连接 pg-mcp 服务器并调用 query 工具.

用法 (在项目根目录下):
    uv run python scripts/mcp_client.py                            # 默认问题
    uv run python scripts/mcp_client.py "sm_user 表里有多少条记录?"
    uv run python scripts/mcp_client.py --sql "查一下用户表的结构"   # 仅生成 SQL 不执行

服务器作为子进程启动 (stdio transport), 读取项目根目录的 .env 配置
(数据库 + LLM 网关), 无需额外环境变量.
"""

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parents[1]


async def run(question: str, return_type: str = "result") -> None:
    """Spawn the pg-mcp server, initialize MCP, and call the query tool."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "pg_mcp"],
        cwd=str(PROJECT_ROOT),
    )

    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        names = [t.name for t in tools.tools]
        print(f"[connected] tools: {names}")
        print(f"[question ] {question}")
        print("-" * 60)

        result = await session.call_tool(
            "query",
            {"question": question, "return_type": return_type},
        )

        if result.isError:
            print("[ERROR] tool reported failure")

        printed_structured = False
        if getattr(result, "structuredContent", None):
            print(json.dumps(result.structuredContent, ensure_ascii=False, indent=2))
            printed_structured = True

        for block in result.content:
            text = getattr(block, "text", None)
            if text is None:
                continue
            # 结构化内容已打印过时避免重复输出整段 JSON
            if printed_structured and text.strip().startswith("{"):
                continue
            print(text)

        print("-" * 60)
        print("[done]")


if __name__ == "__main__":
    args = sys.argv[1:]
    return_type = "sql" if "--sql" in args else "result"
    args = [a for a in args if a != "--sql"]
    question = " ".join(args) if args else "数据库里有哪些表?"
    asyncio.run(run(question, return_type))
