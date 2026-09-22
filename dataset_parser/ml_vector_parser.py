#!/usr/bin/env python3
"""Build ML vector datasets from OpenROAD-flow-scripts results.

The output layout intentionally mirrors the existing hand-curated datasets:

  <out>/<design>/
    place/vectors/<design>_place_vectors/vectors/<scenario>/...
    route/vectors/<design>_route_vectors/vectors/<scenario>/...

This parser is deliberately conservative. Geometry, connectivity, placement,
LEF tech/cell data, DEF routing, and SPEF net capacitance are parsed from local
files. If flow/reports/.../ml_reports exists, OpenSTA/OpenROAD timing and power
reports are folded back into the JSON features and wire_path files.
"""

from __future__ import annotations

import argparse
import csv
import zlib
import json
import math
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


SENTINEL_TIME = 1.1e20
DEFAULT_TRANSITIONS = [SENTINEL_TIME, SENTINEL_TIME, SENTINEL_TIME, SENTINEL_TIME]
ZERO_TRANSITIONS = [0, 0, 0, 0]
SKIP_INSTANCE_PREFIXES = (
    "FILLER",
    "TAPCELL",
    "PHY_EDGE",
    "DECAP",
)
SKIP_CELL_PREFIXES = (
    "FILL",
    "TAP",
    "ANTENNA",
    "DECAP",
)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def strip_def_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def normalize_design_name(name: str) -> str:
    return name.replace("\\[", "[").replace("\\]", "]").replace("\\", "")


def dataset_design_name(orfs_design: str) -> str:
    """Map ORFS result nicknames to the stable dataset design name."""
    name = orfs_design
    if name.startswith("bench_"):
        name = name[len("bench_") :]
    for suffix in (
        "_fastclk_bad_m3",
        "_fastclk_bad",
        "_grt_repair_off70",
        "_dense70",
    ):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


def dbu_float_to_int(value: str, dbu: int) -> int:
    return int(round(float(value) * dbu))


def is_signal_instance(name: str, cell: str) -> bool:
    upper_name = name.upper()
    upper_cell = cell.upper()
    return not any(upper_name.startswith(p) for p in SKIP_INSTANCE_PREFIXES) and not any(
        upper_cell.startswith(p) for p in SKIP_CELL_PREFIXES
    )


@dataclass
class Cell:
    id: int
    name: str
    width: int
    height: int
    pins: dict[str, str] = field(default_factory=dict)


@dataclass
class Instance:
    id: int
    name: str
    cell: str
    cell_id: int
    llx: int
    lly: int
    width: int
    height: int

    @property
    def cx(self) -> int:
        return self.llx + self.width // 2

    @property
    def cy(self) -> int:
        return self.lly + self.height // 2

    @property
    def urx(self) -> int:
        return self.llx + self.width

    @property
    def ury(self) -> int:
        return self.lly + self.height


@dataclass
class Port:
    name: str
    direction: str = "INOUT"
    x: int = 0
    y: int = 0


@dataclass
class Segment:
    x1: int
    y1: int
    layer1: int
    x2: int
    y2: int
    layer2: int
    via: str | None = None

    @property
    def length(self) -> int:
        if self.layer1 != self.layer2:
            return 0
        return abs(self.x1 - self.x2) + abs(self.y1 - self.y2)


@dataclass
class Net:
    id: int
    name: str
    pins: list[tuple[str, str]] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    capacitance: float = 0.0
    resistance: float = 0.0
    has_spef: bool = False


@dataclass
class ReportData:
    instance_power: dict[str, dict[str, float]] = field(default_factory=dict)
    net_metrics: dict[str, dict[str, float]] = field(default_factory=dict)
    pin_metrics: dict[str, dict[str, Any]] = field(default_factory=dict)
    cell_arc_delays: dict[tuple[str, str], float] = field(default_factory=dict)
    path_checks: list[dict[str, Any]] = field(default_factory=list)
    drc_markers: list[dict[str, Any]] = field(default_factory=list)
    congestion_markers: list[dict[str, Any]] = field(default_factory=list)
    drc_available: bool = False
    congestion_available: bool = False
    ir_points: list[dict[str, float]] = field(default_factory=list)
    net_power: dict[str, dict[str, Any]] = field(default_factory=dict)
    reports_dir: Path | None = None


@dataclass
class DefDesign:
    name: str
    dbu: int
    diearea: tuple[int, int, int, int]
    gcell_x: list[int]
    gcell_y: list[int]
    vias: dict[str, dict[str, Any]]
    instances: list[Instance]
    ports: dict[str, Port]
    nets: list[Net]


class LefParser:
    def __init__(self, lef_paths: Iterable[Path]):
        self.lef_paths = list(lef_paths)
        self.cells: dict[str, Cell] = {}
        self.layers: list[dict[str, Any]] = []
        self.layer_id: dict[str, int] = {}
        self.vias: dict[str, dict[str, Any]] = {}

    def parse(self, dbu: int) -> "LefParser":
        for path in self.lef_paths:
            if path.exists():
                self._parse_file(path, dbu)
        for idx, layer in enumerate(self.layers):
            self.layer_id[layer["name"]] = idx
        for idx, name in enumerate(sorted(self.cells)):
            self.cells[name].id = idx
        return self

    def _parse_file(self, path: Path, dbu: int) -> None:
        text = read_text(path)
        self._parse_layers(text, dbu)
        self.layer_id = {layer["name"]: idx for idx, layer in enumerate(self.layers)}
        self._parse_vias(text, dbu)
        self._parse_macros(text, dbu)

    def _parse_layers(self, text: str, dbu: int) -> None:
        seen = {layer["name"] for layer in self.layers}
        for match in re.finditer(r"(?ms)^\s*LAYER\s+(\S+)\s*(.*?)^\s*END\s+\1\b", text):
            name, body = match.group(1), match.group(2)
            layer_type = re.search(r"\bTYPE\s+(\S+)\s*;", body)
            if not layer_type:
                continue
            typ = layer_type.group(1).upper()
            if typ in {"ROUTING", "CUT"} and name not in seen:
                width = re.search(r"\bWIDTH\s+([.\d]+)\s*;", body)
                self.layers.append(
                    {
                        "id": len(self.layers),
                        "name": name,
                        "type": typ,
                        "width": dbu_float_to_int(width.group(1), dbu) if typ == "ROUTING" and width else 0,
                    }
                )
                seen.add(name)

    def _parse_vias(self, text: str, dbu: int) -> None:
        for match in re.finditer(r"(?ms)^\s*VIA\s+(\S+).*?^\s*END\s+\1\b", text):
            name, body = match.group(1), match.group(0)
            rects = re.findall(
                r"LAYER\s+(\S+)\s*;\s*RECT\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*;",
                body,
            )
            if len(rects) < 2:
                continue
            boxes = []
            for layer, llx, lly, urx, ury in rects[:3]:
                boxes.append(
                    {
                        "layer": layer,
                        "box": {
                            "llx": dbu_float_to_int(llx, dbu),
                            "lly": dbu_float_to_int(lly, dbu),
                            "urx": dbu_float_to_int(urx, dbu),
                            "ury": dbu_float_to_int(ury, dbu),
                        },
                    }
                )
            routing_boxes = [box for box in boxes if self.layer_id.get(box["layer"]) is not None and self.layers[self.layer_id[box["layer"]]].get("type") == "ROUTING"]
            cut_boxes = [box for box in boxes if self.layer_id.get(box["layer"]) is not None and self.layers[self.layer_id[box["layer"]]].get("type") == "CUT"]
            routing_boxes.sort(key=lambda box: self.layer_id.get(box["layer"], 0))
            bottom_box = routing_boxes[0] if routing_boxes else boxes[0]
            top_box = routing_boxes[-1] if routing_boxes else boxes[-1]
            cut_box = cut_boxes[0] if cut_boxes else boxes[min(1, len(boxes) - 1)]
            bottom = bottom_box["box"]
            cut = cut_box["box"]
            top = top_box["box"]
            self.vias[name] = {
                "id": len(self.vias),
                "name": name,
                "bottom_layer": bottom_box["layer"],
                "cut_layer": cut_box["layer"],
                "top_layer": top_box["layer"],
                "bottom": bottom,
                "cut": cut,
                "top": top,
                "row": 1,
                "col": 1,
                "bottom_direction": "C",
                "top_direction": "C",
            }

    def _parse_macros(self, text: str, dbu: int) -> None:
        for match in re.finditer(r"(?ms)^\s*MACRO\s+(\S+)\s*(.*?)^\s*END\s+\1\b", text):
            name, body = match.group(1), match.group(2)
            size = re.search(r"\bSIZE\s+([.\d]+)\s+BY\s+([.\d]+)\s*;", body)
            if not size:
                continue
            pins: dict[str, str] = {}
            for pin_match in re.finditer(r"(?ms)^\s*PIN\s+(\S+)\s*(.*?)^\s*END\s+\1\b", body):
                pin_name, pin_body = pin_match.group(1), pin_match.group(2)
                direction = re.search(r"\bDIRECTION\s+(\S+)\s*;", pin_body)
                if direction:
                    pins[pin_name] = direction.group(1).upper()
            self.cells[name] = Cell(
                id=-1,
                name=name,
                width=dbu_float_to_int(size.group(1), dbu),
                height=dbu_float_to_int(size.group(2), dbu),
                pins=pins,
            )


