#!/usr/bin/env python3
"""
Click accuracy evaluator using Analyst next-step status only (no VLM).

For each click action, the outcome is determined by whether the *next* step's
[🔍 状态] contains 成功 or 失败.
"""

import re
import json
import sys
from pathlib import Path


def parse_debug_log(log_path: Path) -> list[dict]:
    text = log_path.read_text(encoding="utf-8")
    parts = re.split(r"=== Step (\d+)/\d+ ===", text)

    steps = []
    for i in range(1, len(parts), 2):
        step_num = int(parts[i])
        block = parts[i + 1]

        status_m = re.search(r"\*\*\[🔍 状态\]\*\*[：:]\s*(.+)", block)
        instr_m  = re.search(r"\*\*\[👉 指令\]\*\*[：:]\s*(.+)", block)
        action_m = re.search(r"Action: (\w+)\(", block)
        coord_m  = re.search(r"→ 逻辑\((\d+),(\d+)\)", block)

        steps.append({
            "step":           step_num,
            "analyst_status": status_m.group(1).strip() if status_m else "",
            "instruction":    instr_m.group(1).strip()  if instr_m  else "",
            "action_type":    action_m.group(1)         if action_m else "",
            "logical_coords": (int(coord_m.group(1)), int(coord_m.group(2))) if coord_m else None,
        })

    # Tag each step with outcome from next step's analyst_status
    for i in range(len(steps) - 1):
        nxt = steps[i + 1]["analyst_status"]
        if "成功" in nxt:
            steps[i]["outcome"] = "success"
        elif "失败" in nxt:
            steps[i]["outcome"] = "failure"
        else:
            steps[i]["outcome"] = "unknown"
    if steps:
        steps[-1]["outcome"] = "unknown"

    return steps


def process_task(task_dir: Path) -> list[dict]:
    log_path = task_dir / "debug_log.txt"
    if not log_path.exists():
        return []

    steps = parse_debug_log(log_path)
    click_types = {"click", "left_double", "right_single"}
    click_steps = [s for s in steps if s["action_type"] in click_types]

    results = []
    for s in click_steps:
        icon = {"success": "✓", "failure": "✗", "unknown": "?"}.get(s["outcome"], "?")
        print(f"  [{icon}] step {s['step']:2d} [{s['outcome']:7s}]: {s['instruction'][:60]}")
        results.append({
            "task":           task_dir.name,
            "step":           s["step"],
            "action_type":    s["action_type"],
            "instruction":    s["instruction"],
            "coords_logical": list(s["logical_coords"]) if s["logical_coords"] else None,
            "outcome":        s["outcome"],
        })
    return results


def print_report(all_results: list[dict], site: str) -> None:
    print("\n" + "=" * 64)
    print(f"Click Accuracy Report — {site}")
    print("=" * 64)

    total   = len(all_results)
    known   = [r for r in all_results if r["outcome"] != "unknown"]
    success = sum(1 for r in known if r["outcome"] == "success")

    print(f"Total click actions : {total}")
    print(f"Judged (non-unknown): {len(known)}")
    print(f"Success             : {success}/{len(known)} = {success/len(known):.1%}" if known else "No judged results.")

    print("\nPer-task breakdown:")
    tasks = sorted({r["task"] for r in all_results},
                   key=lambda t: int(re.search(r"\d+", t).group()))
    for t in tasks:
        tr = [r for r in all_results if r["task"] == t and r["outcome"] != "unknown"]
        ok = sum(1 for r in tr if r["outcome"] == "success")
        total_t = [r for r in all_results if r["task"] == t]
        unk = sum(1 for r in total_t if r["outcome"] == "unknown")
        bar = f"{ok}/{len(tr)}" if tr else "-"
        unk_note = f"  (+{unk} unknown)" if unk else ""
        print(f"  {t:8s}  {bar}{unk_note}")

    # Failure details
    failures = [r for r in all_results if r["outcome"] == "failure"]
    if failures:
        print(f"\nFailures ({len(failures)}):")
        for r in failures:
            print(f"  {r['task']} step {r['step']:2d}: {r['instruction'][:60]}")


def main():
    base_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("test_data3/daolv")
    site = base_dir.name

    task_dirs = sorted(base_dir.glob("task*"),
                       key=lambda p: int(re.search(r"\d+", p.name).group()))
    print(f"Site: {site}  ({len(task_dirs)} tasks)")

    all_results = []
    for task_dir in task_dirs:
        print(f"\n=== {task_dir.name} ===")
        results = process_task(task_dir)
        all_results.extend(results)

    print_report(all_results, site)

    out_path = base_dir.parent / f"analyst_accuracy_{site}.json"
    out_path.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResults → {out_path}")


if __name__ == "__main__":
    main()
