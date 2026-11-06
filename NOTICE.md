# Notice

**altium-mcp-unified** is a fork of [coffeenmusic/altium-mcp](https://github.com/coffeenmusic/altium-mcp)
(MIT License, see `LICENSE`, © its authors). All original code keeps that license.

Additions in this fork (MIT, © 2026 To'ymurodov Behruz):
- `server/design_tools.py`, `server/library_gateway.py`, `server/AltiumScript/design_utils.pas`
- target modes for `build_schematic` (`schematic_utils.pas`, `main.py`)
- dispatcher / focus-rule / config entries for the above

Optional companion program (not included, downloaded separately):
[embedded-society/altium-designer-mcp](https://github.com/embedded-society/altium-designer-mcp),
GPL-3.0. It runs as its own process and is reached over MCP by `library_gateway.py`;
none of its source is part of this package.

DelphiScript API patterns for polygons, the board outline and document creation were
checked against [BrettLMiller/Altium-DelphiScripts](https://github.com/BrettLMiller/Altium-DelphiScripts).
