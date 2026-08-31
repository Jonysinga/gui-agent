"""
scripted_main.py - 预置指令版：跳过大脑(Analyst)，直接用人工编写的分步指令驱动小脑(Executor)
用法：uv run python scripted_main.py --task 0 --prompt prompt_uitars.yaml

与 debug_main.py 的区别：
- 去掉 Analyst(Gemini) 调用，改为从 tasks_scripted.yaml 的 steps 列表中按序取指令
- 每一步执行完后，取下一条预置指令发给 Executor
- Executor 仍然接收当前截图 + 指令，输出动作并执行
- 所有坐标转换、动作执行逻辑与 debug_main.py 完全一致
"""
import yaml
import argparse
import os
import sys
import time
import json
import re
import ast
import base64
import math
import platform
import subprocess
import shutil
from io import BytesIO
from openai import OpenAI
import pyautogui
import pyperclip
from PIL import Image

# ================= 配置（与 debug_main.py 保持一致）=================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# ---- Executor 后端选择: "local" 或 "doubao" ----
EXECUTOR_BACKEND = "doubao"

_EXECUTOR_CONFIGS = {
    "doubao": {
        "model": "doubao-1-5-ui-tars-250428",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api_key": os.getenv("DOUBAO_API_KEY", ""),
    },
    "local": {
        "model": "/mnt/disk3/models/UI-TARS-1.5-7B",
        "base_url": "http://115.190.215.236:8004/v1",
        "api_key": "EMPTY",
    },
}

_cfg = _EXECUTOR_CONFIGS[EXECUTOR_BACKEND]
EXECUTOR_MODEL = _cfg["model"]
client_executor = OpenAI(base_url=_cfg["base_url"], api_key=_cfg["api_key"])

SCREEN_WIDTH, SCREEN_HEIGHT = pyautogui.size()
_ss = pyautogui.screenshot()
SCREENSHOT_W = _ss.size[0]
SCREENSHOT_H = _ss.size[1]
SCALE_X = SCREENSHOT_W / SCREEN_WIDTH
SCALE_Y = SCREENSHOT_H / SCREEN_HEIGHT
del _ss
pyautogui.FAILSAFE = True
IS_MAC = platform.system() == "Darwin"
CMD_KEY = 'command' if IS_MAC else 'ctrl'