class LibertyParser:
    def __init__(self, lib_paths: Iterable[Path]):
        self.lib_paths = list(lib_paths)
        self.pin_dirs: dict[str, dict[str, str]] = {}
        self.timing_arcs: dict[str, set[tuple[str, str]]] = {}
        self.leakage: dict[str, float] = {}
        self.nominal_voltage: float = 0.0

    def parse(self) -> "LibertyParser":
        for path in self.lib_paths:
            if path.exists():
                self._parse_file(path)
        return self

    def _parse_file(self, path: Path) -> None:
        text = read_text(path)
        if not self.nominal_voltage:
            voltage = re.search(r"\bnom_voltage\s*:\s*([-+eE.\d]+)\s*;", text)
            if voltage:
                self.nominal_voltage = float(voltage.group(1))
        pos = 0
        while True:
            match = re.search(r"\bcell\s*\(\s*([^)]+)\s*\)\s*\{", text[pos:])
            if not match:
                break
            name = match.group(1).strip()
            start = pos + match.end()
            end = find_matching_brace(text, start - 1)
            if end < 0:
                break
            body = text[start:end]
            pin_dirs: dict[str, str] = {}
            timing_arcs: set[tuple[str, str]] = set()
            pin_pos = 0
            while True:
                pin_match = re.search(r"\bpin\s*\(\s*([^)]+)\s*\)\s*\{", body[pin_pos:])
                if not pin_match:
                    break
                pin_name = pin_match.group(1).strip()
                pin_start = pin_pos + pin_match.end()
                pin_end = find_matching_brace(body, pin_start - 1)
                if pin_end < 0:
                    break
                pin_body = body[pin_start:pin_end]
                direction = re.search(r"\bdirection\s*:\s*(\w+)\s*;", pin_body)
                if direction:
                    pin_dirs[pin_name] = direction.group(1).upper()
                if direction and direction.group(1).upper() == "OUTPUT":
                    for related in re.findall(r"\brelated_pin\s*:\s*\"?([^\";]+)\"?\s*;", pin_body):
                        for related_pin in re.split(r"[,\s]+", related.strip()):
                            if related_pin:
                                timing_arcs.add((related_pin, pin_name))
                pin_pos = pin_end + 1
            leakage = re.search(r"\bcell_leakage_power\s*:\s*([-+eE.\d]+)\s*;", body)
            if pin_dirs:
                self.pin_dirs[name] = pin_dirs
            if timing_arcs:
                self.timing_arcs[name] = timing_arcs
            if leakage:
                self.leakage[name] = float(leakage.group(1))
            pos = end + 1


def find_matching_brace(text: str, open_index: int) -> int:
    depth = 0
    for idx in range(open_index, len(text)):
        ch = text[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return idx
    return -1


class DefParser:
    def __init__(self, def_path: Path, lef: LefParser, liberty: LibertyParser, verilog_ports: dict[str, str]):
        self.def_path = def_path
        self.lef = lef
        self.liberty = liberty
        self.verilog_ports = verilog_ports
        self.layer_id = lef.layer_id

    def parse(self) -> DefDesign:
        text = strip_def_comments(read_text(self.def_path))
        dbu = self._parse_dbu(text)
        name = self._parse_design_name(text)
        diearea = self._parse_diearea(text)
        gcell_x = self._parse_gcell(text, "X")
        gcell_y = self._parse_gcell(text, "Y")
        ports = self._parse_ports(text)
        instances = self._parse_components(text)
        nets = self._parse_nets(text)
        return DefDesign(name, dbu, diearea, gcell_x, gcell_y, self.lef.vias, instances, ports, nets)

    def _parse_dbu(self, text: str) -> int:
        match = re.search(r"UNITS\s+DISTANCE\s+MICRONS\s+(\d+)\s*;", text)
        return int(match.group(1)) if match else 1000

    def _parse_design_name(self, text: str) -> str:
        match = re.search(r"DESIGN\s+(\S+)\s*;", text)
        return match.group(1) if match else self.def_path.stem

    def _parse_diearea(self, text: str) -> tuple[int, int, int, int]:
        match = re.search(r"DIEAREA\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*;", text)
        if not match:
            return (0, 0, 0, 0)
        return tuple(int(v) for v in match.groups())  # type: ignore[return-value]

    def _parse_gcell(self, text: str, axis: str) -> list[int]:
        match = re.search(rf"GCELLGRID\s+{axis}\s+(-?\d+)\s+DO\s+(\d+)\s+STEP\s+(\d+)\s*;", text)
        if not match:
            return []
        start, count, step = (int(v) for v in match.groups())
        return [start + i * step for i in range(count)]

    def _section(self, text: str, name: str) -> str:
        match = re.search(rf"(?ms)^{name}\s+\d+\s*;\s*(.*?)^END\s+{name}\b", text)
        return match.group(1) if match else ""

    def _parse_components(self, text: str) -> list[Instance]:
        section = self._section(text, "COMPONENTS")
        instances: list[Instance] = []
        for item in split_def_items(section):
            match = re.match(r"-\s+(\S+)\s+(\S+)\s+(.*)", item, re.S)
            if not match:
                continue
            name, cell_name, rest = match.groups()
            if not is_signal_instance(name, cell_name):
                continue
            cell = self.lef.cells.get(cell_name, Cell(-1, cell_name, 0, 0))
            loc = re.search(r"\+\s+(?:PLACED|FIXED|COVER)\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)", rest)
            if not loc:
                continue
            inst = Instance(
                id=len(instances),
                name=name,
                cell=cell_name,
                cell_id=cell.id,
                llx=int(loc.group(1)),
                lly=int(loc.group(2)),
                width=cell.width,
                height=cell.height,
            )
            instances.append(inst)
        return instances

    def _parse_ports(self, text: str) -> dict[str, Port]:
        ports: dict[str, Port] = {}
        section = self._section(text, "PINS")
        for item in split_def_items(section):
            match = re.match(r"-\s+(\S+)\s+(.*)", item, re.S)
            if not match:
                continue
            name, rest = match.groups()
            direction = self.verilog_ports.get(name, "INOUT")
            dir_match = re.search(r"\+\s+DIRECTION\s+(\S+)", rest)
            if dir_match:
                direction = dir_match.group(1).upper()
            loc = re.search(r"\+\s+(?:PLACED|FIXED)\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)", rest)
            ports[name] = Port(
                name=name,
                direction=direction.upper(),
                x=int(loc.group(1)) if loc else 0,
                y=int(loc.group(2)) if loc else 0,
            )
        return ports

    def _parse_nets(self, text: str) -> list[Net]:
        section = self._section(text, "NETS")
        nets: list[Net] = []
        for item in split_def_items(section):
            match = re.match(r"-\s+(\S+)\s+(.*)", item, re.S)
            if not match:
                continue
            net_name, rest = match.groups()
            conn_text = re.split(
                r"\s\+\s(?:ROUTED|FIXED|COVER|NOSHIELD|SOURCE|USE|NONDEFAULTRULE|ESTCAP)\b",
                rest,
                maxsplit=1,
            )[0]
            pins = re.findall(r"\(\s+(\S+)\s+(\S+)\s+\)", conn_text)
            segments = self._parse_route_segments(rest)
            nets.append(Net(id=len(nets), name=net_name, pins=pins, segments=segments))
        return nets

    def _parse_route_segments(self, text: str) -> list[Segment]:
        route_text = " ".join(re.findall(r"\+\s+(?:ROUTED|FIXED|COVER|NOSHIELD)\s+(.+?)(?=\s\+\s(?:SOURCE|USE|NONDEFAULTRULE|ESTCAP|ROUTED|FIXED|COVER|NOSHIELD)|$)", text, re.S))
        if not route_text:
            return []
        tokens = re.findall(r"\(|\)|\*|-?\d+|[A-Za-z_.$/\\\[\]<>:][\w.$/\\\[\]<>:!-]*", route_text)
        segments: list[Segment] = []
        layer: str | None = None
        last: tuple[int, int, str] | None = None
        idx = 0
        while idx < len(tokens):
            tok = tokens[idx]
            if tok == "NEW":
                layer = None
                last = None
                idx += 1
                continue
            if tok in self.layer_id:
                layer = tok
                idx += 1
                continue
            if tok == "(" and layer and idx + 4 < len(tokens):
                x_tok, y_tok = tokens[idx + 1], tokens[idx + 2]
                if tokens[idx + 3] != ")":
                    idx += 1
                    continue
                x = last[0] if x_tok == "*" and last else (0 if x_tok == "*" else int(x_tok))
                y = last[1] if y_tok == "*" and last else (0 if y_tok == "*" else int(y_tok))
                point = (x, y, layer)
                if last:
                    segments.append(
                        Segment(
                            x1=last[0],
                            y1=last[1],
                            layer1=self.layer_id[last[2]],
                            x2=point[0],
                            y2=point[1],
                            layer2=self.layer_id[point[2]],
                        )
                    )
                last = point
                idx += 4
                if idx < len(tokens) and tokens[idx] not in {"NEW", "(", ")"} and tokens[idx] not in self.layer_id:
                    via = tokens[idx]
                    via_layers = self._via_layer_pair(via)
                    if via_layers and last:
                        segments.append(
                            Segment(
                                x1=last[0],
                                y1=last[1],
                                layer1=via_layers[0],
                                x2=last[0],
                                y2=last[1],
                                layer2=via_layers[1],
                                via=via,
                            )
                        )
                        layer_name = self._layer_name(via_layers[1])
                        if layer_name:
                            layer = layer_name
                            last = (last[0], last[1], layer)
                    idx += 1
                continue
            idx += 1
        return segments

    def _via_layer_pair(self, via_name: str) -> tuple[int, int] | None:
        via = self.lef.vias.get(via_name)
        if not via:
            # OpenROAD-generated via names often encode metalN_N+1.
            match = re.search(r"via(\d+)_(\d+)", via_name, re.I)
            if match:
                lower = f"metal{match.group(1)}"
                upper = f"metal{match.group(2)}"
                if lower in self.layer_id and upper in self.layer_id:
                    return (self.layer_id[lower], self.layer_id[upper])
            return None
        bottom = via.get("bottom_layer")
        top = via.get("top_layer")
        if bottom in self.layer_id and top in self.layer_id:
            return (self.layer_id[bottom], self.layer_id[top])
        return None

    def _layer_name(self, layer_id: int) -> str | None:
        for name, idx in self.layer_id.items():
            if idx == layer_id:
                return name
        return None


def split_def_items(section: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        current.append(line)
        if line.endswith(";"):
            item = " ".join(current).rstrip(";").strip()
            if item.startswith("-"):
                items.append(item)
            current = []
    return items


def parse_verilog_ports(verilog_path: Path) -> dict[str, str]:
    if not verilog_path.exists():
        return {}
    text = read_text(verilog_path)
    ports: dict[str, str] = {}
    for direction, names in re.findall(r"\b(input|output|inout)\b\s*(?:\[[^\]]+\])?\s*([^;]+);", text):
        for name in re.split(r",\s*", names.strip()):
            clean = name.strip().strip("\\").split()[0] if name.strip() else ""
            if clean:
                ports[clean] = direction.upper()
    return ports


def parse_spef(spef_path: Path) -> tuple[dict[str, float], dict[str, float]]:
    caps: dict[str, float] = {}
    ress: dict[str, float] = {}
    if not spef_path.exists():
        return caps, ress
    text = read_text(spef_path)
    name_map: dict[str, str] = {}
    in_name_map = False
    for line in text.splitlines():
        if line.startswith("*NAME_MAP"):
            in_name_map = True
            continue
        if in_name_map and line.startswith("*") and not re.match(r"\*\d+\s+", line):
            in_name_map = False
        if in_name_map:
            match = re.match(r"\*(\d+)\s+(.+)", line)
            if match:
                name_map[f"*{match.group(1)}"] = match.group(2).strip()
    for block in re.split(r"\n(?=\*D_NET\s+)", text):
        header = re.match(r"\*D_NET\s+(\S+)\s+([-+eE.\d]+)", block)
        if not header:
            continue
        raw_name, cap = header.group(1), float(header.group(2))
        name = name_map.get(raw_name, raw_name)
        caps[name] = cap
        total_r = 0.0
        for res in re.finditer(r"^\s*\d+\s+\S+\s+\S+\s+([-+eE.\d]+)\s*$", block, re.M):
            total_r += float(res.group(1))
        ress[name] = total_r
    return caps, ress


def seconds_to_ns(value: Any) -> float:
    try:
        return float(value) * 1e9
    except Exception:
        return 0.0


def seconds_to_ns_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value) * 1e9
    except Exception:
        return None


def farads_to_pf(value: Any) -> float:
    try:
        return float(value) * 1e12
    except Exception:
        return 0.0


def farads_to_pf_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value) * 1e12
    except Exception:
        return None


