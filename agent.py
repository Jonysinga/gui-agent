import os
import time
import json
import re
import base64
import platform
import yaml
from io import BytesIO
from openai import OpenAI
import pyautogui
import pyperclip
from PIL import Image

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ================= 配置区域 =================
# API Key 从环境变量读取（见 .env.example）。请勿将真实密钥硬编码进源码。
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-pro-preview")
client_analyst = OpenAI(
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    api_key=GEMINI_API_KEY
)


# ===== 豆包模型（已注释）=====
# DOUBAO_API_KEY = os.getenv("DOUBAO_API_KEY", "")
# EXECUTOR_MODEL = "doubao-1-5-ui-tars-250428"
# client_executor = OpenAI(
#     base_url="https://ark.cn-beijing.volces.com/api/v3",
#     api_key=DOUBAO_API_KEY
# )

# ===== 执行器模型配置 =====
# 可选后端: "UI-TARS-1.5-7B" / "doubao" / "GUI-Owl-32B-Think" / "GUI-Owl-32B-Instruct"
#           / "GUI-Owl-8B-Think" / "GUI-Owl-8B-Instruct" / "GUI-Owl-4B-Instruct" / "GUI-Owl-2B-Instruct"
EXECUTOR_BACKEND = "UI-TARS-1.5-7B"

_EXECUTOR_CONFIGS = {
    "UI-TARS-1.5-7B": {
        "model": "/mnt/disk3/models/UI-TARS-1.5-7B",
        "base_url": "http://115.190.215.236:8004/v1",
        "api_key": "EMPTY",
    },
    "doubao": {
        "model": "doubao-1-5-ui-tars-250428",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api_key": os.getenv("DOUBAO_API_KEY", ""),
    },
    "GUI-Owl-32B-Think": {
        "model": "/mnt/disk1/models/GUI-Owl-1.5-32B-Think",
        "base_url": "http://115.190.234.37:8000/v1",
        "api_key": "EMPTY",
    },
    "GUI-Owl-32B-Instruct": {
        "model": "/mnt/disk1/models/GUI-Owl-1.5-32B-Instruct",
        "base_url": "http://115.190.234.37:8007/v1",
        "api_key": "EMPTY",
    },
    "GUI-Owl-8B-Think": {
        "model": "/mnt/disk1/models/GUI-Owl-1.5-8B-Think",
        "base_url": "http://115.190.234.37:8001/v1",
        "api_key": "EMPTY",
    },
    "GUI-Owl-8B-Instruct": {
        "model": "/mnt/disk1/models/GUI-Owl-1.5-8B-Instruct",
        "base_url": "http://115.190.234.37:8003/v1",
        "api_key": "EMPTY",
    },
    "GUI-Owl-4B-Instruct": {
        "model": "/mnt/disk1/models/GUI-Owl-1.5-4B-Instruct",
        "base_url": "http://115.190.234.37:8006/v1",
        "api_key": "EMPTY",
    },
    "GUI-Owl-2B-Instruct": {
        "model": "/mnt/disk1/models/GUI-Owl-1.5-2B-Instruct",
        "base_url": "http://115.190.234.37:8005/v1",
        "api_key": "EMPTY",
    },
}

_cfg = _EXECUTOR_CONFIGS[EXECUTOR_BACKEND]
EXECUTOR_MODEL = _cfg["model"]
client_executor = OpenAI(base_url=_cfg["base_url"], api_key=_cfg["api_key"])

SCREEN_WIDTH, SCREEN_HEIGHT = pyautogui.size()
_ss = pyautogui.screenshot()
SCALE_X = _ss.size[0] / SCREEN_WIDTH  # 物理像素 / 逻辑像素，Retina = 2.0
SCALE_Y = _ss.size[1] / SCREEN_HEIGHT
del _ss
pyautogui.FAILSAFE = True
IS_MAC = platform.system() == "Darwin"
CMD_KEY = 'command' if IS_MAC else 'ctrl'


