"""
debug_main.py - 调试版：打印并记录 Analyst/Executor 模型的原始输出
用法：uv run python debug_main.py --task 0

修复说明：
- UI-TARS-1.5-7B (Qwen2.5-VL) 输出的是**绝对像素坐标**(相对于 smart_resize 后的图片)
- 需要先对截图做 smart_resize 预处理，再发给模型
- 坐标转换：model_coord / smart_resize_dim * original_dim
- Executor 提示词使用 UI-TARS 官方格式 (Thought/Action)
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

# ================= 配置（与 agent.py 保持一致）=================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-3-pro-preview"
client_analyst = OpenAI(
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    api_key=GEMINI_API_KEY
)

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
SCREENSHOT_W = _ss.size[0]  # 截图物理像素宽度
SCREENSHOT_H = _ss.size[1]  # 截图物理像素高度
SCALE_X = SCREENSHOT_W / SCREEN_WIDTH  # 物理像素 / 逻辑像素，Retina = 2.0
SCALE_Y = SCREENSHOT_H / SCREEN_HEIGHT
del _ss
pyautogui.FAILSAFE = True
IS_MAC = platform.system() == "Darwin"
CMD_KEY = 'command' if IS_MAC else 'ctrl'


# ================= 录屏 =================
class ScreenRecorder:
    """
    使用 ffmpeg 录制屏幕。
    macOS: avfoundation (screen index 1 为主屏幕)
    Linux: x11grab
    录制文件保存到 output_path (mp4)。
    依赖：系统需安装 ffmpeg (brew install ffmpeg / apt install ffmpeg)
    """

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
            # 先列出设备，找到 "Capture screen 0" 的索引
            screen_idx = self._find_mac_screen_index()
            cmd = [
                "ffmpeg", "-y",
                "-f", "avfoundation",
                "-capture_cursor", "1",
                "-capture_mouse_clicks", "1",
                "-i", f"{screen_idx}",
                "-r", str(self.fps),
                "-c:v", "libx264",
                "-crf", "28",
                "-preset", "ultrafast",
                "-pix_fmt", "yuv420p",
                self.output_path,
            ]
        else:
            display = os.environ.get("DISPLAY", ":0")
            cmd = [
                "ffmpeg", "-y",
                "-f", "x11grab",
                "-r", str(self.fps),
                "-s", f"{SCREEN_WIDTH}x{SCREEN_HEIGHT}",
                "-i", f"{display}.0+0,0",
                "-c:v", "libx264",
                "-crf", "28",
                "-preset", "ultrafast",
                "-pix_fmt", "yuv420p",
                self.output_path,
            ]

        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"   [录屏] 开始录制 → {self.output_path}")

    def stop(self):
        if not self.process:
            return
        try:
            # 发送 'q' 让 ffmpeg 优雅退出
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
            print("   [录屏] 录制文件未生成（可能 ffmpeg 权限不足或设备索引有误）")

    def _find_mac_screen_index(self):
        """列出 avfoundation 设备，找到第一个 Capture screen 的索引，默认返回 1。"""
        try:
            result = subprocess.run(
                ["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                capture_output=True, text=True, timeout=5
            )
            # avfoundation 设备列表在 stderr
            output = result.stderr
            for line in output.splitlines():
                if "Capture screen" in line or "screen" in line.lower():
                    m = re.search(r'\[(\d+)\]', line)
                    if m:
                        return m.group(1)
        except Exception:
            pass
        return "1"  # 默认主屏幕


# ================= smart_resize (来自 UI-TARS 官方) =================
IMAGE_FACTOR = 28
MIN_PIXELS = 100 * 28 * 28       # 78,400
MAX_PIXELS = 16384 * 28 * 28     # 12,845,056


def round_by_factor(number, factor):
    return round(number / factor) * factor


def ceil_by_factor(number, factor):
    return math.ceil(number / factor) * factor


def floor_by_factor(number, factor):
    return math.floor(number / factor) * factor


def smart_resize(height, width, factor=IMAGE_FACTOR,
                 min_pixels=MIN_PIXELS, max_pixels=MAX_PIXELS):
    """
    将图片尺寸调整为 factor(28) 的倍数，保持纵横比，
    像素总量控制在 [min_pixels, max_pixels] 范围内。
    这是 Qwen2.5-VL / UI-TARS-1.5 的官方预处理算法。
    """
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


# ================= Tee：同时输出到终端和文件 =================
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
    """
    对截图进行 smart_resize 预处理：
    1. 计算 smart_resize 目标尺寸 (28的倍数)
    2. Resize 图片到目标尺寸
    3. 返回 (resized_image, base64, smart_resize_w, smart_resize_h)

    坐标转换公式：
      screen_x = (model_x / smart_resize_w) * original_w / SCALE_X
      screen_y = (model_y / smart_resize_h) * original_h / SCALE_Y
    """
    orig_w, orig_h = screenshot.size  # 物理像素尺寸
    # smart_resize 参数顺序: (height, width)
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
    """
    将 UI-TARS-1.5 本地模型输出的绝对像素坐标转换为 pyautogui 逻辑坐标。
    适用于: local 后端 (UI-TARS-1.5-7B)

    流程：
    1. model_coord → 归一化 (除以 smart_resize 尺寸)
    2. 归一化 → 物理像素 (乘以原始截图尺寸)
    3. 物理像素 → 逻辑坐标 (除以 DPI 缩放比)
    """
    # 归一化到 0~1
    norm_x = model_x / sr_w
    norm_y = model_y / sr_h

    # 映射到物理像素
    phys_x = norm_x * SCREENSHOT_W
    phys_y = norm_y * SCREENSHOT_H

    # 转换为逻辑坐标 (pyautogui 使用逻辑坐标)
    logical_x = int(max(0, min(phys_x / SCALE_X, SCREEN_WIDTH - 1)))
    logical_y = int(max(0, min(phys_y / SCALE_Y, SCREEN_HEIGHT - 1)))

    print(f"   [坐标/local] 模型输出({model_x},{model_y}) / resize({sr_w},{sr_h})"
          f" → 归一化({norm_x:.3f},{norm_y:.3f})"
          f" → 物理({phys_x:.0f},{phys_y:.0f})"
          f" → 逻辑({logical_x},{logical_y})")
    return logical_x, logical_y


def convert_doubao_coord_to_logical(model_x, model_y):
    """
    将 doubao-1-5-ui-tars 输出的坐标转换为 pyautogui 逻辑坐标。
    适用于: doubao 后端 (doubao-1-5-ui-tars-250428)

    doubao 输出 0-1000 归一化坐标（x,y 均在 [0,1000]）。
    若超过 1000 则视为物理像素，按 DPI 缩放比转换。
    """
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
    """按当前 EXECUTOR_BACKEND 路由坐标转换"""
    if EXECUTOR_BACKEND == "doubao":
        return convert_doubao_coord_to_logical(model_x, model_y)
    else:
        return convert_uitars_coord_to_logical(model_x, model_y, sr_w, sr_h)


# ================= UI-TARS 原生输出解析 =================
def parse_uitars_response(response_text):
    """
    解析 UI-TARS 原生输出格式：
        Thought: ...
        Action: click(start_box='(x,y)')

    返回统一的 action 列表，每个 action 是 dict:
      {"action": "click", "point": [x,y], "text": "", "key": "", "thought": "..."}
    """
    text = response_text.strip()

    # 提取 Thought
    thought = ""
    thought_match = re.search(r'Thought:\s*(.+?)(?=\s*Action:|$)', text, re.DOTALL)
    if thought_match:
        thought = thought_match.group(1).strip()

    # 提取 Action 部分
    action_match = re.search(r'Action:\s*(.+)', text, re.DOTALL)
    if not action_match:
        print(f"   [解析] 未找到 Action 字段")
        return [], thought

    action_str = action_match.group(1).strip()

    # 处理 <point>x y</point> 格式 → start_box='(x,y)' 格式
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
    # 去掉 box tokens
    action_str = action_str.replace("<|box_start|>", "").replace("<|box_end|>", "")

    # 分割多个 action：官方格式用 ")\n\n" 分隔（双空行），兼容单换行
    # 优先按官方 ")\n\n" 分割，若无双换行则回退到单换行 lookahead
    if ")\n\n" in action_str:
        raw_actions = action_str.split(")\n\n")
    else:
        raw_actions = re.split(r'\)\s*\n(?=\s*\w+\s*\()', action_str)
    # 补回右括号（split 把它去掉了）
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
    """解析单个 UI-TARS action 字符串，返回统一格式的 dict"""

    # 使用 ast 安全解析函数调用
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
        # 回退：用正则解析
        func_match = re.match(r'(\w+)\s*\(', action_str)
        if not func_match:
            return None
        func_name = func_match.group(1)
        kwargs = {}
        # 提取 start_box
        box_match = re.search(r"start_box='?\(?(\d+)\s*,\s*(\d+)\)?'?", action_str)
        if box_match:
            kwargs['start_box'] = f"({box_match.group(1)},{box_match.group(2)})"
        # 提取 end_box
        end_match = re.search(r"end_box='?\(?(\d+)\s*,\s*(\d+)\)?'?", action_str)
        if end_match:
            kwargs['end_box'] = f"({end_match.group(1)},{end_match.group(2)})"
        # 提取 content
        content_match = re.search(r"content='(.*?)'", action_str)
        if content_match:
            kwargs['content'] = content_match.group(1)
        # 提取 key
        key_match = re.search(r"key='(.*?)'", action_str)
        if key_match:
            kwargs['key'] = key_match.group(1)
        # 提取 direction
        dir_match = re.search(r"direction='(.*?)'", action_str)
        if dir_match:
            kwargs['direction'] = dir_match.group(1)

    # 提取坐标
    point = [0, 0]
    for box_key in ['start_box', 'point']:
        if box_key in kwargs:
            nums = re.findall(r'\d+', str(kwargs[box_key]))
            if len(nums) >= 2:
                point = [int(nums[0]), int(nums[1])]
            break

    # 映射到统一的 action 格式
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

    # drag 需要 end_point
    if func_name == 'drag' and 'end_box' in kwargs:
        end_nums = re.findall(r'\d+', str(kwargs['end_box']))
        if len(end_nums) >= 2:
            result['end_point'] = [int(end_nums[0]), int(end_nums[1])]

    return result


# ================= 执行动作 =================
def execute_action(data, sr_w, sr_h):
    """执行单个动作，坐标使用 UI-TARS 坐标转换"""
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
            # 处理 \n 结尾 → 输入后按回车
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


def check_repeated_actions(history_log, min_repeats=3):
    if len(history_log) < min_repeats: return False, 0, ""
    actions = []
    for entry in history_log:
        if "|" in entry:
            try:
                part = entry.split("|")[0].split(":", 1)[1].strip()
                actions.append(part)
            except:
                continue

    if not actions: return False, 0, ""
    last_action = actions[-1]
    count = 0
    for action in reversed(actions):
        if action == last_action:
            count += 1
        else:
            break
    if count >= min_repeats:
        action_type = last_action.split('(')[0]
        return True, count, action_type
    return False, 0, ""


# ==========================================
#  调试版核心循环
# ==========================================
def run_agent_task_debug(task_prompt, prompts, max_steps=50):
    print(f"   任务启动(DEBUG): {task_prompt}")
    print(f"   屏幕逻辑尺寸: {SCREEN_WIDTH}x{SCREEN_HEIGHT}")
    print(f"   截图物理尺寸: {SCREENSHOT_W}x{SCREENSHOT_H}")
    print(f"   DPI 缩放: X={SCALE_X:.1f} Y={SCALE_Y:.1f}")
    start_time = time.time()

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    save_dir = f"screenshots_{timestamp}"
    os.makedirs(save_dir, exist_ok=True)

    log_path = os.path.join(save_dir, "debug_log.txt")
    jsonl_path = os.path.join(save_dir, "dataset.jsonl")
    recording_path = os.path.join(save_dir, "recording.mp4")
    log_file = open(log_path, "w", encoding="utf-8")
    jsonl_file = open(jsonl_path, "w", encoding="utf-8")
    original_stdout = sys.stdout
    sys.stdout = Tee(original_stdout, log_file)

    print(f"   截图目录: {os.path.abspath(save_dir)}")
    print(f"   日志文件: {os.path.abspath(log_path)}")
    print(f"   数据集文件: {os.path.abspath(jsonl_path)}")
    print(f"   录屏文件: {os.path.abspath(recording_path)}")
    print(f"   Analyst 模型: {GEMINI_MODEL}")
    print(f"   Executor 模型: {EXECUTOR_MODEL}")
    print("=" * 60)

    # 启动录屏（已关闭）
    # recorder = ScreenRecorder(output_path=os.path.abspath(recording_path), fps=10)
    # recorder.start()

    print("   请在 3 秒内切换到目标窗口...")
    time.sleep(3)
    history_log = []
    last_step_img = None

    # 构造 UI-TARS 官方系统提示词 (executor)
    executor_system_prompt = prompts['executor_prompt']

    try:
        for step in range(max_steps):
            print(f"\n=== Step {step + 1}/{max_steps} ===")

            screenshot = take_screenshot()
            screenshot_path = os.path.join(save_dir, f"step_{step + 1:02d}.jpg")

            try:
                if screenshot.mode == 'RGBA':
                    save_img = screenshot.convert('RGB')
                else:
                    save_img = screenshot
                save_img.save(screenshot_path)
            except Exception as e:
                print(f"   截图保存失败: {e}")

            # ===== 关键：对截图做 smart_resize 预处理 =====
            resized_img, base64_img, sr_w, sr_h = preprocess_screenshot(screenshot)

            # 同时保留原始截图的 base64 给 Analyst（Analyst 不需要 smart_resize）
            analyst_base64 = encode_image_to_base64(screenshot)

            is_repeated, repeat_count, repeated_action = check_repeated_actions(history_log, min_repeats=3)
            warning_str = ""
            if repeated_action == "SCROLL_DOWN" and repeat_count >= 5:
                warning_str = prompts['warnings']['scroll_down'].format(count=repeat_count)
                print(warning_str)
            elif is_repeated and repeat_count >= 3:
                warning_str = prompts['warnings']['repeated'].format(count=repeat_count, action=repeated_action)
                print(f"   检测到重复操作: {repeated_action}")

            if history_log:
                last_action_str = history_log[-1]
                prior_history_str = "\n".join(history_log[-11:-1]) if len(history_log) > 1 else "无 (这是第一步)"
            else:
                last_action_str = "无 (初始状态)"
                prior_history_str = "无"

            context_str = prompts['task_template'].format(
                task=task_prompt,
                prior_history=prior_history_str,
                last_action=last_action_str,
                warning=warning_str
            )

            user_content = []
            analyst_img_paths = []

            if last_step_img is not None:
                prev_path = os.path.join(save_dir, f"step_{step:02d}.jpg")
                analyst_img_paths.append(f"图1(Before): {os.path.abspath(prev_path)}")
                user_content.append({"type": "text", "text": "【图1：动作前 (Before Action)】(这是你上一步操作前的样子)(请以此判断上一步组件是否处于原始状态)"})
                user_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{last_step_img}"}})
                user_content.append({"type": "text", "text": "【图2：当前现状 (Current State)(当前最新截图)\n(请以此进行 [动作校验] 和 [差距] 分析)"})
            else:
                user_content.append({"type": "text", "text": "【图2：初始状态】"})

            analyst_img_paths.append(f"图2(Current): {os.path.abspath(screenshot_path)}")
            user_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{analyst_base64}"}})
            user_content.append({"type": "text",
                                 "text": context_str + "\n\n请对比两张图片（如有）来分析上一步是否执行完成，根据【图2】进行下一步操作（【图1】已经不存在了）【动作校验】，输出【动作校验结果】、【差距分析】和【执行指令】。"})

            # ===== DEBUG: Analyst 输入图片路径 =====
            print("─" * 40)
            print("   [DEBUG] Analyst 输入图片:")
            for p in analyst_img_paths:
                print(f"   {p}")

            print("   [Analyst] 正在分析...")
            try:
                resp_analyst = client_analyst.chat.completions.create(
                    model=GEMINI_MODEL,
                    messages=[
                        {"role": "system", "content": prompts['analyst_prompt']},
                        {"role": "user", "content": user_content}
                    ],
                    temperature=0.1
                )
                analysis_plan = resp_analyst.choices[0].message.content

                # ===== DEBUG: Analyst 原始输出 =====
                print("=" * 60)
                print("   [DEBUG] Analyst 原始 content:")
                print(analysis_plan)
                print("=" * 60)

            except Exception as e:
                print(f"   Analyst 失败: {e}")
                continue

            last_step_img = analyst_base64

            print("   [Executor] 正在生成动作...")
            try:
                # 构造给 Executor 的用户消息
                # UI-TARS 官方格式：截图 + 任务指令
                # 只提取 [👉 指令] 部分，不暴露终极目标给 Executor
                instr_match = re.search(
                    r'\*\*\[👉 指令\]\*\*[：:]\s*(.+?)(?=\*\*\[|$)',
                    analysis_plan, re.DOTALL
                )
                extracted_instruction = instr_match.group(1).strip() if instr_match else analysis_plan
                executor_instruction = extracted_instruction
                if warning_str:
                    executor_instruction += f"\n\n{warning_str}"

                # doubao 使用原始截图（0-1000 归一化坐标，与分辨率无关）
                # local (UI-TARS-1.5-7B) 使用 smart_resize 后的图（输出绝对像素坐标）
                executor_img = analyst_base64 if EXECUTOR_BACKEND == "doubao" else base64_img

                resp_executor = client_executor.chat.completions.create(
                    model=EXECUTOR_MODEL,
                    messages=[
                        {"role": "system", "content": executor_system_prompt},
                        {"role": "user", "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{executor_img}"}},
                            {"type": "text", "text": executor_instruction}
                        ]}
                    ],
                    temperature=0.1
                )
                response_text = resp_executor.choices[0].message.content

                # ===== DEBUG: Executor 原始 content =====
                print("=" * 60)
                print("   [DEBUG] Executor 原始 content:")
                print(response_text)
                print("=" * 60)

                try:
                    # 解析 UI-TARS 原生格式的输出
                    actions_list, exec_thought = parse_uitars_response(response_text)

                    if not actions_list:
                        # 回退：尝试解析 JSON 格式 (兼容旧格式)
                        actions_list, exec_thought = _fallback_parse_json(response_text)

                    if not actions_list:
                        print("   无法解析 Executor 输出")
                        history_log.append(f"Step {step + 1}: ERROR(PARSE)")
                        continue

                    print(f"   Executor Thought: {exec_thought[:100] if exec_thought else '无'}")

                    step_logs = []
                    for idx, action_item in enumerate(actions_list):
                        print(f"  └─ 动作 {idx + 1}/{len(actions_list)}: {action_item.get('summary', action_item.get('action', '?'))}")
                        is_finished, action_desc = execute_action(action_item, sr_w, sr_h)
                        step_logs.append(action_desc)

                        if idx < len(actions_list) - 1:
                            time.sleep(0.8)

                        if is_finished:
                            print("   任务完成！")
                            return

                    full_analysis_log = analysis_plan.replace('\n', '  ').strip()
                    combined_actions = " -> ".join(step_logs)
                    log_entry = f"Step {step + 1}: [{combined_actions}] | {full_analysis_log}"
                    history_log.append(log_entry)

                    model_coords = [a.get("point") for a in actions_list]
                    executed_coords = [a.get("_logical_xy") for a in actions_list]

                    record = {
                        "step": step + 1,
                        "screenshot_before": f"step_{step:02d}.jpg" if step > 0 else None,
                        "screenshot_current": f"step_{step + 1:02d}.jpg",
                        "screenshot_size": f"{SCREENSHOT_W}x{SCREENSHOT_H}",
                        "smart_resize": f"{sr_w}x{sr_h}",
                        "model_coords": model_coords,
                        "executed_coords": executed_coords,
                        "analyst_output": analysis_plan,
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

                except Exception as e:
                    print(f"   解析失败: {e}")
                    import traceback
                    traceback.print_exc()
                    history_log.append(f"Step {step + 1}: ERROR(PARSE: {e})")
                    record = {
                        "step": step + 1,
                        "screenshot_before": f"step_{step:02d}.jpg" if step > 0 else None,
                        "screenshot_current": f"step_{step + 1:02d}.jpg",
                        "screenshot_size": f"{SCREENSHOT_W}x{SCREENSHOT_H}",
                        "smart_resize": f"{sr_w}x{sr_h}",
                        "model_coords": None,
                        "executed_coords": None,
                        "analyst_output": analysis_plan,
                        "executor_raw": response_text,
                        "executor_parsed_ok": False,
                        "actions_executed": [],
                        "parse_error": str(e)
                    }
                    jsonl_file.write(json.dumps(record, ensure_ascii=False) + "\n")
                    jsonl_file.flush()

            except Exception as e:
                print(f"   Executor 失败: {e}")
                history_log.append(f"Step {step + 1}: ERROR({str(e)})")

            time.sleep(5)

        print("   达到最大步数，流程结束")

    finally:
        # recorder.stop()
        sys.stdout = original_stdout
        log_file.close()
        jsonl_file.close()
        print(f"   日志已保存到: {os.path.abspath(log_path)}")
        print(f"   数据集已保存到: {os.path.abspath(jsonl_path)}")
        print(f"   录屏已保存到: {os.path.abspath(recording_path)}")


def _fallback_parse_json(response_text):
    """回退：兼容旧的 JSON 格式输出"""
    try:
        start_idx = response_text.find('{')
        end_idx = response_text.rfind('}')
        if start_idx == -1 or end_idx == -1:
            return [], ""
        json_str = response_text[start_idx: end_idx + 1]
        # 清理 JSON
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


# ================= main =================
def load_config(prompt_file="prompt.yaml"):
    with open(prompt_file, "r", encoding="utf-8") as f:
        prompts = yaml.safe_load(f)
    with open("tasks.yaml", "r", encoding="utf-8") as f:
        tasks_config = yaml.safe_load(f)
    return prompts, tasks_config


def list_tasks(tasks_config):
    print("\n   任务列表：")
    print("=" * 60)
    for i, task in enumerate(tasks_config['tasks']):
        status = "OK" if task.get('enabled', True) else "OFF"
        print(f"{status} [{i}] {task['name']}")
        print(f"    {task['description'][:80]}...")
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
    parser = argparse.ArgumentParser(description='UI Agent (DEBUG)')
    parser.add_argument('--task', '-t', type=str, help='任务名称或索引')
    parser.add_argument('--list', '-l', action='store_true', help='列出所有任务')
    parser.add_argument('--verbose', '-v', action='store_true', help='显示详细信息')
    parser.add_argument('--prompt', '-p', type=str, default='prompt_uitars.yaml', help='提示词文件路径')
    args = parser.parse_args()

    print(f"   使用提示词文件: {args.prompt}")
    prompts, tasks_config = load_config(args.prompt)

    if args.list:
        list_tasks(tasks_config)
        return

    if args.task:
        selected_task = find_task(tasks_config, args.task)
        if selected_task:
            print(f"\n   运行任务: {selected_task['name']}")
            if args.verbose:
                print(f"   任务描述: {selected_task['description']}")
            print("-" * 60)
            run_agent_task_debug(
                selected_task['description'],
                prompts,
                max_steps=selected_task.get('max_steps', 50)
            )
        else:
            print(f"   未找到任务: {args.task}")
            list_tasks(tasks_config)
    else:
        default_selector = tasks_config.get('default_task', 0)
        selected_task = find_task(tasks_config, default_selector)
        if selected_task:
            run_agent_task_debug(
                selected_task['description'],
                prompts,
                max_steps=selected_task.get('max_steps', 50)
            )
        else:
            print("   默认任务未配置")
            list_tasks(tasks_config)


if __name__ == "__main__":
    main()
