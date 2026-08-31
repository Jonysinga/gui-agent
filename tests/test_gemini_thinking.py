import os
"""
test_gemini_thinking.py - 测试 Gemini 模型 thinking/CoT 内容获取
用法：uv run python test_gemini_thinking.py
"""
import json
from openai import OpenAI

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-3-pro-preview"

client = OpenAI(
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    api_key=GEMINI_API_KEY
)

print(f"🧠 测试模型: {GEMINI_MODEL}")
print("=" * 60)

# ===== 方式1: 尝试 extra_body thinking 参数 =====
print("\n[方式1] extra_body thinking enabled")
try:
    resp = client.chat.completions.create(
        model=GEMINI_MODEL,
        messages=[
            {"role": "user", "content": "请分析：1+1等于几？请展示你的推理过程。"}
        ],
        temperature=0.1,
        extra_body={"thinking": {"type": "enabled", "budget_tokens": 2000}}
    )
    print(f"✅ 请求成功")
    print(f"完整 choices[0].message 结构:")
    msg = resp.choices[0].message
    print(f"  content: {msg.content}")
    # 尝试打印所有字段
    print(f"  model_dump: {json.dumps(msg.model_dump(), ensure_ascii=False, indent=2)}")
except Exception as e:
    print(f"❌ 失败: {e}")

print("\n" + "=" * 60)

# ===== 方式2: 不带 thinking 参数的普通请求，看 response 结构 =====
print("\n[方式2] 普通请求，查看完整 response 结构")
try:
    resp = client.chat.completions.create(
        model=GEMINI_MODEL,
        messages=[
            {"role": "user", "content": "请分析：1+1等于几？请展示你的推理过程。"}
        ],
        temperature=0.1,
    )
    print(f"✅ 请求成功")
    msg = resp.choices[0].message
    print(f"  content: {msg.content}")
    print(f"  model_dump: {json.dumps(msg.model_dump(), ensure_ascii=False, indent=2)}")
    # 打印完整 response
    print(f"\n完整 response model_dump:")
    print(json.dumps(resp.model_dump(), ensure_ascii=False, indent=2))
except Exception as e:
    print(f"❌ 失败: {e}")