def parse_instance_power(path: Path) -> dict[str, dict[str, float]]:
    powers: dict[str, dict[str, float]] = {}
    if not path.exists():
        return powers
    line_re = re.compile(
        r"^\s*([-+eE.\d]+)\s+([-+eE.\d]+)\s+([-+eE.\d]+)\s+([-+eE.\d]+)\s+(.+?)\s*$"
    )
    for line in read_text(path).splitlines():
        match = line_re.match(line)
        if not match:
            continue
        internal, switching, leakage, total, inst = match.groups()
        powers[normalize_design_name(inst.strip())] = {
            "internal": float(internal),
            "switching": float(switching),
            "leakage": float(leakage),
            "total": float(total),
        }
    return powers


def parse_path_report(
    path: Path,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, float]], dict[str, dict[str, Any]], dict[tuple[str, str], float]]:
    if not path.exists():
        return [], {}, {}, {}
    try:
        data = json.loads(read_text(path))
    except json.JSONDecodeError:
        return [], {}, {}, {}
    checks = data.get("checks", []) if isinstance(data, dict) else []
    net_accum: dict[str, dict[str, list[float]]] = {}
    pin_metrics: dict[str, dict[str, Any]] = {}
    cell_arc_accum: dict[tuple[str, str], list[float]] = {}
    for check in checks:
        for points in (list(check.get("source_path", [])), list(check.get("target_clock_path", []))):
            prev_by_net: dict[str, dict[str, Any]] = {}
            prev_point: dict[str, Any] | None = None
            for point in points:
                pin = normalize_design_name(point.get("pin", ""))
                if pin:
                    pin_metrics[pin] = {
                        "capacitance": farads_to_pf(point.get("capacitance", 0)),
                        "slew": seconds_to_ns(point.get("slew", 0)),
                        "arrival": seconds_to_ns(point.get("arrival", 0)),
                        "required": SENTINEL_TIME,
                        "net_load_delay": 0.0,
                        "is_endpoint": pin_metrics.get(pin, {}).get("is_endpoint", False),
                    }
                if prev_point is not None and "arrival" in point and "arrival" in prev_point:
                    prev_inst = normalize_design_name(prev_point.get("instance", ""))
                    inst = normalize_design_name(point.get("instance", ""))
                    prev_pin = normalize_design_name(prev_point.get("pin", ""))
                    if inst and inst == prev_inst and prev_pin and pin and prev_pin != pin:
                        delay = max(0.0, seconds_to_ns(point.get("arrival", 0)) - seconds_to_ns(prev_point.get("arrival", 0)))
                        cell_arc_accum.setdefault((prev_pin, pin), []).append(delay)
                net = normalize_design_name(point.get("net") or "")
                if net:
                    entry = net_accum.setdefault(net, {"capacitance": [], "slew": [], "delay": []})
                    if "capacitance" in point:
                        entry["capacitance"].append(farads_to_pf(point.get("capacitance", 0)))
                    if "slew" in point:
                        entry["slew"].append(seconds_to_ns(point.get("slew", 0)))
                    prev = prev_by_net.get(net)
                    if prev is not None and "arrival" in point and "arrival" in prev:
                        delay = max(0.0, seconds_to_ns(point.get("arrival", 0)) - seconds_to_ns(prev.get("arrival", 0)))
                        entry["delay"].append(delay)
                        if pin:
                            pin_metrics.setdefault(pin, {})["net_load_delay"] = delay
                    prev_by_net[net] = point
                prev_point = point
        slack = seconds_to_ns(check.get("slack", 0))
        required = seconds_to_ns(check.get("required_time", 0))
        endpoint = normalize_design_name(check.get("endpoint", ""))
        if endpoint in pin_metrics and required:
            pin_metrics[endpoint]["required"] = required
            pin_metrics[endpoint]["is_endpoint"] = True
        for point in check.get("source_path", []):
            pin = normalize_design_name(point.get("pin", ""))
            if pin in pin_metrics and pin_metrics[pin].get("required", SENTINEL_TIME) == SENTINEL_TIME:
                arrival = pin_metrics[pin].get("arrival", 0)
                pin_metrics[pin]["required"] = arrival + slack if slack else SENTINEL_TIME
    net_metrics = {
        net: {
            "capacitance": max(values["capacitance"], default=0.0),
            "slew": max(values["slew"], default=0.0),
            "delay": max(values["delay"], default=0.0),
        }
        for net, values in net_accum.items()
    }
    cell_arc_delays = {arc: max(delays) for arc, delays in cell_arc_accum.items()}
    return checks, net_metrics, pin_metrics, cell_arc_delays


def parse_marker_report(path: Path, source: str) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    if not path.exists():
        return markers
    text = read_text(path)
    for line_no, line in enumerate(text.splitlines(), start=1):
        nums = [int(float(value)) for value in re.findall(r"[-+]?\d+(?:\.\d+)?", line)]
        if len(nums) < 4:
            continue
        x1, y1, x2, y2 = nums[:4]
        llx, urx = sorted((x1, x2))
        lly, ury = sorted((y1, y2))
        if llx == urx and lly == ury:
            continue
        markers.append(
            {
                "bbox": (llx, lly, urx, ury),
                "type": line.strip().split()[0] if line.strip() else source,
                "source": source,
                "line": line_no,
            }
        )
    return markers


def platform_nominal_voltage(platform: str) -> float:
    if platform == "asap7":
        return 0.77
    return 1.1


