#!/usr/bin/env python3
"""
Click accuracy evaluator for daolv task trajectories.

Method:
  1. Parse debug_log.txt to extract all click actions with context
  2. For each click: draw a red circle on the screenshot at the exact click point
  3. Send to Claude VLM with the instruction: did the click land on the correct element?
  4. Also extract the Analyst's own next-step status as a baseline comparison
  5. Report per-task and overall accuracy
"""

import re
import json
import base64
import io
import os
import sys
import time
from pathlib import Path
from PIL import Image, ImageDraw
from anthropic import AnthropicBedrock

BASE_DIR = Path("/Users/xiaoan/Desktop/ui-agent/test_data3/daolv")  # default, overridden by CLI arg
SCREEN_LOGICAL = (1920, 1080)


def parse_debug_log(log_path: Path) -> list[dict]:
    """
    Parse debug_log.txt into a list of step dicts.

    Each dict contains:
      step            - step number
      analyst_status  - [🔍 状态] text (evaluation of the *previous* action)
      instruction     - [👉 指令] text (what to do in this step)
      action_type     - click / type / scroll / etc.
      logical_coords  - (x, y) in 1920×1080 space, or None
      screenshot      - filename like "step_01.jpg"
    """
    text = log_path.read_text(encoding="utf-8")
    parts = re.split(r"=== Step (\d+)/\d+ ===", text)
    # parts: [header, step_num, block, step_num, block, ...]

    steps = []
    for i in range(1, len(parts), 2):
        step_num = int(parts[i])
        block = parts[i + 1]

        status_m = re.search(r"\*\*\[🔍 状态\]\*\*[：:]\s*(.+)", block)
        instr_m  = re.search(r"\*\*\[👉 指令\]\*\*[：:]\s*(.+)", block)
        action_m = re.search(r"Action: (\w+)\(", block)
        # "模型输出(190,320) /1000*1920x1080 → 逻辑(364,345)"
        coord_m  = re.search(r"→ 逻辑\((\d+),(\d+)\)", block)
        # current screenshot filename
        shot_m   = re.search(r"图2\(Current\):[^\n]+/(step_\d+\.jpg)", block)

        steps.append({
            "step":           step_num,
            "analyst_status": status_m.group(1).strip() if status_m else "",
            "instruction":    instr_m.group(1).strip()  if instr_m  else "",
            "action_type":    action_m.group(1)         if action_m else "",
            "logical_coords": (int(coord_m.group(1)), int(coord_m.group(2))) if coord_m else None,
            "screenshot":     shot_m.group(1) if shot_m else f"step_{step_num:02d}.jpg",
        })

    # Tag each step with the *outcome* judged by the next step's analyst_status
    for i in range(len(steps) - 1):
        next_status = steps[i + 1]["analyst_status"]
        if "成功" in next_status:
            steps[i]["outcome"] = "success"
        elif "失败" in next_status:
            steps[i]["outcome"] = "failure"
        else:
            steps[i]["outcome"] = "unknown"
    if steps:
        steps[-1]["outcome"] = "unknown"  # last step has no next

    return steps


