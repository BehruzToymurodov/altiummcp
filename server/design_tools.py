"""
Project/document creation and PCB construction tools (altium-mcp-unified).

Registered onto the same FastMCP instance and Altium bridge as main.py, so
they share the request/response exchange, timeouts and stuck-script recovery.
The Altium side lives in AltiumScript/design_utils.pas.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

# Short layer names accepted by the tools -> Altium layer names (String2Layer)
LAYER_ALIASES = {
    "top": "Top Layer", "bottom": "Bottom Layer",
    "mid1": "Mid-Layer 1", "mid2": "Mid-Layer 2", "mid3": "Mid-Layer 3", "mid4": "Mid-Layer 4",
    "topoverlay": "Top Overlay", "bottomoverlay": "Bottom Overlay",
    "silk": "Top Overlay", "topsilk": "Top Overlay", "bottomsilk": "Bottom Overlay",
    "keepout": "Keep-Out Layer", "multi": "Multi-Layer", "multilayer": "Multi-Layer",
    "toppaste": "Top Paste", "bottompaste": "Bottom Paste",
    "topsolder": "Top Solder", "bottomsolder": "Bottom Solder",
}
for _i in range(1, 33):
    LAYER_ALIASES[f"mech{_i}"] = f"Mechanical {_i}"
    LAYER_ALIASES[f"mechanical{_i}"] = f"Mechanical {_i}"


def layer_name(name: str) -> str:
    key = str(name).replace(" ", "").replace("-", "").replace("_", "").lower()
    return LAYER_ALIASES.get(key, str(name))


def _clean(v: Any) -> str:
    """Spec fields are pipe-delimited lines: keep values on one line, no pipes."""
    return str(v).replace("|", "/").replace("\r", " ").replace("\n", " ")


def _num(v: Any) -> str:
    f = float(v)
    return str(int(f)) if f.is_integer() else f"{f:.4f}".rstrip("0")


def _flat_points(points: list, what: str) -> list[str]:
    """Accept [[x,y],...] or a flat [x,y,x,y,...] list."""
    if points and isinstance(points[0], (list, tuple)):
        flat = [c for p in points for c in p]
    else:
        flat = list(points or [])
    if len(flat) < 6 or len(flat) % 2:
        raise ValueError(f"{what} needs at least 3 [x, y] vertices")
    return [_num(c) for c in flat]


def prjpcb_text(documents: list[str]) -> str:
    """A minimal .PrjPcb. Altium fills in every omitted setting with its default."""
    out = ["[Design]", "Version=1.0", "HierarchyMode=0", "ChannelRoomNamingStyle=0",
           "ReleasesFolder=", "OutputPath=Project Outputs for $ProjectName", "LogFolderPath=",
           "ManagedProjectGUID=", ""]
    for i, rel in enumerate(documents, 1):
        out += [f"[Document{i}]", f"DocumentPath={rel}", "AnnotationEnabled=1",
                "AnnotateStartValue=1", "AnnotationIndexControlEnabled=0", "AnnotateSuffix=",
                "AnnotateScope=All", "AnnotateOrder=-1", "DoLibraryUpdate=1",
                "DoDatabaseUpdate=1", "ClassGenCCAutoEnabled=1", "ClassGenCCAutoRoomEnabled=1",
                "ClassGenNCAutoScope=None", "DItemRevisionGUID=", "GenerateClassCluster=0",
                "DocumentUniqueId=", ""]
    return "\r\n".join(out)


def build_pcb_spec(outline=None, tracks=None, arcs=None, vias=None,
                   polygons=None, fills=None, texts=None) -> list[str]:
    lines: list[str] = []
    if outline:
        lines.append("OUTLINE|" + "|".join(_flat_points(outline, "outline")))
    for t in tracks or []:
        pts = t.get("points")
        if not pts:
            pts = [[t["x1"], t["y1"]], [t["x2"], t["y2"]]]
        if len(pts) < 2:
            raise ValueError(f"track needs 2+ points: {t}")
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            lines.append("TRACK|{}|{}|{}|{}|{}|{}|{}".format(
                _num(x1), _num(y1), _num(x2), _num(y2), _num(t.get("width", 10)),
                _clean(layer_name(t.get("layer", "top"))), _clean(t.get("net", ""))))
    for a in arcs or []:
        lines.append("ARC|{}|{}|{}|{}|{}|{}|{}|{}".format(
            _num(a["cx"]), _num(a["cy"]), _num(a["radius"]), _num(a.get("start_angle", 0)),
            _num(a.get("end_angle", 360)), _num(a.get("width", 10)),
            _clean(layer_name(a.get("layer", "top"))), _clean(a.get("net", ""))))
    for v in vias or []:
        rec = "VIA|{}|{}|{}|{}|{}".format(_num(v["x"]), _num(v["y"]), _num(v.get("size", 24)),
                                          _num(v.get("hole", 12)), _clean(v.get("net", "")))
        if v.get("low_layer") or v.get("high_layer"):
            rec += "|{}|{}".format(_clean(layer_name(v.get("low_layer", "bottom"))),
                                   _clean(layer_name(v.get("high_layer", "top"))))
        lines.append(rec)
    for i, p in enumerate(polygons or [], 1):
        lines.append("POLY|{}|{}|{}|{}".format(
            _clean(layer_name(p.get("layer", "top"))), _clean(p.get("net", "")),
            _clean(p.get("name", f"Pour{i}")), "|".join(_flat_points(p["points"], "polygon"))))
    for f in fills or []:
        lines.append("FILL|{}|{}|{}|{}|{}|{}".format(
            _num(f["x1"]), _num(f["y1"]), _num(f["x2"]), _num(f["y2"]),
            _clean(layer_name(f.get("layer", "top"))), _clean(f.get("net", ""))))
    for t in texts or []:
        lines.append("TEXT|{}|{}|{}|{}|{}".format(
            _num(t["x"]), _num(t["y"]), _num(t.get("height", 60)),
            _clean(layer_name(t.get("layer", "topoverlay"))), _clean(t["text"])))
    return lines


def l_route(a: tuple[float, float], b: tuple[float, float], horizontal_first: bool) -> list:
    if a[0] == b[0] or a[1] == b[1]:
        return [list(a), list(b)]
    corner = (b[0], a[1]) if horizontal_first else (a[0], b[1])
    return [list(a), list(corner), list(b)]


# --------------------------------------------------------------- disk checks
# Altium can report a save as done when it wrote nothing, or wrote "Copy of
# <name>" instead (its answer to "a command is currently active"). These
# helpers compare the disk with what the tool was asked to write.

_MTIME_SLACK = 2.0   # seconds: file-system timestamp resolution and clock skew


def written_since(path: str, since: float) -> bool:
    """True if `path` exists and was modified at or after `since`."""
    try:
        return os.path.getmtime(path) >= since - _MTIME_SLACK
    except OSError:
        return False


def copies_created(folders, since: float) -> list:
    """'Copy of ...' files Altium wrote in `folders` at or after `since`."""
    found = []
    for folder in {str(Path(f)) for f in folders}:
        try:
            for entry in os.scandir(folder):
                if entry.is_file() and entry.name.lower().startswith("copy of ") \
                        and entry.stat().st_mtime >= since - _MTIME_SLACK:
                    found.append(entry.path)
        except OSError:
            pass
    return sorted(found)


def classify_save(docs: list, since: float, must_write: bool) -> dict:
    """Sort Altium's save attempts into saved / not_written / skipped.

    `must_write` is True when the caller named the files or forced the save:
    then a document Altium did not even attempt counts as not written, so
    the tool can never report success while nothing reached the disk.
    """
    saved, not_written, skipped = [], [], []
    for d in docs:
        p = d.get("path", "")
        if written_since(p, since):
            saved.append(p)
        elif d.get("attempted") or must_write:
            not_written.append(p)
        else:
            skipped.append(p)
    return {"saved": saved, "not_written": not_written, "skipped_unmodified": skipped}


def project_document_paths(prj_path: str) -> list:
    """DocumentPath= entries of a .PrjPcb, in file order (duplicates kept)."""
    try:
        text = Path(prj_path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []
    return [m.group(1).strip() for m in re.finditer(r"^DocumentPath=(.*)$", text, re.MULTILINE)]


def register(mcp, bridge, exchange_dir: Path, write_text: Callable[[Path, str], None], logger):
    """Attach the tools to `mcp`. `bridge.execute_command(cmd, params)` is async."""

    async def run(command: str, params: dict | None = None) -> dict:
        response = await bridge.execute_command(command, params or {})
        dialogs = response.get("dialogs") if isinstance(response, dict) else None
        if not response.get("success", False):
            r = {"success": False, "error": response.get("error", "Unknown error")}
        else:
            result = response.get("result", {})
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except ValueError:
                    pass
            r = {"success": True, "result": result}
        if dialogs:
            # Dialogs Altium raised during the command, each answered with its
            # safest button (never Yes) and photographed: see "snapshot"
            r["dialogs"] = dialogs
        return r

    def out(obj: Any) -> str:
        return json.dumps(obj, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------ projects

    @mcp.tool()
    async def create_project(project_path: str, schematic_sheets: list = None,
                             pcb_name: str = "", overwrite: bool = False) -> str:
        """
        Create a new Altium PCB project on disk with empty schematic sheet(s) and a
        PCB document, then open it in Altium.

        Args:
            project_path: full path of the .PrjPcb to create, e.g.
                "C:/Projects/CarRemote/CarRemote.PrjPcb". Folders are created.
            schematic_sheets: sheet names, e.g. ["Power", "MCU"]. Default ["Sheet1"].
            pcb_name: PCB document name. Default: the project name.
                Pass "none" to create the project without a PcbDoc.
            overwrite: replace an existing .PrjPcb file (documents are never overwritten).

        Next steps: draw with build_schematic(target="file", target_path=<sheet>),
        then update_pcb_from_schematic, then build_pcb / place_components.
        """
        prj = Path(project_path)
        if prj.suffix.lower() != ".prjpcb":
            return out({"success": False, "error": "project_path must end in .PrjPcb"})
        if prj.exists() and not overwrite:
            return out({"success": False, "error": f"{prj} already exists. Open it with "
                        "open_document, or pass overwrite=True to rewrite the project file."})
        sheets = schematic_sheets or ["Sheet1"]
        pcb = prj.stem if not pcb_name else pcb_name
        docs = [f"{Path(s).stem}.SchDoc" for s in sheets]
        if pcb.lower() != "none":
            docs.append(f"{Path(pcb).stem}.PcbDoc")

        prj.parent.mkdir(parents=True, exist_ok=True)
        prj.write_text(prjpcb_text(docs), encoding="utf-8")
        spec = [f"PROJECT|{prj}"]
        for d in docs:
            kind = "SCH" if d.lower().endswith(".schdoc") else "PCB"
            spec.append(f"DOC|{kind}|{prj.parent / d}")
        write_text(exchange_dir / "project_spec.txt", "\n".join(spec) + "\n")

        r = await run("create_project")
        if r["success"]:
            paths = [str(prj.parent / d) for d in docs]
            r["result"]["documents"] = paths
            missing = [p for p in paths if not os.path.isfile(p)]
            if missing:
                # Altium reported the documents as saved, but they are not on
                # disk: they exist only in memory and are lost when it closes
                r["success"] = False
                r["error"] = ("Altium reported these documents as created but they are not on "
                              "disk: " + ", ".join(missing) + ". Save them with "
                              "save_documents(paths=[...]) before closing Altium.")
                r["result"]["missing_on_disk"] = missing
        return out(r)

    @mcp.tool()
    async def add_document(path: str, add_to_project: bool = True, project_path: str = "") -> str:
        """
        Create a new empty schematic sheet (.SchDoc) or PCB (.PcbDoc) at `path`,
        add it to a project (unless add_to_project=False) and open it.
        Existing files are never overwritten.

        Args:
            project_path: the .PrjPcb to add it to. Strongly recommended: without
                it the focused project is used, which is often "Free Documents"
                or the MCP's script project, and then nothing is added.
        The file and the project's DocumentPath entry are checked on disk.
        """
        ext = Path(path).suffix.lower()
        if ext not in (".schdoc", ".pcbdoc"):
            return out({"success": False, "error": "path must end in .SchDoc or .PcbDoc"})
        kind = "SCH" if ext == ".schdoc" else "PCB"
        r = await run("add_document", {"path": str(path), "kind": kind,
                                       "add_to_project": add_to_project,
                                       "project": str(project_path)})
        if not r["success"]:
            return out(r)
        res = r["result"] if isinstance(r["result"], dict) else {}
        problems = []
        if not os.path.isfile(path):
            problems.append(f"{path} is not on disk")
        if add_to_project:
            prj = res.get("project") or project_path
            if not res.get("added_to_project"):
                problems.append("not added to a project: " +
                                res.get("not_added_reason", "no reason given"))
            elif prj:
                entries = [e.lower() for e in project_document_paths(prj)]
                try:
                    rel = os.path.relpath(path, Path(prj).parent).lower()
                except ValueError:
                    rel = str(path).lower()
                hits = entries.count(rel) + entries.count(str(path).lower())
                if hits == 0:
                    problems.append(f"{prj} on disk does not list {rel}")
                elif hits > 1:
                    problems.append(f"{prj} lists {rel} {hits} times (duplicate entries)")
        if problems:
            r["success"] = False
            r["error"] = "; ".join(problems)
        return out(r)

    @mcp.tool()
    async def open_document(path: str, focus: str = "") -> str:
        """
        Open a project (.PrjPcb) or document (.SchDoc, .PcbDoc, .SchLib, .PcbLib,
        .OutJob) in Altium and bring it to the front. For a project, focus="SCH"
        or "PCB" picks which of its documents is shown.
        """
        if not os.path.isfile(path):
            return out({"success": False, "error": f"file not found: {path}"})
        return out(await run("open_document", {"path": str(path), "focus": focus.upper()}))

    @mcp.tool()
    async def save_documents(scope: str = "active", paths: list = None, force: bool = True,
                             cancel_active_command: bool = True) -> str:
        """
        Save documents and check on disk that they were really written.

        Args:
            scope: "active" (the document in front) or "project" (every open
                document of the focused project, plus the .PrjPcb).
            paths: explicit document paths to save instead of `scope`, e.g.
                ["D:/P/Board.PcbDoc", "D:/P/lib/Footprints.PcbLib"]. They must be
                open in Altium. Use this after script edits to a document that
                is not in front.
            force: mark documents modified before saving (default True). Edits
                made through scripts do not set Altium's modified flag, and
                Altium skips unmodified documents.
            cancel_active_command: send Esc to Altium first (default True). An
                interactive command left running makes Altium refuse the save
                and offer "save copy of current document" instead.

        Returns saved (verified on disk), not_written (Altium did not write
        the file), copies_created ("Copy of ..." files written instead) and
        dialogs (any box Altium raised, answered with its safest button and
        photographed). success is false unless every file was written.
        """
        t0 = time.time()
        esc_sent = 0
        if cancel_active_command:
            try:
                import sandbox
                esc_sent = sandbox.cancel_active_command()
            except Exception as e:  # noqa: BLE001 - reported, not fatal
                logger.warning(f"could not send Esc to Altium: {e}")
            if esc_sent:
                await asyncio.sleep(0.5)
        params = {"scope": scope, "force": force,
                  "paths": "|".join(str(p) for p in paths) if paths else ""}
        r = await run("save_documents", params)
        if not r["success"]:
            return out(r)
        res = r["result"] if isinstance(r["result"], dict) else {}
        docs = res.get("documents", [])
        sorted_docs = classify_save(docs, t0, must_write=bool(paths) or force)
        not_written = sorted_docs["not_written"]
        copies = copies_created([Path(d.get("path", "")).parent for d in docs if d.get("path")], t0)
        result = {"saved": sorted_docs["saved"], "not_written": not_written,
                  "copies_created": copies,
                  "skipped_unmodified": sorted_docs["skipped_unmodified"],
                  "not_open": res.get("not_open", []),
                  "esc_sent_to_altium": esc_sent}
        ok = not not_written and not copies and not result["not_open"]
        reply = {"success": ok, "result": result}
        if r.get("dialogs"):
            reply["dialogs"] = r["dialogs"]
        if not ok:
            hints = []
            if not_written:
                hints.append("Altium did not write: " + ", ".join(not_written))
            if copies:
                hints.append("Altium wrote copies instead: " + ", ".join(copies))
            if result["not_open"]:
                hints.append("not open in Altium: " + ", ".join(result["not_open"]))
            hints.append("If a dialog said a command is active, click into the Altium "
                         "editor, press Esc and save again.")
            reply["error"] = " ".join(hints)
        return out(reply)

    @mcp.tool()
    async def update_pcb_from_schematic() -> str:
        """
        Get the project ready to push schematic parts and nets into the PCB:
        compiles the focused project and opens its PcbDoc. Altium has no scripting
        API for the ECO itself, so the user finishes with one click (returned in
        `next_step`). Afterwards use get_all_designators / place_components.
        """
        r = await run("prepare_pcb_update")
        if r["success"]:
            r["next_step"] = ("In Altium: Design > Import Changes From <project>.PrjPcb, "
                              "then click 'Execute Changes' and close the ECO dialog. "
                              "Tell me when done and I will place and route the parts.")
        return out(r)

    # ----------------------------------------------------------------- PCB

    @mcp.tool()
    async def build_pcb(outline: list = None, tracks: list = None, arcs: list = None,
                        vias: list = None, polygons: list = None, fills: list = None,
                        texts: list = None) -> str:
        """
        Add board outline and copper to the active PCB in one transaction.
        All coordinates in mils relative to the board origin.

        Args:
            outline: board shape vertices [[0,0],[2000,0],[2000,1500],[0,1500]]
                (replaces the current board shape; closes itself).
            tracks: [{"points": [[x,y],[x,y],...], "width": 12, "layer": "top",
                "net": "GND"}] - or x1/y1/x2/y2. Get pad positions from
                get_component_pins. 10-12 mil signals, 20-40 mil power.
            arcs: [{"cx","cy","radius","start_angle","end_angle","width","layer","net"}]
            vias: [{"x","y","size":24,"hole":12,"net"}] (top-bottom by default;
                low_layer/high_layer for blind/buried).
            polygons: copper pours [{"points": [...], "layer": "bottom", "net": "GND",
                "name": "GND_Bottom"}].
            fills: [{"x1","y1","x2","y2","layer","net"}]
            texts: silkscreen text [{"x","y","text","height":60,"layer":"topoverlay"}]
        Layers: top, bottom, mid1..mid4, topoverlay, bottomoverlay, keepout,
        mech1..mech32, or any Altium layer name. Unknown nets are reported in
        nets_not_found (the object is placed with no net).
        """
        try:
            lines = build_pcb_spec(outline, tracks, arcs, vias, polygons, fills, texts)
        except (KeyError, ValueError, TypeError) as e:
            return out({"success": False, "error": f"bad input: {e}"})
        if not lines:
            return out({"success": False, "error": "nothing to build"})
        write_text(exchange_dir / "pcb_spec.txt", "\n".join(lines) + "\n")
        return out(await run("build_pcb"))

    async def _pad_positions(designators: list[str]) -> dict:
        r = await bridge.execute_command("get_component_pins", {"designators": designators})
        if not r.get("success", False):
            raise RuntimeError(r.get("error", "could not read pads"))
        data = r.get("result", [])
        if isinstance(data, str):
            data = json.loads(data)
        pads = {}
        for comp in data if isinstance(data, list) else []:
            des = str(comp.get("designator", "")).upper()
            for p in comp.get("pins", []):
                pads[f"{des}.{str(p.get('name', '')).upper()}"] = p
        return pads

    @mcp.tool()
    async def route_pads(connections: list, width: float = 12, layer: str = "top",
                         horizontal_first: bool = True) -> str:
        """
        Route simple pad-to-pad tracks using real pad coordinates.

        Args:
            connections: [{"from": "U1.3", "to": "C4.1"}, ...] (designator.pad).
                Optional per item: width, layer, horizontal_first, via_points
                [[x,y],...] for a custom path between the pads.
            width / layer / horizontal_first: defaults for every connection.
        Each route is one straight or L-shaped track, netted with the pad's net.
        This is not an autorouter: check for crossings with check_placement,
        a screenshot, and get_pcb_violations afterwards.
        """
        names = sorted({c[k].split(".")[0].upper() for c in connections for k in ("from", "to")})
        try:
            pads = await _pad_positions(names)
        except Exception as e:  # noqa: BLE001 - reported to the caller
            return out({"success": False, "error": str(e)})
        tracks, problems = [], []
        for c in connections:
            a, b = pads.get(c["from"].upper()), pads.get(c["to"].upper())
            if not a or not b:
                problems.append(f"pad not found: {c['from'] if not a else c['to']}")
                continue
            pa, pb = (float(a["x"]), float(a["y"])), (float(b["x"]), float(b["y"]))
            pts = ([list(pa)] + [list(p) for p in c["via_points"]] + [list(pb)]
                   if c.get("via_points") else
                   l_route(pa, pb, c.get("horizontal_first", horizontal_first)))
            net = a.get("net") or b.get("net") or ""
            if a.get("net") and b.get("net") and a["net"] != b["net"]:
                problems.append(f"{c['from']} ({a['net']}) and {c['to']} ({b['net']}) "
                                "are on different nets - skipped")
                continue
            tracks.append({"points": pts, "width": c.get("width", width),
                           "layer": c.get("layer", layer), "net": net})
        if not tracks:
            return out({"success": False, "error": "no routable connections", "problems": problems})
        write_text(exchange_dir / "pcb_spec.txt", "\n".join(build_pcb_spec(tracks=tracks)) + "\n")
        r = await run("build_pcb")
        r["problems"] = problems
        r["routes"] = [{"net": t["net"], "points": t["points"]} for t in tracks]
        return out(r)

    @mcp.tool()
    async def get_pcb_violations(limit: int = 200) -> str:
        """
        List the design-rule violations Altium has marked on the active PCB
        (online DRC: clearance, short-circuit, width...). For a full batch check
        the user runs Tools > Design Rule Check.
        """
        return out(await run("get_pcb_violations", {"limit": int(limit)}))

    logger.info("design_tools registered")
