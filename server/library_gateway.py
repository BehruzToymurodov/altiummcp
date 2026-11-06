"""
Gateway to embedded-society/altium-designer-mcp (GPL-3.0, Rust), which reads
and writes .SchLib / .PcbLib files directly - no Altium needed, so it also
works while Altium is closed or busy.

It runs as a separate program and is talked to over MCP (stdio). Nothing of
its source is included here; the user downloads its release binary. Two tools
are exposed: `library_tools` (what it can do) and `library_call` (do it).

Configuration (environment or %USERPROFILE%\\.altium-mcp\\config.json):
    ALTIUM_LIB_MCP_EXE   path to altium-designer-mcp(.exe)
    ALTIUM_LIB_DIRS      library folders it may touch, separated by ';'
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

CALL_TIMEOUT_S = 120


def _config(data_dir: Path) -> dict:
    cfg = {}
    f = data_dir / "config.json"
    if f.is_file():
        try:
            cfg = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            cfg = {}
    exe = os.environ.get("ALTIUM_LIB_MCP_EXE") or cfg.get("library_mcp_exe", "")
    if not exe:
        for cand in (data_dir / "bin" / "altium-designer-mcp.exe",
                     data_dir / "bin" / "altium-designer-mcp"):
            if cand.is_file():
                exe = str(cand)
                break
    if not exe:
        exe = shutil.which("altium-designer-mcp") or ""
    dirs = os.environ.get("ALTIUM_LIB_DIRS") or cfg.get("library_dirs", "")
    if isinstance(dirs, str):
        dirs = [d for d in dirs.split(";") if d.strip()]
    return {"exe": exe, "dirs": dirs}


def _content_to_text(result) -> str:
    parts = []
    for item in getattr(result, "content", []) or []:
        kind = getattr(item, "type", "")
        if kind == "text":
            parts.append(item.text)
        elif kind == "image":
            parts.append(f"[image {getattr(item, 'mimeType', '')} omitted]")
        else:
            parts.append(str(item))
    return "\n".join(parts)


async def _with_session(cfg: dict, fn):
    args = []
    for d in cfg["dirs"]:
        args += ["--allow", d]
    params = StdioServerParameters(command=cfg["exe"], args=args)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await fn(session)


def register(mcp, data_dir: Path, logger):
    import asyncio

    def missing() -> str | None:
        cfg = _config(data_dir)
        if not cfg["exe"] or not os.path.isfile(cfg["exe"]):
            return json.dumps({
                "success": False,
                "error": "The library-file server is not installed. Download "
                         "altium-designer-mcp from github.com/embedded-society/"
                         "altium-designer-mcp/releases and put the executable at "
                         f"{data_dir / 'bin' / 'altium-designer-mcp.exe'}, or set "
                         "ALTIUM_LIB_MCP_EXE. Set ALTIUM_LIB_DIRS to your library folders."})
        return None

    @mcp.tool()
    async def library_tools() -> str:
        """
        List the offline library-file tools (from altium-designer-mcp): read/write
        .SchLib and .PcbLib files without Altium running - create IPC-7351B
        footprints and symbols, render ASCII previews, diff/merge/validate/repair
        libraries, rename/copy/delete components, manage backups.
        Returns each tool's name, description and input schema for library_call.
        """
        err = missing()
        if err:
            return err
        cfg = _config(data_dir)

        async def go(session):
            res = await session.list_tools()
            return [{"name": t.name, "description": t.description,
                     "input_schema": t.inputSchema} for t in res.tools]
        try:
            tools = await asyncio.wait_for(_with_session(cfg, go), CALL_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001
            logger.error(f"library_tools failed: {e}")
            return json.dumps({"success": False, "error": f"library server failed: {e}"})
        return json.dumps({"success": True, "allowed_dirs": cfg["dirs"], "tools": tools},
                          indent=2)

    @mcp.tool()
    async def library_call(tool: str, arguments: dict = None) -> str:
        """
        Run one offline library-file tool by name (see library_tools for names
        and argument schemas), e.g. library_call("read_pcblib",
        {"filepath": "C:/Libs/Passives.PcbLib"}). Files must sit in a folder
        listed in ALTIUM_LIB_DIRS. Close the library in Altium before writing
        to it, or Altium may overwrite the change.
        """
        err = missing()
        if err:
            return err
        cfg = _config(data_dir)

        async def go(session):
            return await session.call_tool(tool, arguments or {})
        try:
            res = await asyncio.wait_for(_with_session(cfg, go), CALL_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001
            logger.error(f"library_call {tool} failed: {e}")
            return json.dumps({"success": False, "error": f"library server failed: {e}"})
        return json.dumps({"success": not getattr(res, "isError", False),
                           "result": _content_to_text(res)}, indent=2, ensure_ascii=False)

    logger.info("library_gateway registered")