# ================= 工具函数 =================
def load_prompts(filepath="prompt.yaml"):
    with open(filepath, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def encode_image_to_base64(image):
    if image.mode == 'RGBA':
        image = image.convert('RGB')
    buffered = BytesIO()
    image.save(buffered, format="JPEG", quality=100)
    return base64.b64encode(buffered.getvalue()).decode('utf-8')


def take_screenshot():
    return pyautogui.screenshot()


def normalize_point_ui_tars(point):
    """UI-TARS-1.5 坐标转换: 支持归一化坐标和物理像素坐标"""
    px, py = point[0], point[1]
    if px <= 1000 and py <= 1000:
        x = int(px / 1000 * SCREEN_WIDTH)
        y = int(py / 1000 * SCREEN_HEIGHT)
        coord_mode = "norm"
    else:
        x = int(max(0, min(px / SCALE_X, SCREEN_WIDTH - 1)))
        y = int(max(0, min(py / SCALE_Y, SCREEN_HEIGHT - 1)))
        coord_mode = f"pixel(scale={SCALE_X:.1f}x)"
    print(f"   [坐标/{EXECUTOR_BACKEND}] 原始{point} ({coord_mode}) → 逻辑({x}, {y})")
    return x, y


def normalize_point_gui_owl(point):
    """GUI-Owl 系列坐标转换: 输出 0-1000 归一化坐标（与豆包相同）"""
    px, py = point[0], point[1]
    x = int(px / 1000 * SCREEN_WIDTH)
    y = int(py / 1000 * SCREEN_HEIGHT)
    print(f"   [坐标/{EXECUTOR_BACKEND}] 原始{point} (norm 0-1000) → 逻辑({x}, {y})")
    return x, y


def normalize_point(point):
    """将模型输出的坐标统一转换为 pyautogui 逻辑坐标。
    - UI-TARS-1.5: 归一化坐标 (0~1000) 或物理像素坐标 (>1000)
    - GUI-Owl 系列: 归一化坐标 (0~1000)
    - doubao: 归一化坐标 (0~1000)
    """
    # GUI-Owl 系列模型使用 0-1000 归一化坐标
    if EXECUTOR_BACKEND in ["GUI-Owl-32B-Think", "GUI-Owl-32B-Instruct",
                            "GUI-Owl-8B-Think", "GUI-Owl-8B-Instruct",
                            "GUI-Owl-4B-Instruct", "GUI-Owl-2B-Instruct"]:
        return normalize_point_gui_owl(point)
    else:
        return normalize_point_ui_tars(point)


def execute_action(data):
    """执行动作 - 返回 (是否结束, 动作简描述)"""
    action = data.get("action")
    point = data.get("point", [0, 0])
    text = data.get("text", "")
    key = data.get("key", "")

    try:
        if action == "click":
            x, y = normalize_point(point)
            # 边缘防抖
            if y > SCREEN_HEIGHT * 0.9:
                print(f"⚠️ 边缘检测: Y={y} -> 修正为 {y - 50}")
                y -= 50
            print(f"🖱️ 单击: ({x}, {y})")
            pyautogui.moveTo(x, y, duration=0.5)
            pyautogui.click()
            return False, f"CLICK(point={point})"

        elif action == "double_click":
            x, y = normalize_point(point)
            print(f"🖱️🖱️ 双击: ({x}, {y})")
            pyautogui.moveTo(x, y, duration=0.5)
            pyautogui.doubleClick()
            return False, f"DOUBLE_CLICK(point={point})"

        elif action == "type":
            if point and point != [0, 0]:
                x, y = normalize_point(point)
                pyautogui.moveTo(x, y, duration=0.3)
                pyautogui.click()
                time.sleep(0.3)
            print(f"📋 输入: {text}")
            pyperclip.copy(text)
            time.sleep(0.3)
            pyautogui.hotkey(CMD_KEY, 'a')
            time.sleep(0.1)
            pyautogui.hotkey(CMD_KEY, 'v')
            time.sleep(0.5)
            return False, f"TYPE(text='{text}')"

        elif action == "press":
            print(f"⌨️ 按键: {key}")
            if key == 'enter': key = 'return' if IS_MAC else 'enter'
            pyautogui.press(key)
            return False, f"PRESS(key='{key}')"

        elif action == "scroll_down":
            print("📜 下滚")
            pyautogui.scroll(-10 if IS_MAC else -900)
            return False, "SCROLL_DOWN"

        elif action == "scroll_up":
            print("📜 上滚")
            pyautogui.scroll(10 if IS_MAC else 1500)
            return False, "SCROLL_UP"

        elif action == "finish":
            print("✅ 任务完成")
            return True, "FINISH"
        else:
            return False, f"UNKNOWN({action})"
    except Exception as e:
        print(f"❌ 执行失败: {e}")
        return False, f"ERROR({str(e)})"


def check_repeated_actions(history_log, min_repeats=3):
    if len(history_log) < min_repeats: return False, 0, ""
    actions = []
    for entry in history_log:
        if "|" in entry:
            try:
                # 提取完整动作描述，例如 "CLICK(point=[100, 200])"
                # 不再移除括号内的参数，这样能区分不同位置的点击
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
        # 提取动作类型用于显示警告（如 CLICK）
        action_type = last_action.split('(')[0]
        return True, count, action_type
    return False, 0, ""


# ==========================================
# 🔥 核心：双步循环逻辑 (Analyst -> Executor)
# ==========================================
def run_agent_task(task_prompt, prompts, max_steps=50):
    print(f"🚀 任务启动: {task_prompt}")
    start_time = time.time()  # ⏱️ 计时开始

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    save_dir = f"screenshots_{timestamp}"
    os.makedirs(save_dir, exist_ok=True)

    print("⚠️ 请在 3 秒内切换到目标窗口...")
    time.sleep(3)
    history_log = []
    # 🔥 新增：用于存上一轮的截图（接力棒）
    last_step_img = None
    for step in range(max_steps):
        print(f"\n=== Step {step + 1}/{max_steps} ===")

        screenshot = take_screenshot()

        # 🛠️ 截图保存 (RGB模式)
        try:
            if screenshot.mode == 'RGBA':
                save_img = screenshot.convert('RGB')
            else:
                save_img = screenshot
            save_img.save(os.path.join(save_dir, f"step_{step + 1:02d}.jpg"))
        except Exception as e:
            print(f"❌ 截图保存失败: {e}")

        base64_img = encode_image_to_base64(screenshot)

        # 1. 准备上下文
        is_repeated, repeat_count, repeated_action = check_repeated_actions(history_log, min_repeats=3)
        warning_str = ""
        if repeated_action == "SCROLL_DOWN" and repeat_count >= 5:
            warning_str = prompts['warnings']['scroll_down'].format(count=repeat_count)
            print(warning_str)
        elif is_repeated and repeat_count >= 3:
            warning_str = prompts['warnings']['repeated'].format(count=repeat_count, action=repeated_action)
            print(f"⚠️ 检测到重复操作: {repeated_action}")

        if history_log:
            # 取最后一条作为“待验证动作”
            last_action_str = history_log[-1]

            # 取最后一条之前的内容作为“背景上下文” (保留最近 10 条，避免太长)
            prior_history_str = "\n".join(history_log[-11:-1]) if len(history_log) > 1 else "无 (这是第一步)"
        else:
            last_action_str = "无 (初始状态)"
            prior_history_str = "无"

            # 🔥 DEBUG: 确认切割是否正确
            # print(f"🔍 DEBUG - Last Action: {last_action_str}")

            # 注入新的模板变量
        context_str = prompts['task_template'].format(
            task=task_prompt,
            prior_history=prior_history_str,  # 👈 以前的
            last_action=last_action_str,  # 👈 刚做的 (动作校验对象)
            warning=warning_str
        )
        # 🔥🔥🔥 核心修改：组装双图消息 🔥🔥🔥
        user_content = []

        # 1. 如果有上一轮的图（代表“动作前”），先塞进去
        if last_step_img is not None:
            user_content.append({"type": "text", "text": "【图1：动作前 (Before Action)】(这是你上一步操作前的样子)(请以此判断上一步组件是否处于原始状态)"})
            user_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{last_step_img}"}})
            user_content.append({"type": "text", "text": "【图2：当前现状 (Current State)(当前最新截图)\n(请以此进行 [🔍 动作校验] 和 [📉 差距] 分析)"})
        else:
            user_content.append({"type": "text", "text": "【图2：初始状态】"})

        # 2. 塞入当前这一轮的图
        user_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}})

        # 3. 塞入文本 Prompt
        user_content.append({"type": "text",
                             "text": context_str + "\n\n请对比两张图片（如有）来分析上一步是否执行完成，根据【图2】进行下一步操作（【图1】已经不存在了）【动作校验】，输出【动作校验结果】、【差距分析】和【执行指令】。"})

        # 2. Analyst (分析)
        print("🧠 [Analyst] 正在分析...")
        try:
            resp_analyst = client_analyst.chat.completions.create(
                model=GEMINI_MODEL,
                messages=[
                    {"role": "system", "content": prompts['analyst_prompt']},
                    {"role": "user", "content":user_content}
                ],
                temperature=0.1
            )
            analysis_plan = resp_analyst.choices[0].message.content
            print(f"📋 分析报告: \n{analysis_plan}")
            print("-" * 30)
        except Exception as e:
            print(f"❌ Analyst 失败: {e}")
            continue
        last_step_img = base64_img
        # 3. Executor (执行)
        print("🖐️ [Executor] 正在生成动作...")
        try:
            executor_input = f"""
            【历史信息】
            {context_str}
            ================================
            【上级指令 (Analyst Plan)】
            {analysis_plan}
            ================================
            请根据上级指令，生成对应的 JSON 动作。
            """

            resp_executor = client_executor.chat.completions.create(
                model=EXECUTOR_MODEL,
                messages=[
                    {"role": "system", "content": prompts['executor_prompt']},
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}},
                        {"type": "text", "text": executor_input}
                    ]}
                ],
                temperature=0.1
            )
            response_text = resp_executor.choices[0].message.content

            try:
                # 1. 提取 JSON
                start_idx = response_text.find('{')
                end_idx = response_text.rfind('}')
                if start_idx != -1 and end_idx != -1:
                    json_str = response_text[start_idx: end_idx + 1]
                else:
                    raise ValueError("未找到 JSON")

                # 2. 清洗 JSON (坐标、逗号、注释)
                def sanitize_point(match):
                    content = match.group(1)
                    nums = re.findall(r"\d+", content)
                    if len(nums) >= 2: return f'"point": [{nums[0]}, {nums[1]}]'
                    return '"point": [0, 0]'

                json_str = re.sub(r'"point"\s*:\s*\[(.*?)\]', sanitize_point, json_str)
                json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
                json_str = re.sub(r'//.*', '', json_str)
                json_str = re.sub(r'/\*.*?\*/', '', json_str, flags=re.DOTALL)

                # 3. 加载 JSON
                data = json.loads(json_str)

                # 🔥 核心修改：支持 batch 数组 或 单个 action 兼容
                actions_list = []
                if "batch" in data and isinstance(data["batch"], list):
                    actions_list = data["batch"]
                else:
                    # 兼容旧格式（如果模型偶尔只吐单个对象）
                    actions_list = [data]

                print(f"🤖 Executor Thought: {data.get('thought', '无')}")

                # 🔥 循环执行所有动作
                step_logs = []
                for idx, action_item in enumerate(actions_list):
                    print(f"  └─ 动作 {idx + 1}/{len(actions_list)}: {action_item.get('summary', '无')}")

                    is_finished, action_desc = execute_action(action_item)
                    step_logs.append(action_desc)

                    # 动作间增加微小延迟，防止操作过快网页反应不过来
                    if idx < len(actions_list) - 1:
                        time.sleep(0.8)

                    if is_finished:
                        print("🎉 任务完成！")
                        return

                # 记录日志 (把这一批动作合并记录)
                full_analysis_log = analysis_plan.replace('\n', '  ').strip()
                combined_actions = " -> ".join(step_logs)
                log_entry = f"Step {step + 1}: [{combined_actions}] | 🧠 {full_analysis_log}"
                history_log.append(log_entry)


                elapsed = time.time() - start_time
                avg_time = elapsed / (step + 1)
                print(f"⏱️ 总耗时: {elapsed:.2f}s | 本轮平均: {avg_time:.2f}s")

            except json.JSONDecodeError as e:
                print(f"⚠️ JSON 解析失败: {e}")
                # Debug: print(json_str)
                history_log.append(f"Step {step + 1}: ERROR(JSON)")
                # --- 👆 解析逻辑修改结束 👆 ---

        except Exception as e:
            print(f"❌ Executor 失败: {e}")
            history_log.append(f"Step {step + 1}: ERROR({str(e)})")

        time.sleep(1)

    print("🏁 达到最大步数，流程结束")


if __name__ == "__main__":
    try:
        prompts = load_prompts()
        task = "请在携程预订上海外滩附近，2月11日入住，2月14日离店，如家或汉庭品牌，距离近优先。"
        run_agent_task(task, prompts)
    except Exception as e:
        print(f"💥 程序崩溃: {e}")