# ================= 录屏 =================
class ScreenRecorder:
    def __init__(self, output_path, fps=10):
        self.output_path = output_path
        self.fps = fps
        self.process = None
        self._available = shutil.which("ffmpeg") is not None

    def start(self):
        if not self._available:
            print("   [录屏] 未找到 ffmpeg，跳过录屏。请运行: brew install ffmpeg")
            return
        if IS_MAC:
            screen_idx = self._find_mac_screen_index()
            cmd = [
                "ffmpeg", "-y", "-f", "avfoundation",
                "-capture_cursor", "1", "-capture_mouse_clicks", "1",
                "-i", f"{screen_idx}", "-r", str(self.fps),
                "-c:v", "libx264", "-crf", "28", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", self.output_path,
            ]
        else:
            display = os.environ.get("DISPLAY", ":0")
            cmd = [
                "ffmpeg", "-y", "-f", "x11grab", "-r", str(self.fps),
                "-s", f"{SCREEN_WIDTH}x{SCREEN_HEIGHT}",
                "-i", f"{display}.0+0,0",
                "-c:v", "libx264", "-crf", "28", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", self.output_path,
            ]
        self.process = subprocess.Popen(
            cmd, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        print(f"   [录屏] 开始录制 → {self.output_path}")

    def stop(self):
        if not self.process:
            return
        try:
            self.process.stdin.write(b"q")
            self.process.stdin.flush()
            self.process.wait(timeout=15)
        except Exception:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()
        self.process = None
        if os.path.exists(self.output_path):
            size_mb = os.path.getsize(self.output_path) / 1024 / 1024
            print(f"   [录屏] 已保存: {self.output_path} ({size_mb:.1f} MB)")
        else:
            print("   [录屏] 录制文件未生成")

    def _find_mac_screen_index(self):
        try:
            result = subprocess.run(
                ["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stderr.splitlines():
                if "Capture screen" in line or "screen" in line.lower():
                    m = re.search(r'\[(\d+)\]', line)
                    if m:
                        return m.group(1)
        except Exception:
            pass
        return "1"


# ================= smart_resize =================
IMAGE_FACTOR = 28
MIN_PIXELS = 100 * 28 * 28
MAX_PIXELS = 16384 * 28 * 28


def round_by_factor(number, factor):
    return round(number / factor) * factor


def ceil_by_factor(number, factor):
    return math.ceil(number / factor) * factor


def floor_by_factor(number, factor):
    return math.floor(number / factor) * factor


def smart_resize(height, width, factor=IMAGE_FACTOR,
                 min_pixels=MIN_PIXELS, max_pixels=MAX_PIXELS):
    max_ratio = 200
    if max(height, width) / min(height, width) > max_ratio:
        raise ValueError(f"aspect ratio too large: {max(height, width) / min(height, width)}")
    h_bar = max(factor, round_by_factor(height, factor))
    w_bar = max(factor, round_by_factor(width, factor))
    if h_bar * w_bar > max_pixels:
        beta = math.sqrt((height * width) / max_pixels)
        h_bar = floor_by_factor(height / beta, factor)
        w_bar = floor_by_factor(width / beta, factor)
    elif h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        h_bar = ceil_by_factor(height * beta, factor)
        w_bar = ceil_by_factor(width * beta, factor)
    return h_bar, w_bar


# ================= Tee =================
class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)
            s.flush()

    def flush(self):
        for s in self.streams:
            s.flush()


# ================= 工具函数 =================
def encode_image_to_base64(image):
    if image.mode == 'RGBA':
        image = image.convert('RGB')
    buffered = BytesIO()
    image.save(buffered, format="JPEG", quality=100)
    return base64.b64encode(buffered.getvalue()).decode('utf-8')


def take_screenshot():
    return pyautogui.screenshot()


def preprocess_screenshot(screenshot):
    orig_w, orig_h = screenshot.size
    sr_h, sr_w = smart_resize(orig_h, orig_w)
    resized = screenshot.resize((sr_w, sr_h), Image.LANCZOS)
    if resized.mode == 'RGBA':
        resized = resized.convert('RGB')
    buffered = BytesIO()
    resized.save(buffered, format="JPEG", quality=95)
    b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
    print(f"   [预处理] 原始截图: {orig_w}x{orig_h} → smart_resize: {sr_w}x{sr_h}")
    return resized, b64, sr_w, sr_h


def convert_uitars_coord_to_logical(model_x, model_y, sr_w, sr_h):
    norm_x = model_x / sr_w
    norm_y = model_y / sr_h
    phys_x = norm_x * SCREENSHOT_W
    phys_y = norm_y * SCREENSHOT_H
    logical_x = int(max(0, min(phys_x / SCALE_X, SCREEN_WIDTH - 1)))
    logical_y = int(max(0, min(phys_y / SCALE_Y, SCREEN_HEIGHT - 1)))
    print(f"   [坐标/local] 模型输出({model_x},{model_y}) / resize({sr_w},{sr_h})"
          f" → 归一化({norm_x:.3f},{norm_y:.3f})"
          f" → 物理({phys_x:.0f},{phys_y:.0f})"
          f" → 逻辑({logical_x},{logical_y})")
    return logical_x, logical_y


def convert_doubao_coord_to_logical(model_x, model_y):
    if model_x <= 1000 and model_y <= 1000:
        logical_x = int(model_x / 1000 * SCREEN_WIDTH)
        logical_y = int(model_y / 1000 * SCREEN_HEIGHT)
        coord_mode = "norm(0-1000)"
    else:
        logical_x = int(max(0, min(model_x / SCALE_X, SCREEN_WIDTH - 1)))
        logical_y = int(max(0, min(model_y / SCALE_Y, SCREEN_HEIGHT - 1)))
        coord_mode = f"pixel(scale={SCALE_X:.1f}x)"
    print(f"   [坐标/doubao] 模型输出({model_x},{model_y}) ({coord_mode})"
          f" → 逻辑({logical_x},{logical_y})")
    return logical_x, logical_y


def coord_to_logical(model_x, model_y, sr_w, sr_h):
    if EXECUTOR_BACKEND == "doubao":
        return convert_doubao_coord_to_logical(model_x, model_y)
    else:
        return convert_uitars_coord_to_logical(model_x, model_y, sr_w, sr_h)


# ================= UI-TARS 原生输出解析 =================
def parse_uitars_response(response_text):
    text = response_text.strip()
    thought = ""
    thought_match = re.search(r'Thought:\s*(.+?)(?=\s*Action:|$)', text, re.DOTALL)
    if thought_match:
        thought = thought_match.group(1).strip()
    action_match = re.search(r'Action:\s*(.+)', text, re.DOTALL)
    if not action_match:
        print(f"   [解析] 未找到 Action 字段")
        return [], thought
    action_str = action_match.group(1).strip()
    action_str = re.sub(
        r"start_point='<point>(\d+)\s+(\d+)</point>'",
        lambda m: f"start_box='({m.group(1)},{m.group(2)})'",
        action_str
    )
    action_str = re.sub(
        r"end_point='<point>(\d+)\s+(\d+)</point>'",
        lambda m: f"end_box='({m.group(1)},{m.group(2)})'",
        action_str
    )
    action_str = re.sub(
        r"point='<point>(\d+)\s+(\d+)</point>'",
        lambda m: f"start_box='({m.group(1)},{m.group(2)})'",
        action_str
    )
    action_str = action_str.replace("<|box_start|>", "").replace("<|box_end|>", "")
    if ")\n\n" in action_str:
        raw_actions = action_str.split(")\n\n")
    else:
        raw_actions = re.split(r'\)\s*\n(?=\s*\w+\s*\()', action_str)
    raw_actions = [r if r.strip().endswith(')') else r + ')' for r in raw_actions]
    actions = []
    for raw in raw_actions:
        raw = raw.strip()
        if not raw:
            continue
        if not raw.endswith(")"):
            raw += ")"
        try:
            parsed = _parse_single_action(raw)
            if parsed:
                parsed["thought"] = thought
                actions.append(parsed)
        except Exception as e:
            print(f"   [解析] 单个 action 解析失败: {raw} -> {e}")
    return actions, thought


def _parse_single_action(action_str):
    try:
        node = ast.parse(action_str.replace("\n", "\\n"), mode='eval')
        if not isinstance(node, ast.Expression) or not isinstance(node.body, ast.Call):
            raise ValueError("Not a function call")
        call = node.body
        func_name = call.func.id if isinstance(call.func, ast.Name) else str(call.func.attr)
        kwargs = {}
        for kw in call.keywords:
            if isinstance(kw.value, ast.Constant):
                kwargs[kw.arg] = kw.value.value
            elif isinstance(kw.value, ast.Str):
                kwargs[kw.arg] = kw.value.s
    except Exception:
        func_match = re.match(r'(\w+)\s*\(', action_str)
        if not func_match:
            return None
        func_name = func_match.group(1)
        kwargs = {}
        box_match = re.search(r"start_box='?\(?(\d+)\s*,\s*(\d+)\)?'?", action_str)
        if box_match:
            kwargs['start_box'] = f"({box_match.group(1)},{box_match.group(2)})"
        end_match = re.search(r"end_box='?\(?(\d+)\s*,\s*(\d+)\)?'?", action_str)
        if end_match:
            kwargs['end_box'] = f"({end_match.group(1)},{end_match.group(2)})"
        content_match = re.search(r"content='(.*?)'", action_str)
        if content_match:
            kwargs['content'] = content_match.group(1)
        key_match = re.search(r"key='(.*?)'", action_str)
        if key_match:
            kwargs['key'] = key_match.group(1)
        dir_match = re.search(r"direction='(.*?)'", action_str)
        if dir_match:
            kwargs['direction'] = dir_match.group(1)

    point = [0, 0]
    for box_key in ['start_box', 'point']:
        if box_key in kwargs:
            nums = re.findall(r'\d+', str(kwargs[box_key]))
            if len(nums) >= 2:
                point = [int(nums[0]), int(nums[1])]
            break

    action_map = {
        'click': 'click',
        'left_single': 'click',
        'left_double': 'double_click',
        'right_single': 'right_click',
        'type': 'type',
        'hotkey': 'hotkey',
        'scroll': 'scroll',
        'wait': 'wait',
        'finished': 'finish',
        'drag': 'drag',
    }
    unified_action = action_map.get(func_name, func_name)
    result = {
        "action": unified_action,
        "point": point,
        "text": kwargs.get('content', ''),
        "key": kwargs.get('key', kwargs.get('hotkey', '')),
        "direction": kwargs.get('direction', 'down'),
        "summary": f"{func_name}({kwargs})",
        "thought": "",
    }
    if func_name == 'drag' and 'end_box' in kwargs:
        end_nums = re.findall(r'\d+', str(kwargs['end_box']))
        if len(end_nums) >= 2:
            result['end_point'] = [int(end_nums[0]), int(end_nums[1])]
    return result


# ================= 执行动作 =================
def execute_action(data, sr_w, sr_h):
    action = data.get("action")
    point = data.get("point", [0, 0])
    text = data.get("text", "")
    key = data.get("key", "")
    direction = data.get("direction", "down")

    try:
        if action == "click":
            x, y = coord_to_logical(point[0], point[1], sr_w, sr_h)
            data["_logical_xy"] = [x, y]
            print(f"   单击: ({x}, {y})")
            pyautogui.moveTo(x, y, duration=0.5)
            pyautogui.click()
            return False, f"CLICK({point[0]},{point[1]})"

        elif action == "double_click":
            x, y = coord_to_logical(point[0], point[1], sr_w, sr_h)
            data["_logical_xy"] = [x, y]
            print(f"   双击: ({x}, {y})")
            pyautogui.moveTo(x, y, duration=0.5)
            pyautogui.doubleClick()
            return False, f"DOUBLE_CLICK({point[0]},{point[1]})"

        elif action == "right_click":
            x, y = coord_to_logical(point[0], point[1], sr_w, sr_h)
            data["_logical_xy"] = [x, y]
            print(f"   右键: ({x}, {y})")
            pyautogui.moveTo(x, y, duration=0.5)
            pyautogui.click(button='right')
            return False, f"RIGHT_CLICK({point[0]},{point[1]})"

        elif action == "type":
            if point and point != [0, 0]:
                x, y = coord_to_logical(point[0], point[1], sr_w, sr_h)
                data["_logical_xy"] = [x, y]
                pyautogui.moveTo(x, y, duration=0.3)
                pyautogui.click()
                time.sleep(0.3)
            print(f"   输入: {text}")
            should_enter = text.endswith("\\n") or text.endswith("\n")
            clean_text = text.rstrip("\\n").rstrip("\n")
            if clean_text:
                pyperclip.copy(clean_text)
                time.sleep(0.3)
                pyautogui.hotkey(CMD_KEY, 'a')
                time.sleep(0.1)
                pyautogui.hotkey(CMD_KEY, 'v')
                time.sleep(0.5)
            if should_enter:
                pyautogui.press('return' if IS_MAC else 'enter')
            return False, f"TYPE('{text}')"

        elif action == "hotkey":
            keys = key.split()
            print(f"   热键: {keys}")
            convert_keys = []
            for k in keys:
                if k == 'ctrl' and IS_MAC:
                    k = 'command'
                if k == 'space':
                    k = ' '
                convert_keys.append(k)
            pyautogui.hotkey(*convert_keys)
            return False, f"HOTKEY({key})"

        elif action == "press":
            print(f"   按键: {key}")
            if key == 'enter':
                key = 'return' if IS_MAC else 'enter'
            pyautogui.press(key)
            return False, f"PRESS('{key}')"

        elif action == "scroll":
            scroll_dir = direction.lower() if direction else "down"
            if point and point != [0, 0]:
                x, y = coord_to_logical(point[0], point[1], sr_w, sr_h)
                data["_logical_xy"] = [x, y]
                pyautogui.moveTo(x, y, duration=0.2)
            if "up" in scroll_dir:
                print("   上滚")
                pyautogui.scroll(5 if IS_MAC else 500)
            elif "down" in scroll_dir:
                print("   下滚")
                pyautogui.scroll(-5 if IS_MAC else -500)
            return False, f"SCROLL({scroll_dir})"

        elif action == "scroll_down":
            print("   下滚")
            pyautogui.scroll(-5 if IS_MAC else -500)
            return False, "SCROLL_DOWN"

        elif action == "scroll_up":
            print("   上滚")
            pyautogui.scroll(5 if IS_MAC else 500)
            return False, "SCROLL_UP"

        elif action == "wait":
            print("   等待 5s...")
            time.sleep(5)
            return False, "WAIT"

        elif action in ("finish", "finished"):
            print("   任务完成")
            return True, "FINISH"

        else:
            print(f"   未知动作: {action}")
            return False, f"UNKNOWN({action})"

    except Exception as e:
        print(f"   执行失败: {e}")
        return False, f"ERROR({str(e)})"


def _fallback_parse_json(response_text):
    try:
        start_idx = response_text.find('{')
        end_idx = response_text.rfind('}')
        if start_idx == -1 or end_idx == -1:
            return [], ""
        json_str = response_text[start_idx: end_idx + 1]
        json_str = re.sub(r'//.*', '', json_str)
        json_str = re.sub(r'/\*.*?\*/', '', json_str, flags=re.DOTALL)
        json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
        data = json.loads(json_str)
        thought = data.get("thought", "")
        actions = []
        items = data.get("batch", [data])
        for item in items:
            actions.append({
                "action": item.get("action", ""),
                "point": item.get("point", [0, 0]),
                "text": item.get("text", ""),
                "key": item.get("key", ""),
                "direction": item.get("direction", "down"),
                "summary": item.get("summary", ""),
                "thought": thought,
            })
        return actions, thought
    except Exception:
        return [], ""


# ==========================================
#  预置指令核心循环
# ==========================================
def run_scripted_task(task_config, executor_system_prompt, max_steps=50):
    task_prompt = task_config['description']
    steps = task_config.get('steps', [])

    print(f"   任务启动(SCRIPTED): {task_prompt}")
    print(f"   预置步骤数: {len(steps)}")
    print(f"   屏幕逻辑尺寸: {SCREEN_WIDTH}x{SCREEN_HEIGHT}")
    print(f"   截图物理尺寸: {SCREENSHOT_W}x{SCREENSHOT_H}")
    print(f"   DPI 缩放: X={SCALE_X:.1f} Y={SCALE_Y:.1f}")
    print(f"   Executor 模型: {EXECUTOR_MODEL}")

    if not steps:
        print("   ⚠️  该任务没有预置 steps，退出。请在 tasks_scripted.yaml 中添加 steps 字段。")
        return

    start_time = time.time()
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    save_dir = f"scripted_{timestamp}"
    os.makedirs(save_dir, exist_ok=True)

    log_path = os.path.join(save_dir, "log.txt")
    jsonl_path = os.path.join(save_dir, "dataset.jsonl")
    log_file = open(log_path, "w", encoding="utf-8")
    jsonl_file = open(jsonl_path, "w", encoding="utf-8")
    original_stdout = sys.stdout
    sys.stdout = Tee(original_stdout, log_file)

    print(f"   截图目录: {os.path.abspath(save_dir)}")
    print(f"   日志文件: {os.path.abspath(log_path)}")
    print(f"   数据集文件: {os.path.abspath(jsonl_path)}")
    print("=" * 60)

    print("   请在 3 秒内切换到目标窗口...")
    time.sleep(3)

    # 当前预置步骤指针
    step_idx = 0
    # 当前步骤已执行的次数（用于 max_retries 判断）
    step_retry_count = 0

    try:
        for step in range(max_steps):
            # 所有预置步骤已执行完
            if step_idx >= len(steps):
                print(f"\n   所有 {len(steps)} 条预置指令已执行完毕，任务结束。")
                break

            current_step_cfg = steps[step_idx]
            if isinstance(current_step_cfg, str):
                # 兼容纯字符串格式
                instruction = current_step_cfg
                max_retries = 1
                wait_after = 0
            else:
                instruction = current_step_cfg.get('instruction', '')
                max_retries = current_step_cfg.get('max_retries', 1)
                wait_after = current_step_cfg.get('wait_after', 0)

            print(f"\n=== Step {step + 1}/{max_steps} | 预置步骤 [{step_idx + 1}/{len(steps)}] (重试 {step_retry_count}/{max_retries}) ===")
            print(f"   [指令] {instruction}")

            # 截图
            screenshot = take_screenshot()
            screenshot_path = os.path.join(save_dir, f"step_{step + 1:02d}.jpg")
            try:
                save_img = screenshot.convert('RGB') if screenshot.mode == 'RGBA' else screenshot
                save_img.save(screenshot_path)
            except Exception as e:
                print(f"   截图保存失败: {e}")

            # 预处理截图
            resized_img, base64_img, sr_w, sr_h = preprocess_screenshot(screenshot)
            executor_img = encode_image_to_base64(screenshot) if EXECUTOR_BACKEND == "doubao" else base64_img

            print("   [Executor] 正在生成动作...")
            try:
                resp_executor = client_executor.chat.completions.create(
                    model=EXECUTOR_MODEL,
                    messages=[
                        {"role": "system", "content": executor_system_prompt},
                        {"role": "user", "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{executor_img}"}},
                            {"type": "text", "text": instruction}
                        ]}
                    ],
                    temperature=0.1
                )
                response_text = resp_executor.choices[0].message.content

                print("=" * 60)
                print("   [DEBUG] Executor 原始 content:")
                print(response_text)
                print("=" * 60)

                actions_list, exec_thought = parse_uitars_response(response_text)
                if not actions_list:
                    actions_list, exec_thought = _fallback_parse_json(response_text)

                if not actions_list:
                    print("   无法解析 Executor 输出，跳过本 step")
                    step_retry_count += 1
                    if step_retry_count >= max_retries:
                        print(f"   步骤 [{step_idx + 1}] 达到最大重试次数，跳到下一条预置指令")
                        step_idx += 1
                        step_retry_count = 0
                    continue

                print(f"   Executor Thought: {exec_thought[:100] if exec_thought else '无'}")

                step_logs = []
                task_finished = False
                for idx, action_item in enumerate(actions_list):
                    print(f"  └─ 动作 {idx + 1}/{len(actions_list)}: {action_item.get('summary', action_item.get('action', '?'))}")
                    is_finished, action_desc = execute_action(action_item, sr_w, sr_h)
                    step_logs.append(action_desc)
                    if idx < len(actions_list) - 1:
                        time.sleep(0.8)
                    if is_finished:
                        task_finished = True
                        print("   任务完成！")
                        break

                # 记录数据集
                record = {
                    "step": step + 1,
                    "preset_step_idx": step_idx,
                    "preset_instruction": instruction,
                    "screenshot_current": f"step_{step + 1:02d}.jpg",
                    "screenshot_size": f"{SCREENSHOT_W}x{SCREENSHOT_H}",
                    "smart_resize": f"{sr_w}x{sr_h}",
                    "model_coords": [a.get("point") for a in actions_list],
                    "executed_coords": [a.get("_logical_xy") for a in actions_list],
                    "executor_raw": response_text,
                    "executor_parsed_ok": True,
                    "actions_executed": step_logs,
                    "parse_error": None
                }
                jsonl_file.write(json.dumps(record, ensure_ascii=False) + "\n")
                jsonl_file.flush()

                elapsed = time.time() - start_time
                avg_time = elapsed / (step + 1)
                print(f"   总耗时: {elapsed:.2f}s | 本轮平均: {avg_time:.2f}s")

                if task_finished:
                    return

                # Executor 输出了 finished 以外的动作，认为本步骤执行完毕，推进到下一条预置指令
                step_idx += 1
                step_retry_count = 0

                if wait_after > 0:
                    print(f"   [等待] 步骤后等待 {wait_after}s...")
                    time.sleep(wait_after)

            except Exception as e:
                print(f"   Executor 失败: {e}")
                import traceback
                traceback.print_exc()
                record = {
                    "step": step + 1,
                    "preset_step_idx": step_idx,
                    "preset_instruction": instruction,
                    "screenshot_current": f"step_{step + 1:02d}.jpg",
                    "screenshot_size": f"{SCREENSHOT_W}x{SCREENSHOT_H}",
                    "smart_resize": f"{sr_w}x{sr_h}",
                    "model_coords": None,
                    "executed_coords": None,
                    "executor_raw": "",
                    "executor_parsed_ok": False,
                    "actions_executed": [],
                    "parse_error": str(e)
                }
                jsonl_file.write(json.dumps(record, ensure_ascii=False) + "\n")
                jsonl_file.flush()

            time.sleep(5)

        print("   达到最大步数，流程结束")

    finally:
        sys.stdout = original_stdout
        log_file.close()
        jsonl_file.close()
        print(f"   日志已保存到: {os.path.abspath(log_path)}")
        print(f"   数据集已保存到: {os.path.abspath(jsonl_path)}")


