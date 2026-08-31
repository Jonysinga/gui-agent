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

# ================= 配置区域 =================
API_KEY = os.getenv("DOUBAO_API_KEY", "")
ENDPOINT_ID = "doubao-1-5-ui-tars-250428"

client = OpenAI(
    base_url="https://ark.cn-beijing.volces.com/api/v3",
    api_key=API_KEY
)

# 获取屏幕尺寸
SCREEN_WIDTH, SCREEN_HEIGHT = pyautogui.size()
pyautogui.FAILSAFE = True

# 判断当前系统是否为 macOS
IS_MAC = platform.system() == "Darwin"
CMD_KEY = 'command' if IS_MAC else 'ctrl'

print(f"🖥️ 检测到系统: {'macOS' if IS_MAC else 'Windows/Linux'}")
print(f"📏 屏幕逻辑尺寸: {SCREEN_WIDTH}x{SCREEN_HEIGHT}")
print(f"⌨️ 快捷键修饰符: {CMD_KEY}")


# ================= 工具函数 =================
def load_prompts(filepath="prompt.yaml"):
    """加载 Prompt 配置"""
    with open(filepath, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def encode_image_to_base64(image):
    if image.mode == 'RGBA':
        image = image.convert('RGB')
    buffered = BytesIO()
    image.save(buffered, format="JPEG", quality=75)
    return base64.b64encode(buffered.getvalue()).decode('utf-8')


def take_screenshot():
    return pyautogui.screenshot()


def execute_action(data):
    """执行动作"""
    action = data.get("action")
    thought = data.get("thought", "")
    point = data.get("point", [0, 0])
    text = data.get("text", "")
    key = data.get("key", "")

    print(f"💡 思考: {thought}")

    try:
        if action == "click":
            x = int(point[0] / 1000 * SCREEN_WIDTH)
            y = int(point[1] / 1000 * SCREEN_HEIGHT)
            print(f"🖱️ 单击: ({x}, {y})")
            pyautogui.moveTo(x, y, duration=0.5)
            pyautogui.click()
            return False, f"CLICK(point={point})"

        elif action == "double_click":
            x = int(point[0] / 1000 * SCREEN_WIDTH)
            y = int(point[1] / 1000 * SCREEN_HEIGHT)
            print(f"🖱️🖱️ 双击: ({x}, {y})")
            pyautogui.moveTo(x, y, duration=0.5)
            pyautogui.doubleClick()
            return False, f"DOUBLE_CLICK(point={point})"

        elif action == "clear":
            print(f"⌨️ 清空输入框 ({CMD_KEY}+A -> Delete)")
            pyautogui.keyDown(CMD_KEY)
            time.sleep(0.1)
            pyautogui.press('a')
            time.sleep(0.1)
            pyautogui.keyUp(CMD_KEY)
            time.sleep(0.1)
            pyautogui.press('delete')
            return False, "CLEAR"

        elif action == "type":
            # --- 新增逻辑：如果有坐标，先点击 ---
            if point and point != [0, 0] and point is not None:
                x = int(point[0] / 1000 * SCREEN_WIDTH)
                y = int(point[1] / 1000 * SCREEN_HEIGHT)
                print(f"🖱️ (自动) 点击输入框: ({x}, {y})")
                pyautogui.moveTo(x, y, duration=0.3)
                pyautogui.click()
                time.sleep(0.3)  # 等待焦点激活
            print(f"📋 覆盖输入: {text}")
            pyperclip.copy(text)
            time.sleep(0.3)

            pyautogui.keyDown(CMD_KEY)
            time.sleep(0.1)
            pyautogui.press('a')
            time.sleep(0.1)
            pyautogui.keyUp(CMD_KEY)
            time.sleep(0.2)

            pyautogui.keyDown(CMD_KEY)
            time.sleep(0.1)
            pyautogui.press('v')
            time.sleep(0.1)
            pyautogui.keyUp(CMD_KEY)
            time.sleep(0.5)
            return False, f"TYPE(text='{text}')"

        elif action == "press":
            key_raw = key.lower()
            print(f"⌨️ 按键: {key_raw}")

            if key_raw in ['ctrl', 'control'] and IS_MAC:
                key_raw = 'command'
            if key_raw == 'enter':
                key_raw = 'return' if IS_MAC else 'enter'

            keys = key_raw.replace('+', ' ').split()
            if IS_MAC:
                keys = ['command' if k in ['ctrl', 'control'] else k for k in keys]

            if len(keys) > 1:
                print(f"⌨️ 执行组合键: {keys}")
                pyautogui.keyDown(keys[0])
                time.sleep(0.2)
                pyautogui.press(keys[1])
                time.sleep(0.2)
                pyautogui.keyUp(keys[0])
            else:
                pyautogui.press(keys[0])
            return False, f"PRESS(key='{key_raw}')"

        elif action == "scroll_down":
            print("📜 向下滚动页面")
            scroll_amount = -10 if IS_MAC else -600
            pyautogui.scroll(scroll_amount)
            time.sleep(0.5)
            return False, "SCROLL_DOWN"

        elif action == "scroll_up":
            print("📜 向上滚动页面")
            scroll_amount = 10 if IS_MAC else 1000
            pyautogui.scroll(scroll_amount)
            time.sleep(0.5)
            return False, "SCROLL_UP"

        elif action == "finish":
            print("✅ 任务完成！")
            return True, "FINISH"

        else:
            print(f"⚠️ 未知动作: {action}")
            return False, f"UNKNOWN({action})"

    except Exception as e:
        print(f"❌ 动作执行失败: {e}")
        return False, f"ERROR({str(e)})"


def check_repeated_actions(history_log, min_repeats=3):
    """检查重复操作"""
    if len(history_log) < min_repeats:
        return False, 0, ""

    actions = []
    for entry in history_log:
        if "|" in entry:
            action_part = entry.split("|")[0].split(":", 1)[1].strip()
            action_core = re.sub(r'\(.*?\)', '', action_part)
            actions.append(action_core)

    if not actions:
        return False, 0, ""

    last_action = actions[-1]
    count = 0
    for action in reversed(actions):
        if action == last_action:
            count += 1
        else:
            break

    if count >= min_repeats:
        return True, count, last_action

    return False, 0, ""


def run_agent_task(task_prompt, prompts, max_steps=50):
    """运行 Agent 任务"""
    print(f"🚀 任务启动: {task_prompt}")
    # 记录任务开始时间，用于每步打印耗时统计
    start_time = time.time()

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    save_dir = f"screenshots_{timestamp}"
    if not os.path.exists(save_dir):
        os.makedirs(save_dir)
    print(f"📂 截图将保存至文件夹: ./{save_dir}")

    print("⚠️ 请在 3 秒内切换到目标窗口...")
    time.sleep(3)

    history_log = []

    for step in range(max_steps):
        print(f"\n=== Step {step + 1}/{max_steps} ===")

        screenshot = take_screenshot()


        try:
            save_img = screenshot.copy()
            if save_img.mode == 'RGBA':
                save_img = save_img.convert('RGB')
            file_path = os.path.join(save_dir, f"step_{step+1:02d}.jpg")
            save_img.save(file_path, quality=80)
            print(f"📸 截图已保存: {file_path}")
        except Exception as e:
            print(f"❌ 截图保存失败: {e}")

        base64_img = encode_image_to_base64(screenshot)

        # 检测重复操作
        is_repeated, repeat_count, repeated_action = check_repeated_actions(history_log, min_repeats=3)
        warning_str = ""

        if repeated_action == "SCROLL_DOWN" and repeat_count >= 5:
            warning_str = prompts['warnings']['scroll_down'].format(count=repeat_count)
            print(warning_str)
        elif is_repeated and repeat_count >= 3:
            warning_str = prompts['warnings']['repeated'].format(count=repeat_count, action=repeated_action)
            print(f"⚠️ 检测到重复操作: {repeated_action} (连续 {repeat_count} 次)")

        # 只取最近 15 步，防止 Token 爆炸
        recent_history = history_log[-15:] if len(history_log) > 15 else history_log
        history_str = "\n".join(recent_history) if history_log else "无 (这是第一步)"

        # 构建任务 prompt
        current_prompt_text = prompts['task_template'].format(
            task=task_prompt,
            history=history_str,
            warning=warning_str
        )

        user_content = [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}},
            {"type": "text", "text": current_prompt_text}
        ]

        messages = [
            {"role": "system", "content": prompts['system_prompt']},
            {"role": "user", "content": user_content}
        ]

        try:
            response = client.chat.completions.create(
                model=ENDPOINT_ID,
                messages=messages,
                temperature=0.1,
                max_tokens=500
            )
            response_text = response.choices[0].message.content

        except Exception as e:
            print(f"API请求失败: {e}")
            time.sleep(3)
            continue

        try:
            json_matches = re.findall(r'\{.*?\}', response_text, re.DOTALL)

            if json_matches:
                valid_action_executed = False
                for json_str in json_matches:
                    try:
                        # --- 🧹 1. 清洗 JSON 注释 ---
                        json_clean = re.sub(r'//.*', '', json_str)  # 去除 //
                        json_clean = re.sub(r'/\*.*?\*/', '', json_clean, flags=re.DOTALL)  # 去除 /* */
                        # 修复尾随逗号 (例如 "a":1, })
                        json_clean = re.sub(r',(\s*?[\}\]])', r'\1', json_clean)
                        action_data = json.loads(json_clean)
                        is_finished, action_desc_short = execute_action(action_data)

                        thought_summary = action_data.get("thought", "")[:60] + "..."
                        # --- 🔥 核心修改开始 🔥 ---
                        # 获取模型输出的语义描述 (Summary)
                        # 如果模型没输出 summary，就用 "无描述" 代替
                        action_semantic = action_data.get("summary", "无描述")
                        log_entry = f"Step {step+1}: {action_desc_short}[{action_semantic}] | Thought: {thought_summary}"
                        history_log.append(log_entry)
                        print(f"📝 语义记录: {action_semantic}")
                        print(f"📜 完整历史: {log_entry}")
                        # 每步打印耗时统计（从任务开始到当前步）
                        elapsed = time.time() - start_time
                        steps_done = len(history_log)
                        avg_per_step = elapsed / steps_done if steps_done > 0 else 0
                        print(f"⏱️ 已用: {elapsed:.2f}s，平均每步: {avg_per_step:.2f}s（{steps_done} 步）")
                        # 成功执行后，标记并跳出循环
                        valid_action_executed = True
                        if is_finished:
                            print("\n🎉 任务成功完成！")
                            return

                        time.sleep(0.5)
                        break

                    except json.JSONDecodeError as e:
                        print(f"⚠️ JSON 解析失败: {e} \n内容: {json_str}")
                        # 不要 break，尝试下一个 match，或者记录错误


                # --- 🚨 2. 错误回填机制 ---
                if not valid_action_executed:
                    error_msg = f"Step {step + 1}: ERROR(JSON解析失败) | System: 上一步输出的 JSON 格式错误，请不要使用 // 注释。"
                    history_log.append(error_msg)
                    print("❌ 本步执行失败，已将错误回填至 History。")

            # -------------------------
            else:
                print("⚠️ 未检测到有效JSON")
                print(f"原始回复: {response_text[:200]}...")

        except Exception as e:
            print(f"⚠️ 流程错误: {e}")

        time.sleep(0.5)
    print("🏁 达到最大步数，流程结束")