def parse_ir_points(path: Path, nominal_vdd: float) -> list[dict[str, float]]:
    points: list[dict[str, float]] = []
    if not path.exists():
        return points
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as f:
      for row in csv.DictReader(f):
        try:
            x = float(row["X location"])
            y = float(row["Y location"])
            voltage = float(row["Voltage"])
        except (KeyError, ValueError):
            continue
        terminal = row.get("Terminal", "")
        nominal = 0.0 if terminal.upper().startswith("VS") or terminal.upper().startswith("GN") else nominal_vdd
        drop = abs(nominal - voltage)
        points.append({"x_um": x, "y_um": y, "voltage": voltage, "drop": drop})
    return points


def parse_net_power(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as f:
        for row in csv.DictReader(f):
            name = normalize_design_name(row.get("net", ""))
            if not name:
                continue
            try:
                power = float(row.get("switching_power_w", ""))
            except ValueError:
                power = None
            rows[name] = {
                "switching_power": power,
                "source": row.get("source", ""),
                "activity": float(row["activity"]) if row.get("activity") else None,
                "capacitance_pf": float(row["capacitance_pf"]) if row.get("capacitance_pf") else None,
            }
    return rows


def load_ml_reports(reports_dir: Path, stage: str, platform: str) -> ReportData:
    is_preroute_stage = stage in {"place", "cts"}
    has_route_reports = stage == "route"
    power = parse_instance_power(reports_dir / "instance_power.rpt") if has_route_reports else {}
    path_name = "grt_path_report.json" if is_preroute_stage else "path_report.json"
    checks, net_metrics, pin_metrics, cell_arc_delays = parse_path_report(reports_dir / path_name)
    congestion_markers = parse_marker_report(reports_dir / "congestion.rpt", "congestion")
    drc_markers = parse_marker_report(reports_dir / "drc.rpt", "drc") if has_route_reports else []
    ir_points: list[dict[str, float]] = []
    net_power: dict[str, dict[str, Any]] = {}
    if has_route_reports:
        nominal_vdd = platform_nominal_voltage(platform)
        ir_points = parse_ir_points(reports_dir / "ir_drop_VDD.csv", nominal_vdd)
        if not ir_points:
            ir_points = parse_ir_points(reports_dir / "ir_drop_VSS.csv", nominal_vdd)
        net_power = parse_net_power(reports_dir / "net_power.csv")
    return ReportData(
        instance_power=power,
        net_metrics=net_metrics,
        pin_metrics=pin_metrics,
        cell_arc_delays=cell_arc_delays,
        path_checks=checks,
        drc_markers=drc_markers,
        congestion_markers=congestion_markers,
        drc_available=has_route_reports and (reports_dir / "drc.rpt").exists(),
        congestion_available=(reports_dir / "congestion.rpt").exists(),
        ir_points=ir_points,
        net_power=net_power,
        reports_dir=reports_dir,
    )


class DatasetWriter:
    def __init__(
        self,
        design: DefDesign,
        lef: LefParser,
        liberty: LibertyParser,
        out_dir: Path,
        reports: ReportData | None = None,
        patch_size_dbu: int = 3330,
    ):
        self.design = design
        self.lef = lef
        self.liberty = liberty
        self.out_dir = out_dir
        self.reports = reports or ReportData()
        self.instances_by_name = {inst.name: inst for inst in design.instances}
        self.nets_by_name = {net.name: net for net in design.nets}
        self.nets_by_name.update({normalize_design_name(net.name): net for net in design.nets})
        self.net_by_id = {net.id: net for net in design.nets}
        self.cell_by_name = lef.cells
        self.patch_index = PatchIndex(design, patch_size_dbu=patch_size_dbu)

    def write(self) -> None:
        self._prepare_dirs()
        self._write_tech()
        self._write_instances()
        self._write_nets()
        self._write_patches()
        self._write_wire_graph()
        self._write_instance_graph()
        self._write_wire_paths()
        self._write_reports()

    def _prepare_dirs(self) -> None:
        if self.out_dir.exists():
            shutil.rmtree(self.out_dir)
        for name in ["instances", "nets", "patchs", "wire_graph", "wire_paths", "instance_graph", "tech"]:
            (self.out_dir / name).mkdir(parents=True, exist_ok=True)

    def _write_tech(self) -> None:
        cells = [
            {"id": cell.id, "name": cell.name, "width": cell.width, "height": cell.height}
            for cell in sorted(self.lef.cells.values(), key=lambda c: c.id)
        ]
        write_json(self.out_dir / "tech" / "cells.json", {"cell_num": len(cells), "cells": cells})
        layers = [{"id": idx, "name": layer["name"]} for idx, layer in reversed(list(enumerate(self.lef.layers)))]
        vias = sorted(self.lef.vias.values(), key=lambda v: v["id"])
        write_json(
            self.out_dir / "tech" / "tech.json",
            {"layer_num": len(layers), "layers": layers, "via_num": len(vias), "vias": vias},
        )

    def _write_instances(self) -> None:
        instances = [
            {
                "id": inst.id,
                "cell_id": inst.cell_id,
                "name": inst.name,
                "cx": inst.cx,
                "cy": inst.cy,
                "width": inst.width,
                "height": inst.height,
                "llx": inst.llx,
                "lly": inst.lly,
                "urx": inst.urx,
                "ury": inst.ury,
            }
            for inst in self.design.instances
        ]
        write_json(self.out_dir / "instances" / "instances.json", {"instance_num": len(instances), "instances": instances})

    def _write_nets(self) -> None:
        for net in self.design.nets:
            data = self._net_json(net)
            write_json(self.out_dir / "nets" / f"net_{net.id}.json", data)

    def _net_json(self, net: Net) -> dict[str, Any]:
        pin_entries = []
        pin_points = []
        for idx, (inst_name, pin_name) in enumerate(net.pins):
            is_driver = self._is_driver(inst_name, pin_name)
            pin_entries.append({"id": idx, "i": inst_name, "p": pin_name, "driver": 1 if is_driver else 0})
            point = self._pin_point(inst_name)
            if point:
                pin_points.append(point)
        bbox = self._segment_bbox(net.segments) or self._points_bbox(pin_points)
        wire_len = sum(seg.length for seg in net.segments)
        via_num = sum(1 for seg in net.segments if seg.layer1 != seg.layer2)
        width = max(0, bbox[2] - bbox[0]) if bbox else 0
        height = max(0, bbox[3] - bbox[1]) if bbox else 0
        layer_ratio = self._layer_ratio(net.segments, wire_len)
        place_feature = self._place_feature(pin_points)
        timing = self.reports.net_metrics.get(net.name, {})
        net_power = self._net_power(net)
        net_drc = self._marker_count(bbox, self.reports.drc_markers, self.reports.drc_available)
        net_congestion = self._marker_count(bbox, self.reports.congestion_markers, self.reports.congestion_available)
        net_r = net.resistance if net.has_spef else None
        net_c = net.capacitance if net.has_spef else timing.get("capacitance")
        wires = []
        for wire_id, seg in enumerate(net.segments):
            seg_bbox = self._segment_bbox([seg])
            patch_ids = self.patch_index.patches_for_segment(seg)
            path = self._path_object(seg)
            seg_count = max(1, len(net.segments))
            seg_r = net.resistance / seg_count if net.has_spef and seg_count else None
            seg_c = net.capacitance / seg_count if net.has_spef and seg_count else None
            seg_drc = self._marker_count(seg_bbox, self.reports.drc_markers, self.reports.drc_available)
            seg_congestion = self._marker_count(seg_bbox, self.reports.congestion_markers, self.reports.congestion_available)
            wires.append(
                {
                    "id": wire_id,
                    "feature": {
                        "wire_width": 0 if seg.layer1 != seg.layer2 else self._layer_width(seg.layer1),
                        "wire_len": seg.length,
                        "wire_density": self.patch_index.wire_density(seg),
                        "drc_num": seg_drc,
                        "R": seg_r,
                        "C": seg_c,
                        "power": net_power / seg_count if net_power is not None else None,
                        "delay": timing.get("delay", 0) / seg_count,
                        "slew": timing.get("slew", 0),
                        "congestion": seg_congestion,
                        "drc_type": self._marker_types(seg_bbox, self.reports.drc_markers, self.reports.drc_available),
                    },
                    "wire": path,
                    "path_num": 1,
                    "paths": [path],
                    "patch_num": len(patch_ids),
                    "patchs": patch_ids,
                }
            )
        routing_graph = self._routing_graph(net.segments)
        return {
            "id": net.id,
            "name": net.name,
            "feature": {
                "llx": bbox[0] if bbox else 0,
                "lly": bbox[1] if bbox else 0,
                "urx": bbox[2] if bbox else 0,
                "ury": bbox[3] if bbox else 0,
                "wire_len": wire_len,
                "via_num": via_num,
                "drc_num": net_drc,
                "drc_type": self._marker_types(bbox, self.reports.drc_markers, self.reports.drc_available),
                "R": net_r,
                "C": net_c,
                "power": net_power,
                "power_source": self._net_power_source(net),
                "delay": timing.get("delay", 0),
                "slew": timing.get("slew", 0),
                "congestion": net_congestion,
                "aspect_ratio": int(round(width / height)) if height else 0,
                "width": width,
                "height": height,
                "area": width * height,
                "volume": width * height * max(1, len([r for r in layer_ratio if r > 0])),
                "layer_ratio": layer_ratio,
                "place_feature": place_feature,
            },
            "pin_num": len(pin_entries),
            "pins": pin_entries,
            "wire_num": len(wires),
            "wires": wires,
            "routing_graph": routing_graph,
        }

    def _net_power(self, net: Net) -> float | None:
        entry = self.reports.net_power.get(normalize_design_name(net.name), self.reports.net_power.get(net.name, {}))
        return entry.get("switching_power")

    def _net_power_source(self, net: Net) -> str | None:
        entry = self.reports.net_power.get(normalize_design_name(net.name), self.reports.net_power.get(net.name, {}))
        return entry.get("source")

    def _marker_count(self, bbox: tuple[int, int, int, int] | None, markers: list[dict[str, Any]], available: bool) -> int | None:
        if not available:
            return None
        if not bbox:
            return 0
        return sum(1 for marker in markers if self._bbox_intersects(bbox, marker["bbox"]))

    def _marker_types(self, bbox: tuple[int, int, int, int] | None, markers: list[dict[str, Any]], available: bool) -> list[str] | None:
        if not available:
            return None
        if not bbox:
            return []
        return sorted({str(marker.get("type", "")) for marker in markers if self._bbox_intersects(bbox, marker["bbox"])})

    def _bbox_intersects(self, a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
        return max(a[0], b[0]) <= min(a[2], b[2]) and max(a[1], b[1]) <= min(a[3], b[3])

    def _ir_drop_for_patch(self, patch: "Patch") -> float | None:
        if not self.reports.ir_points:
            return None
        drops = [
            point["drop"]
            for point in self.reports.ir_points
            if patch.llx <= point["x_um"] * self.design.dbu <= patch.urx
            and patch.lly <= point["y_um"] * self.design.dbu <= patch.ury
        ]
        return max(drops) if drops else None

    def _is_driver(self, inst_name: str, pin_name: str) -> bool:
        if inst_name == "PIN":
            port = self.design.ports.get(pin_name)
            return bool(port and port.direction == "INPUT")
        inst = self.instances_by_name.get(inst_name)
        if not inst:
            return False
        direction = self.liberty.pin_dirs.get(inst.cell, {}).get(pin_name) or self.cell_by_name.get(inst.cell, Cell(-1, "", 0, 0)).pins.get(pin_name)
        return direction == "OUTPUT"

    def _pin_point(self, inst_name: str) -> tuple[int, int] | None:
        if inst_name == "PIN":
            return None
        inst = self.instances_by_name.get(inst_name)
        if not inst:
            return None
        return (inst.cx, inst.cy)

    def _segment_bbox(self, segments: list[Segment]) -> tuple[int, int, int, int] | None:
        if not segments:
            return None
        xs = []
        ys = []
        for seg in segments:
            xs.extend([seg.x1, seg.x2])
            ys.extend([seg.y1, seg.y2])
        return (min(xs), min(ys), max(xs), max(ys))

    def _points_bbox(self, points: list[tuple[int, int]]) -> tuple[int, int, int, int] | None:
        if not points:
            return None
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return (min(xs), min(ys), max(xs), max(ys))

    def _place_feature(self, points: list[tuple[int, int]]) -> dict[str, Any]:
        bbox = self._points_bbox(points)
        if not bbox:
            return {"pin_num": 0, "aspect_ratio": 0, "width": 0, "height": 0, "area": 0, "l_ness": 0, "hpwl": 0, "rsmt": 0}
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        hpwl = width + height
        return {
            "pin_num": len(points),
            "aspect_ratio": int(round(width / height)) if height else 0,
            "width": width,
            "height": height,
            "area": width * height,
            "l_ness": 1 if len(points) <= 2 else 0,
            "hpwl": hpwl,
            "rsmt": hpwl,
        }

    def _layer_ratio(self, segments: list[Segment], total_len: int) -> list[float]:
        ratios = [0.0 for _ in self.lef.layers]
        if total_len <= 0:
            return [0 for _ in ratios]
        for seg in segments:
            if seg.layer1 == seg.layer2 and 0 <= seg.layer1 < len(ratios):
                ratios[seg.layer1] += seg.length / total_len
        return ratios

    def _layer_width(self, layer_id: int) -> int:
        if 0 <= layer_id < len(self.lef.layers):
            return int(self.lef.layers[layer_id].get("width", 0))
        return 0

    def _path_object(self, seg: Segment) -> dict[str, Any]:
        path = {
            "id1": self._point_id(seg.x1, seg.y1, seg.layer1),
            "x1": seg.x1,
            "y1": seg.y1,
            "real_x1": seg.x1,
            "real_y1": seg.y1,
            "r1": self.patch_index.row(seg.y1),
            "c1": self.patch_index.col(seg.x1),
            "l1": seg.layer1,
            "p1": -1,
            "id2": self._point_id(seg.x2, seg.y2, seg.layer2),
            "x2": seg.x2,
            "y2": seg.y2,
            "real_x2": seg.x2,
            "real_y2": seg.y2,
            "r2": self.patch_index.row(seg.y2),
            "c2": self.patch_index.col(seg.x2),
            "l2": seg.layer2,
            "p2": -1,
        }
        if seg.via:
            path["via"] = self.lef.vias.get(seg.via, {}).get("id", -1)
        return path

    def _point_id(self, x: int, y: int, layer: int) -> int:
        return zlib.crc32(f"{x}:{y}:{layer}".encode("ascii")) & 0x7FFFFFFF

    def _routing_graph(self, segments: list[Segment]) -> dict[str, Any]:
        vertex_ids: dict[tuple[int, int, int], int] = {}
        vertices = []
        edges = []
        for seg in segments:
            for point in [(seg.x1, seg.y1, seg.layer1), (seg.x2, seg.y2, seg.layer2)]:
                if point not in vertex_ids:
                    vertex_ids[point] = len(vertices)
                    vertices.append({"id": vertex_ids[point], "is_pin": 0, "is_driver_pin": 0, "x": point[0], "y": point[1], "layer_id": point[2]})
            source_id = vertex_ids[(seg.x1, seg.y1, seg.layer1)]
            target_id = vertex_ids[(seg.x2, seg.y2, seg.layer2)]
            edges.append(
                {
                    "source_id": source_id,
                    "target_id": target_id,
                    "path": [
                        {"x": seg.x1, "y": seg.y1, "layer_id": seg.layer1},
                        {"x": seg.x2, "y": seg.y2, "layer_id": seg.layer2},
                    ],
                }
            )
        return {"vertices": vertices, "edges": edges}

    def _write_patches(self) -> None:
        patch_nets: dict[int, list[dict[str, int]]] = {patch.id: [] for patch in self.patch_index.patches}
        patch_wires: dict[int, list[tuple[Net, int, Segment]]] = {patch.id: [] for patch in self.patch_index.patches}
        for net in self.design.nets:
            for seg_id, seg in enumerate(net.segments):
                for patch_id in self.patch_index.patches_for_segment(seg):
                    patch_wires[patch_id].append((net, seg_id, seg))
            pin_points = [point for inst_name, _pin_name in net.pins if (point := self._pin_point(inst_name))]
            net_bbox = self._segment_bbox(net.segments) or self._points_bbox(pin_points)
            if net_bbox:
                for patch_id in self.patch_index.patches_for_bbox(net_bbox):
                    patch = self.patch_index.by_id[patch_id]
                    clipped = patch.clip_bbox(net_bbox)
                    if clipped:
                        patch_nets[patch_id].append({"id": net.id, "llx": clipped[0], "lly": clipped[1], "urx": clipped[2], "ury": clipped[3]})
        for patch in self.patch_index.patches:
            insts_in_patch = [
                inst for inst in self.design.instances
                if max(0, min(inst.urx, patch.urx) - max(inst.llx, patch.llx))
                and max(0, min(inst.ury, patch.ury) - max(inst.lly, patch.lly))
            ]
            inst_area = sum(
                max(0, min(inst.urx, patch.urx) - max(inst.llx, patch.llx))
                * max(0, min(inst.ury, patch.ury) - max(inst.lly, patch.lly))
                for inst in insts_in_patch
            )
            pin_count = 0
            rudy = 0.0
            timing = 0.0
            covered_net_ids = {item["id"] for item in patch_nets[patch.id]}
            for net_id in covered_net_ids:
                net = self.net_by_id.get(net_id)
                if not net:
                    continue
                pin_points = [point for inst_name, _pin_name in net.pins if (point := self._pin_point(inst_name))]
                net_bbox = self._segment_bbox(net.segments) or self._points_bbox(pin_points)
                if net_bbox:
                    width = max(0, net_bbox[2] - net_bbox[0])
                    height = max(0, net_bbox[3] - net_bbox[1])
                    denom = max(1, width * height)
                    rudy += (width + height) / denom
                timing = max(timing, self.reports.net_metrics.get(net.name, {}).get("delay", 0.0))
                for inst_name, _pin_name in net.pins:
                    point = self._pin_point(inst_name)
                    if point and patch.llx <= point[0] <= patch.urx and patch.lly <= point[1] <= patch.ury:
                        pin_count += 1
            patch_power = (
                sum(
                    self.reports.instance_power.get(normalize_design_name(inst.name), {}).get("total", 0.0)
                    for inst in insts_in_patch
                )
                if self.reports.instance_power
                else None
            )
            egr_congestion = sum(seg.length for _net, _seg_id, seg in patch_wires[patch.id]) / patch.area if patch.area else 0
            macro_margin = None
            patch_bbox = (patch.llx, patch.lly, patch.urx, patch.ury)
            patch_drc = self._marker_count(patch_bbox, self.reports.drc_markers, self.reports.drc_available)
            patch_congestion = self._marker_count(patch_bbox, self.reports.congestion_markers, self.reports.congestion_available)
            patch_ir_drop = self._ir_drop_for_patch(patch)
            wires_by_layer: dict[int, list[tuple[Net, int, Segment]]] = {}
            for item in patch_wires[patch.id]:
                wires_by_layer.setdefault(item[2].layer1, []).append(item)
            patch_layers = []
            for layer_id in range(len(self.lef.layers)):
                items = wires_by_layer.get(layer_id, [])
                nets = []
                for net, seg_id, seg in items:
                    nets.append(
                        {
                            "id": net.id,
                            "wire_num": 1,
                            "wires": [
                                {
                                    "id": seg_id,
                                    "feature": {"wire_len": seg.length},
                                    "path_num": 1,
                                    "paths": [self._path_object(seg)],
                                }
                            ],
                        }
                    )
                wire_len = sum(item[2].length for item in items)
                patch_layers.append(
                    {
                        "id": layer_id,
                        "feature": {
                            "wire_width": self._layer_width(layer_id),
                            "wire_len": wire_len,
                            "wire_density": wire_len / patch.area if patch.area else 0,
                            "congestion": patch_congestion,
                        },
                        "net_num": len(nets),
                        "nets": nets,
                    }
                )
            data = {
                "id": patch.id,
                "patch_id_row": patch.row,
                "patch_id_col": patch.col,
                "llx": patch.llx,
                "lly": patch.lly,
                "urx": patch.urx,
                "ury": patch.ury,
                "row_min": self.patch_index.row(patch.lly),
                "row_max": self.patch_index.row(patch.ury),
                "col_min": self.patch_index.col(patch.llx),
                "col_max": self.patch_index.col(patch.urx),
                "cell_density": inst_area / patch.area if patch.area else 0,
                "pin_density": pin_count,
                "net_density": len(patch_nets[patch.id]),
                "macro_margin": macro_margin,
                "RUDY_congestion": rudy,
                "EGR_congestion": egr_congestion,
                "drc_num": patch_drc,
                "congestion": patch_congestion,
                "timing": timing,
                "power": patch_power,
                "IR_drop": patch_ir_drop,
                "sub_nets": patch_nets[patch.id],
                "patch_layer": patch_layers,
            }
            write_json(self.out_dir / "patchs" / f"patch_{patch.id}.json", data)

    def _write_wire_graph(self) -> None:
        nodes = []
        edges = []
        node_id: dict[str, int] = {}

        def add_node(name: str, is_pin: bool, is_port: bool, coord: tuple[int, int], cell: str, is_input: bool) -> int:
            if name in node_id:
                return node_id[name]
            idx = len(nodes)
            node_id[name] = idx
            report_name = self._report_pin_name(name)
            pin_metrics = self.reports.pin_metrics.get(report_name, self.reports.pin_metrics.get(name, {}))
            inst_name = name.split(":", 1)[0] if ":" in name else ""
            inst_power = self.reports.instance_power.get(normalize_design_name(inst_name), {})
            slew = pin_metrics.get("slew", SENTINEL_TIME)
            cap = pin_metrics.get("capacitance", 0)
            arrival = pin_metrics.get("arrival", SENTINEL_TIME)
            required = pin_metrics.get("required", SENTINEL_TIME)
            net_load_delay = pin_metrics.get("net_load_delay", 0.0)
            is_endpoint = bool(pin_metrics.get("is_endpoint", False))
            nodes.append(
                {
                    "id": f"node_{idx}",
                    "name": name,
                    "is_pin": is_pin,
                    "is_port": is_port,
                    "node_feature": {
                        "is_input": is_input,
                        "fanout_num": 0,
                        "is_endpoint": is_endpoint,
                        "cell_name": cell,
                        "sizer_cells": None,
                        "node_coord": [coord[0] / self.design.dbu, coord[1] / self.design.dbu],
                        "node_slews": [slew, slew, slew, slew],
                        "node_capacitances": [cap, cap, cap, cap],
                        "node_arrive_times": [arrival, arrival, arrival, arrival],
                        "node_required_times": [required, required, required, required],
                        "node_net_load_delays": [net_load_delay, net_load_delay, net_load_delay, net_load_delay],
                        "node_toggle": None,
                        "node_sp": None,
                        "node_internal_power": inst_power.get("internal", 0),
                        "node_net_power": inst_power.get("switching", 0),
                    },
                }
            )
            return idx

        for net in self.design.nets:
            driver_idx: int | None = None
            pin_node_ids = []
            for inst_name, pin_name in net.pins:
                is_driver = self._is_driver(inst_name, pin_name)
                if inst_name == "PIN":
                    port = self.design.ports.get(pin_name, Port(pin_name))
                    idx = add_node(pin_name, False, True, (port.x, port.y), "NA", port.direction == "INPUT")
                else:
                    inst = self.instances_by_name.get(inst_name)
                    if not inst:
                        continue
                    idx = add_node(f"{inst_name}:{pin_name}", True, False, (inst.cx, inst.cy), inst.cell, not is_driver)
                if is_driver and driver_idx is None:
                    driver_idx = idx
                pin_node_ids.append(idx)
            if driver_idx is not None:
                sink_count = max(1, sum(1 for sink_idx in pin_node_ids if sink_idx != driver_idx))
                driver_internal_power = nodes[driver_idx]["node_feature"].get("node_internal_power", 0.0)
                for sink_idx in pin_node_ids:
                    if sink_idx != driver_idx:
                        delay = self.reports.net_metrics.get(net.name, {}).get("delay", 0.0)
                        edges.append(
                            {
                                "id": f"edge_{len(edges)}",
                                "from_node": driver_idx,
                                "to_node": sink_idx,
                                "is_net_edge": True,
                                "edge_feature": {
                                    "edge_delay": [delay, delay, delay, delay],
                                    "edge_resistance": net.resistance,
                                    "inst_arc_internal_power": driver_internal_power / sink_count,
                                },
                            }
                        )
                        nodes[driver_idx]["node_feature"]["fanout_num"] += 1
        self._add_cell_arcs(nodes, edges, node_id)
        write_json(self.out_dir / "wire_graph" / "timing_wire_graph.json", {"nodes": nodes, "edges": edges})

    def _add_cell_arcs(self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]], node_id: dict[str, int]) -> None:
        pins_by_inst: dict[str, dict[str, int]] = {}
        for name, idx in node_id.items():
            if ":" not in name:
                continue
            inst_name, pin_name = name.split(":", 1)
            pins_by_inst.setdefault(inst_name, {})[pin_name] = idx
        for inst in self.design.instances:
            inst_pins = pins_by_inst.get(inst.name)
            if not inst_pins:
                continue
            arcs = self._cell_timing_arcs(inst.cell)
            valid_arcs = [(from_pin, to_pin) for from_pin, to_pin in arcs if from_pin in inst_pins and to_pin in inst_pins]
            if not valid_arcs:
                continue
            inst_power = self.reports.instance_power.get(normalize_design_name(inst.name), {})
            internal_power = inst_power.get("internal")
            power_per_arc = internal_power / len(valid_arcs) if internal_power is not None else None
            for from_pin, to_pin in valid_arcs:
                from_idx = inst_pins[from_pin]
                to_idx = inst_pins[to_pin]
                from_report = self._report_pin_name(f"{inst.name}:{from_pin}")
                to_report = self._report_pin_name(f"{inst.name}:{to_pin}")
                delay = self.reports.cell_arc_delays.get((from_report, to_report))
                edges.append(
                    {
                        "id": f"edge_{len(edges)}",
                        "from_node": from_idx,
                        "to_node": to_idx,
                        "is_net_edge": False,
                        "edge_feature": {
                            "edge_delay": [delay, delay, delay, delay] if delay is not None else [None, None, None, None],
                            "edge_resistance": 0.0,
                            "inst_arc_internal_power": power_per_arc,
                        },
                    }
                )
                nodes[from_idx]["node_feature"]["fanout_num"] += 1

    def _cell_timing_arcs(self, cell_name: str) -> list[tuple[str, str]]:
        arcs = self.liberty.timing_arcs.get(cell_name)
        if arcs:
            return sorted(arcs)
        pin_dirs = self.liberty.pin_dirs.get(cell_name) or self.cell_by_name.get(cell_name, Cell(-1, "", 0, 0)).pins
        inputs = sorted(pin for pin, direction in pin_dirs.items() if direction in {"INPUT", "INOUT"})
        outputs = sorted(pin for pin, direction in pin_dirs.items() if direction == "OUTPUT")
        return [(input_pin, output_pin) for input_pin in inputs for output_pin in outputs]

    def _report_pin_name(self, graph_name: str) -> str:
        if ":" not in graph_name:
            return graph_name
        inst, pin = graph_name.split(":", 1)
        return normalize_design_name(f"{inst}/{pin}")

    def _write_instance_graph(self) -> None:
        nodes = []
        node_for_inst: dict[str, int] = {}
        for inst in self.design.instances:
            cell_leakage = self.reports.instance_power.get(normalize_design_name(inst.name), {}).get(
                "leakage", self.liberty.leakage.get(inst.cell, 0)
            )
            node_for_inst[inst.name] = len(nodes)
            nodes.append({"id": f"node_{len(nodes)}", "name": inst.name, "leakage_power": cell_leakage})
        edges = []
        for net in self.design.nets:
            drivers = [inst for inst, pin in net.pins if inst != "PIN" and self._is_driver(inst, pin) and inst in node_for_inst]
            sinks = [inst for inst, pin in net.pins if inst != "PIN" and not self._is_driver(inst, pin) and inst in node_for_inst]
            for driver in drivers[:1]:
                for sink in sinks:
                    edges.append({"id": f"edge_{len(edges)}", "from_node": node_for_inst[driver], "to_node": node_for_inst[sink]})
        write_json(self.out_dir / "instance_graph" / "timing_instance_graph.json", {"nodes": nodes, "edges": edges})

    def _write_wire_paths(self) -> None:
        if self.reports.path_checks:
            for path_id, check in enumerate(self.reports.path_checks):
                write_json(self.out_dir / "wire_paths" / f"wire_path_{path_id}.json", self._reported_wire_path_json(check))
            return
        path_id = 0
        for net in self.design.nets:
            driver = next(((inst, pin) for inst, pin in net.pins if self._is_driver(inst, pin)), None)
            if driver is None:
                continue
            sinks = [(inst, pin) for inst, pin in net.pins if (inst, pin) != driver]
            for sink in sinks:
                path = self._wire_path_json(net, driver, sink)
                if path:
                    write_json(self.out_dir / "wire_paths" / f"wire_path_{path_id}.json", path)
                    path_id += 1

    def _reported_wire_path_json(self, check: dict[str, Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        points = check.get("source_path", [])
        if not points and check.get("endpoint"):
            return [
                {
                    "node_0": {
                        "Point": f"{check.get('endpoint')} (NO_TIMING_PATH)",
                        "Capacitance": None,
                        "slew": None,
                        "trans_type": "unconstrained",
                        "endpoint_has_timing_path": False,
                    }
                }
            ]
        prev_point: dict[str, Any] | None = None
        node_idx = 0
        arc_idx = 0
        for point in points:
            pin = point.get("pin", "")
            cell = point.get("cell", "NA")
            result.append(
                {
                    f"node_{node_idx}": {
                        "Point": f"{pin} ({cell})",
                        "Capacitance": farads_to_pf_or_none(point.get("capacitance")),
                        "slew": seconds_to_ns_or_none(point.get("slew")),
                        "trans_type": point.get("transition", "unknown"),
                    }
                }
            )
            if prev_point is not None:
                net = normalize_design_name(point.get("net") or prev_point.get("net") or "")
                incr = max(0.0, seconds_to_ns(point.get("arrival", 0)) - seconds_to_ns(prev_point.get("arrival", 0)))
                result.insert(
                    len(result) - 1,
                    {
                        f"net_arc_{arc_idx}": {
                            "Incr": incr,
                            "edge_0": {
                                "wire_from_node": prev_point.get("pin", ""),
                                "wire_to_node": pin,
                                "wire_R": self._net_resistance(net),
                                "wire_C": self._net_capacitance(net),
                                "from_slew": seconds_to_ns_or_none(prev_point.get("slew")),
                                "to_slew": seconds_to_ns_or_none(point.get("slew")),
                                "wire_delay": incr,
                            },
                        }
                    },
                )
                arc_idx += 1
            prev_point = point
            node_idx += 1
        return result

    def _net_resistance(self, net_name: str) -> float | None:
        net = self.nets_by_name.get(net_name)
        if not net or not net.has_spef:
            return None
        return net.resistance

    def _net_capacitance(self, net_name: str) -> float | None:
        metrics = self.reports.net_metrics.get(net_name, {})
        net = self.nets_by_name.get(net_name)
        if net and net.has_spef:
            return net.capacitance
        return metrics.get("capacitance")

    def _wire_path_json(self, net: Net, driver: tuple[str, str], sink: tuple[str, str]) -> list[dict[str, Any]]:
        driver_point = self._pin_point(driver[0]) or (0, 0)
        sink_point = self._pin_point(sink[0]) or (0, 0)
        driver_cell = self._pin_cell(driver[0])
        sink_cell = self._pin_cell(sink[0])
        net_arc: dict[str, Any] = {"Incr": None}
        seg_count = max(1, len(net.segments))
        for idx, seg in enumerate(net.segments):
            net_arc[f"edge_{idx}"] = {
                "wire_from_node": f"{net.name}:{idx}",
                "wire_to_node": f"{net.name}:{idx + 1}",
                "wire_R": net.resistance / seg_count if net.has_spef else None,
                "wire_C": net.capacitance / seg_count if net.has_spef else None,
                "from_slew": None,
                "to_slew": None,
                "wire_delay": None,
            }
        return [
            {
                "node_0": {
                    "Point": f"{driver[0]}:{driver[1]} ({driver_cell})",
                    "Capacitance": self._net_capacitance(net.name),
                    "slew": None,
                    "trans_type": "rise",
                }
            },
            {"net_arc_0": net_arc},
            {
                "node_1": {
                    "Point": f"{sink[0]}:{sink[1]} ({sink_cell})",
                    "Capacitance": self._net_capacitance(net.name),
                    "slew": None,
                    "trans_type": "rise",
                }
            },
        ]

    def _pin_cell(self, inst_name: str) -> str:
        if inst_name == "PIN":
            return "NA"
        inst = self.instances_by_name.get(inst_name)
        return inst.cell if inst else "NA"

    def _write_reports(self) -> None:
        design_name = self.design.name
        reports_dir = self.reports.reports_dir
        report_src = reports_dir / "path_report.rpt" if reports_dir else None
        power_src = reports_dir / "power.rpt" if reports_dir else None
        inst_power_src = reports_dir / "instance_power.rpt" if reports_dir else None
        for src, dst_name in [
            (report_src, f"{design_name}.rpt"),
            (power_src, f"{design_name}.pwr"),
            (inst_power_src, f"{design_name}_instance.pwr"),
        ]:
            dst = self.out_dir / dst_name
            if src and src.exists():
                shutil.copyfile(src, dst)
            else:
                dst.write_text("", encoding="utf-8")
        voltage = self.liberty.nominal_voltage
        csv_lines = ["Instance Name,Nominal Voltage,Internal Power,Switch Power,Leakage Power,Total Power"]
        json_rows = []
        instance_names = [inst.name for inst in self.design.instances]
        for name in instance_names:
            values = self.reports.instance_power.get(normalize_design_name(name), {})
            internal = values.get("internal", 0.0)
            switching = values.get("switching", 0.0)
            leakage = values.get("leakage", 0.0)
            total = values.get("total", 0.0)
            csv_lines.append(
                f"{name},{voltage},{internal},{switching},{leakage},{total}"
            )
            json_rows.append(
                {
                    "Instance Name": name,
                    "Nominal Voltage": voltage,
                    "Internal Power": internal,
                    "Switch Power": switching,
                    "Leakage Power": leakage,
                    "Total Power": total,
                    "name": name,
                    "internal_power": internal,
                    "switching_power": switching,
                    "leakage_power": leakage,
                    "total_power": total,
                }
            )
        (self.out_dir / f"{design_name}_instance.csv").write_text("\n".join(csv_lines) + "\n", encoding="utf-8")
        write_json(self.out_dir / f"{design_name}_instance.json", {"instances": json_rows})
        pwr_nodes = [
            {
                "id": f"node_{idx}",
                "name": row["name"],
                "internal_power": row.get("internal_power", 0),
                "switching_power": row.get("switching_power", 0),
                "leakage_power": row.get("leakage_power", 0),
                "total_power": row.get("total_power", 0),
            }
            for idx, row in enumerate(json_rows)
        ]
        write_json(self.out_dir / f"{design_name}_pwr_graph.json", {"nodes": pwr_nodes, "edges": []})
        write_json(
            self.out_dir / "timing_power_benchmark.json",
            {
                "design": design_name,
                "instance_power_count": len(json_rows),
                "timing_path_count": len(self.reports.path_checks),
            },
        )


@dataclass
class Patch:
    id: int
    row: int
    col: int
    llx: int
    lly: int
    urx: int
    ury: int

    @property
    def area(self) -> int:
        return max(0, self.urx - self.llx) * max(0, self.ury - self.lly)

    def clip_bbox(self, bbox: tuple[int, int, int, int]) -> tuple[int, int, int, int] | None:
        llx = max(self.llx, bbox[0])
        lly = max(self.lly, bbox[1])
        urx = min(self.urx, bbox[2])
        ury = min(self.ury, bbox[3])
        if llx >= urx or lly >= ury:
            return None
        return (llx, lly, urx, ury)


class PatchIndex:
    def __init__(self, design: DefDesign, patch_size_dbu: int = 3330):
        llx, lly, urx, ury = design.diearea
        self.llx = llx
        self.lly = lly
        self.urx = urx
        self.ury = ury
        self.width = max(1, patch_size_dbu)
        self.height = max(1, patch_size_dbu)
        self.cols = max(1, math.ceil((urx - llx) / self.width))
        self.rows = max(1, math.ceil((ury - lly) / self.height))
        self.patches: list[Patch] = []
        for row in range(self.rows):
            for col in range(self.cols):
                pid = row * self.cols + col
                p_llx = llx + col * self.width
                p_lly = lly + row * self.height
                self.patches.append(Patch(pid, row, col, p_llx, p_lly, min(urx, p_llx + self.width), min(ury, p_lly + self.height)))
        self.by_id = {patch.id: patch for patch in self.patches}

    def row(self, y: int) -> int:
        return max(0, min(self.rows - 1, (y - self.lly) // self.height))

    def col(self, x: int) -> int:
        return max(0, min(self.cols - 1, (x - self.llx) // self.width))

    def patch_id(self, x: int, y: int) -> int:
        return self.row(y) * self.cols + self.col(x)

    def patches_for_segment(self, seg: Segment) -> list[int]:
        bbox = (min(seg.x1, seg.x2), min(seg.y1, seg.y2), max(seg.x1, seg.x2), max(seg.y1, seg.y2))
        ids = self.patches_for_bbox(bbox)
        return ids or [self.patch_id(seg.x1, seg.y1)]

    def patches_for_bbox(self, bbox: tuple[int, int, int, int]) -> list[int]:
        col0 = self.col(bbox[0])
        col1 = self.col(bbox[2])
        row0 = self.row(bbox[1])
        row1 = self.row(bbox[3])
        ids = []
        for row in range(row0, row1 + 1):
            for col in range(col0, col1 + 1):
                ids.append(row * self.cols + col)
        return ids

    def wire_density(self, seg: Segment) -> float:
        patch = self.by_id[self.patch_id(seg.x1, seg.y1)]
        return seg.length / patch.area if patch.area else 0


def collect_lefs(platform_dir: Path) -> list[Path]:
    lef_dir = platform_dir / "lef"
    if not lef_dir.exists():
        return []
    preferred = [
        lef_dir / "NangateOpenCellLibrary.tech.lef",
        lef_dir / "NangateOpenCellLibrary.macro.mod.lef",
        lef_dir / "NangateOpenCellLibrary.macro.lef",
    ]
    paths = [path for path in preferred if path.exists()]
    for path in sorted(lef_dir.glob("*.lef")):
        if path not in paths:
            paths.append(path)
    return paths


def collect_libs(
    flow_root: Path, platform: str, design: str, variant: str, platform_dir: Path, objects_dir: Path | None = None
) -> list[Path]:
    object_lib_dir = (objects_dir / "lib") if objects_dir else flow_root / "flow" / "objects" / platform / design / variant / "lib"
    if object_lib_dir.exists():
        object_libs = sorted(object_lib_dir.glob("*.lib"))
        if object_libs:
            return object_libs
    lib_dir = platform_dir / "lib"
    return sorted(lib_dir.rglob("*.lib")) if lib_dir.exists() else []


def ensure_from_odb(odb_path: Path, out_path: Path, script: Path, openroad_bin: str) -> Path:
    if out_path.exists():
        return out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["ODB_FILE"] = str(odb_path)
    env["DEF_FILE"] = str(out_path)
    env["VERILOG_FILE"] = str(out_path)
    subprocess.run([openroad_bin, str(script)], check=True, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return out_path


def build_stage(args: argparse.Namespace, stage: str, lef: LefParser, liberty: LibertyParser) -> None:
    flow_root = Path(args.flow_root).resolve()
    results_dir = Path(args.results_dir).resolve() if args.results_dir else flow_root / "flow" / "results" / args.platform / args.design / args.variant
    scripts_dir = flow_root / "flow" / "scripts"
    cache_root = Path(args.cache_root).resolve() if args.cache_root else flow_root / ".ml_vector_cache" / args.platform
    cache_dir = cache_root / args.design / args.variant
    if stage == "place":
        stage_odb = results_dir / "3_place.odb"
        def_path = results_dir / "3_place.def"
        verilog_path = results_dir / "3_place.v"
        if not def_path.exists():
            def_path = ensure_from_odb(stage_odb, cache_dir / "3_place.def", scripts_dir / "write_def.tcl", args.openroad)
        if not verilog_path.exists():
            verilog_path = ensure_from_odb(stage_odb, cache_dir / "3_place.v", scripts_dir / "write_verilog.tcl", args.openroad)
        spef_path: Path | None = None
    elif stage == "cts":
        stage_odb = results_dir / "4_cts.odb"
        def_path = results_dir / "4_cts.def"
        verilog_path = results_dir / "4_cts.v"
        if not def_path.exists():
            def_path = ensure_from_odb(stage_odb, cache_dir / "4_cts.def", scripts_dir / "write_def.tcl", args.openroad)
        if not verilog_path.exists():
            verilog_path = ensure_from_odb(stage_odb, cache_dir / "4_cts.v", scripts_dir / "write_verilog.tcl", args.openroad)
        spef_path: Path | None = None
    else:
        def_path = results_dir / "6_final.def"
        verilog_path = results_dir / "6_final.v"
        spef_path = results_dir / "6_final.spef"
        if not def_path.exists():
            def_path = ensure_from_odb(results_dir / "5_route.odb", cache_dir / "5_route.def", scripts_dir / "write_def.tcl", args.openroad)
        if not verilog_path.exists():
            verilog_path = ensure_from_odb(results_dir / "5_route.odb", cache_dir / "5_route.v", scripts_dir / "write_verilog.tcl", args.openroad)
    ports = parse_verilog_ports(verilog_path)
    design = DefParser(def_path, lef, liberty, ports).parse()
    caps, ress = parse_spef(spef_path) if spef_path is not None else ({}, {})
    for net in design.nets:
        net.capacitance = caps.get(net.name, 0)
        net.resistance = ress.get(net.name, 0)
        net.has_spef = net.name in caps or net.name in ress
    scenario = args.scenario or f"{args.variant}_default"
    output_design = args.dataset_design or dataset_design_name(args.design)
    vectors_dir = Path(args.out).resolve() / output_design / stage / "vectors" / f"{output_design}_{stage}_vectors" / "vectors" / scenario
    reports_dir = Path(args.reports_dir).resolve() if args.reports_dir else flow_root / "flow" / "reports" / args.platform / args.design / args.variant / "ml_reports"
    report_data = load_ml_reports(reports_dir, stage, args.platform) if reports_dir.exists() else ReportData()
    DatasetWriter(design, lef, liberty, vectors_dir, report_data, patch_size_dbu=args.patch_size_dbu).write()


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse ORFS results into gcd-style ML vector datasets.")
    parser.add_argument("--flow-root", default="/home/lyh/OpenROAD-flow-scripts")
    parser.add_argument("--platform", default="nangate45")
    parser.add_argument("--design", required=True)
    parser.add_argument("--variant", default="base")
    parser.add_argument("--dataset-design", default="", help="Output dataset design name. Defaults to normalized --design.")
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--cache-root",
        default="",
        help="Directory for ODB-derived intermediate DEF/Verilog files. Defaults outside --out.",
    )
    parser.add_argument("--stage", choices=["place", "cts", "route", "both", "all"], default="both")
    parser.add_argument("--scenario", default="")
    parser.add_argument("--patch-size-dbu", type=int, default=3330)
    parser.add_argument("--openroad", default="/home/lyh/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad")
    parser.add_argument("--results-dir", default="", help="External result directory containing stage files.")
    parser.add_argument("--reports-dir", default="", help="External ml_reports directory.")
    parser.add_argument("--objects-dir", default="", help="External objects directory containing lib files.")
    args = parser.parse_args()

    flow_root = Path(args.flow_root).resolve()
    platform_dir = flow_root / "flow" / "platforms" / args.platform

    # Read DBU from a readily available DEF when possible, otherwise use the
    # Nangate/OpenROAD common default.
    results_dir = Path(args.results_dir).resolve() if args.results_dir else flow_root / "flow" / "results" / args.platform / args.design / args.variant
    route_def = results_dir / "6_final.def"
    dbu = 2000
    if route_def.exists():
        match = re.search(r"UNITS\s+DISTANCE\s+MICRONS\s+(\d+)\s*;", read_text(route_def))
        if match:
            dbu = int(match.group(1))

    lef = LefParser(collect_lefs(platform_dir)).parse(dbu)
    objects_dir = Path(args.objects_dir).resolve() if args.objects_dir else None
    liberty = LibertyParser(collect_libs(flow_root, args.platform, args.design, args.variant, platform_dir, objects_dir)).parse()
    stages = {"both": ["place", "route"], "all": ["place", "cts", "route"]}.get(args.stage, [args.stage])
    for stage in stages:
        build_stage(args, stage, lef, liberty)


if __name__ == "__main__":
    main()
