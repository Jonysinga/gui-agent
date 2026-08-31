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

SCREEN_WIDTH, SCREEN_HEIGHT = pyautogui.size()
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


def execute_action(data):
    """执行动作 - 返回 (是否结束, 动作简描述)"""
    action = data.get("action")
    point = data.get("point", [0, 0])
    text = data.get("text", "")
    key = data.get("key", "")

    try:
        # 物理防抖
        if action == "click" and point and point[1] > (SCREEN_HEIGHT * 0.9):
            print(f"⚠️ 边缘检测: Y坐标 {point[1]} -> 修正为 {point[1] - 50}")
            point[1] -= 50

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

        elif action == "type":
            if point and point != [0, 0]:
                x = int(point[0] / 1000 * SCREEN_WIDTH)
                y = int(point[1] / 1000 * SCREEN_HEIGHT)
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
            pyautogui.scroll(-10 if IS_MAC else -600)
            return False, "SCROLL_DOWN"

        elif action == "scroll_up":
            print("📜 上滚")
            pyautogui.scroll(10 if IS_MAC else 1000)
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
            resp_analyst = client.chat.completions.create(
                model=ENDPOINT_ID,
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

            resp_executor = client.chat.completions.create(
                model=ENDPOINT_ID,
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
                # 1. 提取最外层 JSON (防止被内容中的 } 截断)
                start_idx = response_text.find('{')
                end_idx = response_text.rfind('}')
                if start_idx != -1 and end_idx != -1:
                    json_str = response_text[start_idx: end_idx + 1]
                else:
                    raise ValueError("未找到 JSON 对象")

                # 2. Point 坐标格式清洗 (确保是数字列表)
                def sanitize_point(match):
                    content = match.group(1)
                    nums = re.findall(r"\d+", content)
                    if len(nums) >= 2: return f'"point": [{nums[0]}, {nums[1]}]'
                    return '"point": [0, 0]'

                json_str = re.sub(r'"point"\s*:\s*\[(.*?)\]', sanitize_point, json_str)

                # 3. 强力清除尾随逗号 (Trailing Commas)
                # 匹配：逗号 + 任意空白 + 结束符(}或])
                json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)

                # 4. 清除注释 (// 或 /* */)
                json_str = re.sub(r'//.*', '', json_str)
                json_str = re.sub(r'/\*.*?\*/', '', json_str, flags=re.DOTALL)

                try:
                    action_data = json.loads(json_str)

                    # 🔥🔥🔥 新增：打印 Executor 的思考过程，让你看到它为什么瞎点 🔥🔥🔥
                    print(f"🤖 Executor Thought: {action_data.get('thought', '无')}")
                    print(f"📝 Action Summary:   {action_data.get('summary', '无')}")
                    # -------------------------------------------------------------

                    is_finished, action_desc = execute_action(action_data)

                    full_analysis_log = analysis_plan.replace('\n', '  ').strip()
                    # 📝 详细日志记录
                    thought = action_data.get("thought", "无思考")
                    summary = action_data.get("summary", "无描述")
                    # 格式: Step X: CLICK(..)[描述] | Thought: ...
                    log_entry = f"Step {step + 1}: {action_desc} | 🧠 Analysis: {full_analysis_log} | 🖐️ Summary: {summary} | 🤔 Thought: {thought}"
                    history_log.append(log_entry)

                    # ⏱️ 打印耗时
                    elapsed = time.time() - start_time
                    avg_time = elapsed / (step + 1)
                    print(f"⏱️ 已用: {elapsed:.2f}s，平均每步: {avg_time:.2f}s")

                    if is_finished:
                        print("🎉 任务完成！")
                        return
                except json.JSONDecodeError as e:
                    print(f"⚠️ JSON 解析失败: {e}\n内容: {json_str}")
                    history_log.append(f"Step {step + 1}: ERROR(JSON解析失败)")
            except json.JSONDecodeError as e:
                print(f"⚠️ JSON 解析失败: {e}\n内容: {response_text}")
                history_log.append(f"Step {step + 1}: ERROR(JSON解析失败)")

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