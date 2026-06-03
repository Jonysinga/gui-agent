"""
test_local_model.py - 测试本地模型 API 是否可用
用法：uv run python test_local_model.py
"""
import base64
import pyautogui
from io import BytesIO
from openai import OpenAI

client = OpenAI(base_url="http://115.190.215.236:8004/v1", api_key="EMPTY")

# 先测试服务是否可达
print("🔍 测试服务连通性...")
try:
    models = client.models.list()
    print(f"✅ 服务在线，可用模型: {[m.id for m in models.data]}")
except Exception as e:
    print(f"❌ 服务不可达: {e}")
    exit(1)

# 截一张当前屏幕
print("\n📸 截取当前屏幕...")
screenshot = pyautogui.screenshot()
if screenshot.mode == 'RGBA':
    screenshot = screenshot.convert('RGB')
buffered = BytesIO()
screenshot.save(buffered, format="JPEG", quality=80)
image_base64 = base64.b64encode(buffered.getvalue()).decode()
print(f"✅ 截图完成，大小: {len(image_base64)} bytes (base64)")

# 调用模型
print("\n🤖 调用模型...")
try:
    response = client.chat.completions.create(
        model="/mnt/disk3/models/UI-TARS-1.5-7B",
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
                {"type": "text", "text": "What is the next action?"}
            ]
        }],
        temperature=0.1,
        max_tokens=500
    )
    print(f"✅ 模型响应:")
    print(response.choices[0].message.content)
except Exception as e:
    print(f"❌ 调用失败: {e}")
