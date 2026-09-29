#!/usr/bin/env python3
"""Cross-file consistency checker for parsed gcd-style ML vector datasets.

Verifies that the same design entity (net / instance / pin / port) uses the
same name and id in every file that references it, so the dataset can be
joined reliably across nets/, instances/, wire_graph/, instance_graph/,
patchs/ and wire_paths/.

Usage:
  python3 dataset_parser/check_consistency.py <strategy_vector_dir>
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path

from ml_vector_parser import iter_nets, iter_patches, iter_wire_paths, read_json, zstd_path


def load(path: Path):
    return read_json(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vector_dir", help="A <strategy>/ vector directory.")
    args = parser.parse_args()
    d = Path(args.vector_dir)
    problems: list[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            problems.append(msg)

    # instances.json
    instances = load(d / "instances" / "instances.json")["instances"]
    inst_names = {i["name"] for i in instances}
    check(len(inst_names) == len(instances), f"instance 名字重复: {len(instances) - len(inst_names)} 个")
    for name in inst_names:
        check("\\" not in name, f"instances.json 含转义名: {name!r}")
    check(all(i["id"] == idx for idx, i in enumerate(instances)), "instance id 与序号不一致")

    # wire_graph nodes
    wg = load(d / "wire_graph" / "timing_wire_graph.json")
    ports = set()
    for n in wg["nodes"]:
        name = n["name"]
        check("\\" not in name, f"wire_graph 节点含转义名: {name!r}")
        if n["is_port"]:
            ports.add(name)
        elif n["is_pin"] and ":" in name:
            inst = name.split(":", 1)[0]
            check(inst in inst_names, f"wire_graph 节点实例不存在: {name!r}")
        else:
            check(n["is_pin"] or n["is_port"], f"wire_graph 节点既非 pin 也非 port: {name!r}")
    wg_edges = len(wg["edges"])
    check(all(e["from_node"] != e["to_node"] for e in wg["edges"]), "wire_graph 存在自环边")

    # instance_graph nodes
    ig = load(d / "instance_graph" / "timing_instance_graph.json")
    ig_names = {n["name"] for n in ig["nodes"]}
    for name in ig_names:
        check("\\" not in name, f"instance_graph 节点含转义名: {name!r}")
    check(ig_names <= inst_names, f"instance_graph 有 instances.json 中不存在的实例: {sorted(ig_names - inst_names)[:5]}")
    check(
        len(ig_names) == len(inst_names),
        f"instance_graph 与 instances.json 实例数不一致: {len(ig_names)} vs {len(inst_names)}",
    )

    # nets
    net_count = 0
    net_names: dict[int, str] = {}
    for net_id, j in iter_nets(d):
        net_count += 1
        check(net_id == j["id"], f"net 记录 id 与解包位置不一致: 行 {net_id} 的 id 是 {j['id']}")
        name = j["name"]
        check("\\" not in name, f"net 名含转义: {name!r}")
        check(name not in net_names.values(), f"net 名重复: {name!r}")
        net_names[net_id] = name
        for pin in j["pins"]:
            check("\\" not in pin["i"] and "\\" not in pin["p"], f"net {name} 的 pin 含转义: {pin}")
            if pin["i"] != "PIN":
                check(pin["i"] in inst_names, f"net {name} 的 pin 实例不存在: {pin['i']!r}")
        for arc in j.get("arcs", []):
            for role in ("driver", "sink"):
                pin = arc.get(role, "")
                check("\\" not in pin, f"net {name} 的 arc {role} 含转义: {pin!r}")
                if "/" in pin:
                    inst = pin.split("/", 1)[0]
                    check(inst in inst_names, f"net {name} 的 arc {role} 实例不存在: {pin!r}")
                elif pin:
                    check(pin in ports, f"net {name} 的 arc {role} 端口不在 wire_graph 端口集合: {pin!r}")

    # patchs: sub_nets ids 必须指向存在的 net
    patch_count = 0
    for patch_id, j in iter_patches(d):
        patch_count += 1
        for sub in j.get("sub_nets", []):
            check(sub["id"] in net_names, f"patch_{patch_id} 的 sub_net id {sub['id']} 不存在于 nets/")
        for layer in j.get("patch_layer", []):
            for net_entry in layer.get("nets", []):
                check(net_entry["id"] in net_names, f"patch_{patch_id} 的 patch_layer net id {net_entry['id']} 不存在于 nets/")

    # wire_paths: Point 名字无转义且实例存在
    wp_count = 0
    for path_id, record in iter_wire_paths(d):
        wp_count += 1
        for item in record:
            for v in item.values():
                if isinstance(v, dict) and "Point" in v:
                    point = v["Point"]
                    check("\\" not in point, f"wire_path_{path_id} 的 Point 含转义: {point!r}")
                    if "(" in point:
                        pin_part = point.split("(", 1)[0].strip()
                        if ":" in pin_part:
                            inst = pin_part.split(":", 1)[0]
                            # "PIN:<port>" 是 parser 顶层端口引脚约定（driver 为 PIN 的 fallback wire path）
                            check(inst in inst_names or inst in ("", "PIN"), f"wire_path_{path_id} 的 Point 实例不存在: {point!r}")
                        elif "/" in pin_part:
                            inst = pin_part.split("/", 1)[0]
                            check(inst in inst_names, f"wire_path_{path_id} 的 Point 实例不存在: {point!r}")

    # design_info 存在
    check(zstd_path(d / "design_info.json").exists(), "缺少 design_info.json.zst")

    print(f"files: nets={net_count} patches={patch_count} wire_paths={wp_count} "
          f"wire_graph_edges={wg_edges} instances={len(instances)}")
    if problems:
        print(f"\nFAIL: {len(problems)} 个一致性问题:")
        for p in problems[:50]:
            print(f"  - {p}")
        sys.exit(1)
    print("PASS: 命名与 id 跨文件一致")


if __name__ == "__main__":
    main()