def make_marked_image(img_path: Path, lx: int, ly: int) -> bytes:
    """
    Open screenshot, draw a red circle + crosshair at logical coords,
    resize to ≤1920px wide, return JPEG bytes.
    """
    img = Image.open(img_path).convert("RGB")
    w, h = img.size  # e.g. 3840×2160

    # Map logical → physical pixel
    px = int(lx * w / SCREEN_LOGICAL[0])
    py = int(ly * h / SCREEN_LOGICAL[1])

    draw = ImageDraw.Draw(img)
    r = max(24, w // 80)      # ~30–48 px radius
    lw = max(4, w // 600)     # line width

    draw.ellipse([px - r, py - r, px + r, py + r], outline="red", width=lw * 2)
    draw.line([px - r * 2, py, px + r * 2, py], fill="red", width=lw)
    draw.line([px, py - r * 2, px, py + r * 2], fill="red", width=lw)

    # Downscale to 1920px wide to keep API payload small
    if w > 1920:
        scale = 1920 / w
        img = img.resize((1920, int(h * scale)), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def vlm_judge(client: AnthropicBedrock, img_bytes: bytes,
              instruction: str, step_num: int) -> tuple[bool, str]:
    """
    Ask Claude whether the red-marked click hit the correct element.
    Returns (is_correct, one-line explanation).
    """
    b64 = base64.standard_b64encode(img_bytes).decode()

    prompt = (
        f"这是 GUI Agent 执行第 {step_num} 步时的屏幕截图。\n"
        f"Agent 被指示：{instruction}\n\n"
        "截图中红色圆圈和十字标出了 Agent 实际点击的位置。\n\n"
        "请判断：Agent 点击的位置是否准确击中了目标元素？\n"
        "请只回答：\n"
        "CORRECT - 点击位置准确击中了目标元素\n"
        "INCORRECT - 点击偏离目标（点到了错误元素或空白区域）\n\n"
        "然后用一句话说明原因。"
    )

    model_id = os.environ.get("ANTHROPIC_DEFAULT_OPUS_MODEL", "us.anthropic.claude-opus-4-6-v1")
    resp = client.messages.create(
        model=model_id,
        max_tokens=150,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64",
                            "media_type": "image/jpeg",
                            "data": b64}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    text = resp.content[0].text.strip()
    correct = text.upper().startswith("CORRECT")
    return correct, text


def process_task(task_dir: Path, client: AnthropicBedrock) -> list[dict]:
    log_path = task_dir / "debug_log.txt"
    if not log_path.exists():
        return []

    steps = parse_debug_log(log_path)
    results = []

    click_types = {"click", "left_double", "right_single"}
    click_steps = [s for s in steps
                   if s["action_type"] in click_types
                   and s["logical_coords"] is not None
                   and s["instruction"]]

    print(f"  {len(click_steps)} click actions found")

    for s in click_steps:
        shot_path = task_dir / s["screenshot"]
        if not shot_path.exists():
            print(f"    [SKIP] step {s['step']}: screenshot not found")
            continue

        lx, ly = s["logical_coords"]
        try:
            img_bytes = make_marked_image(shot_path, lx, ly)
            vlm_ok, explanation = vlm_judge(client, img_bytes, s["instruction"], s["step"])
        except Exception as e:
            print(f"    [ERR] step {s['step']}: {e}")
            continue

        icon = "✓" if vlm_ok else "✗"
        analyst_tag = f"[analyst:{s['outcome']}]" if s["outcome"] != "unknown" else ""
        print(f"    [{icon}] step {s['step']:2d} {analyst_tag}: "
              f"{s['instruction'][:48]}… → {explanation[:70]}")

        results.append({
            "task":            task_dir.name,
            "step":            s["step"],
            "action_type":     s["action_type"],
            "instruction":     s["instruction"],
            "coords_logical":  list(s["logical_coords"]),
            "vlm_correct":     vlm_ok,
            "vlm_explanation": explanation,
            "analyst_outcome": s["outcome"],
        })

        time.sleep(0.3)  # gentle rate-limit

    return results


def print_report(all_results: list[dict]) -> None:
    print("\n" + "=" * 64)
    print("Click Accuracy Report")
    print("=" * 64)

    total = len(all_results)
    if total == 0:
        print("No click results found.")
        return

    vlm_ok  = sum(1 for r in all_results if r["vlm_correct"])
    ana_ok  = sum(1 for r in all_results if r["analyst_outcome"] == "success")
    ana_tot = sum(1 for r in all_results if r["analyst_outcome"] != "unknown")

    print(f"Total click actions : {total}")
    print(f"VLM-based accuracy  : {vlm_ok}/{total} = {vlm_ok/total:.1%}")
    if ana_tot:
        print(f"Analyst-based acc   : {ana_ok}/{ana_tot} = {ana_ok/ana_tot:.1%}  (next-step verdict)")

    # Agreement between VLM and Analyst
    both = [(r["vlm_correct"], r["analyst_outcome"] == "success")
            for r in all_results if r["analyst_outcome"] != "unknown"]
    if both:
        agree = sum(1 for v, a in both if v == a)
        print(f"VLM ↔ Analyst agree : {agree}/{len(both)} = {agree/len(both):.1%}")

    print("\nPer-task breakdown:")
    tasks = sorted({r["task"] for r in all_results})
    for t in tasks:
        tr = [r for r in all_results if r["task"] == t]
        ok = sum(1 for r in tr if r["vlm_correct"])
        print(f"  {t:8s}  {ok}/{len(tr)} = {ok/len(tr):.1%}")


def main():
    base_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else BASE_DIR

    client = AnthropicBedrock()
    all_results = []

    task_dirs = sorted(base_dir.glob("task*"),
                       key=lambda p: int(re.search(r"\d+", p.name).group()))

    print(f"Site: {base_dir.name}  ({len(task_dirs)} tasks)")

    for task_dir in task_dirs:
        print(f"\n=== {task_dir.name} ===")
        results = process_task(task_dir, client)
        all_results.extend(results)

    print_report(all_results)

    out_path = base_dir.parent / f"click_accuracy_{base_dir.name}.json"
    out_path.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDetailed results → {out_path}")


if __name__ == "__main__":
    main()