# ================= main =================
def load_config(prompt_file="prompt_uitars.yaml", tasks_file="tasks_scripted.yaml"):
    with open(prompt_file, "r", encoding="utf-8") as f:
        prompts = yaml.safe_load(f)
    with open(tasks_file, "r", encoding="utf-8") as f:
        tasks_config = yaml.safe_load(f)
    return prompts, tasks_config


def list_tasks(tasks_config):
    print("\n   任务列表：")
    print("=" * 60)
    for i, task in enumerate(tasks_config['tasks']):
        status = "OK" if task.get('enabled', True) else "OFF"
        steps_count = len(task.get('steps', []))
        print(f"{status} [{i}] {task['name']} ({steps_count} 步)")
        print(f"    {task['description'][:80]}")
    print("=" * 60)


def find_task(tasks_config, task_selector):
    tasks = tasks_config['tasks']
    try:
        idx = int(task_selector)
        if 0 <= idx < len(tasks):
            return tasks[idx]
    except (ValueError, TypeError):
        pass
    for task in tasks:
        if task['name'] == task_selector:
            return task
    return None


def main():
    parser = argparse.ArgumentParser(description='UI Agent (Scripted - 预置指令模式)')
    parser.add_argument('--task', '-t', type=str, help='任务名称或索引')
    parser.add_argument('--list', '-l', action='store_true', help='列出所有任务')
    parser.add_argument('--prompt', '-p', type=str, default='prompt_uitars.yaml', help='提示词文件路径')
    parser.add_argument('--tasks-file', type=str, default='tasks_scripted.yaml', help='预置任务文件路径')
    args = parser.parse_args()

    print(f"   使用提示词文件: {args.prompt}")
    print(f"   使用任务文件: {args.tasks_file}")
    prompts, tasks_config = load_config(args.prompt, args.tasks_file)

    executor_system_prompt = prompts['executor_prompt']

    if args.list:
        list_tasks(tasks_config)
        return

    if args.task:
        selected_task = find_task(tasks_config, args.task)
        if selected_task:
            print(f"\n   运行任务: {selected_task['name']}")
            print("-" * 60)
            run_scripted_task(
                selected_task,
                executor_system_prompt,
                max_steps=selected_task.get('max_steps', 50)
            )
        else:
            print(f"   未找到任务: {args.task}")
            list_tasks(tasks_config)
    else:
        default_selector = tasks_config.get('default_task', 0)
        selected_task = find_task(tasks_config, default_selector)
        if selected_task:
            run_scripted_task(
                selected_task,
                executor_system_prompt,
                max_steps=selected_task.get('max_steps', 50)
            )
        else:
            print("   默认任务未配置")
            list_tasks(tasks_config)


if __name__ == "__main__":
    main()
