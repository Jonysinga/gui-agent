# 快照说明：312-2

来源压缩包：`ui-agent-refactored-312-2.zip`（内层目录 `ui-agent-refactored-310`，文件时间戳 2026-03-11 ~ 03-12）

## 这个快照是什么

Gemini + 本地 UI-TARS 双模型主线版本，配套 160 条携程订酒店批量评测任务集。

| 角色 | 模型 | 后端 |
|---|---|---|
| Analyst（决策脑） | `gemini-3-pro-preview` | Google OpenAI 兼容端点 |
| Executor（执行脑） | UI-TARS-1.5-7B | 自建 vLLM `http://115.190.215.236:8004/v1` |

## 相对 main 的差异

`main` 分支的 `agent.py` 比本快照**更新**：main 已引入 dotenv 加载和 `_EXECUTOR_CONFIGS` 多后端选择器
（UI-TARS / 豆包 / GUI-Owl 2B~32B）。本快照保留的是那次重构之前的形态，仅作历史备份，不要用它覆盖 main。

本快照独有、`main` 上没有的内容：
- `debug_main.py`、`scripted_main.py`、`main copy.py` —— 调试与脚本化运行入口
- `configs/tasks-old.yaml`、`configs/tasks_scripted.yaml`、`configs/prompt_uitars.yaml`
- `tasks.yaml` —— 160 条批量评测任务
- `output.json` —— 一次运行的输出样本

## 与 snapshot/small-model 的关键差异

- **坐标处理**：本快照有 `normalize_point()`，探测 Retina/DPI 缩放比，同时兼容归一化坐标（≤1000）
  与物理像素坐标（>1000）并做边界裁剪；小模型版没有该函数，只支持归一化坐标。
- **动作集**：小模型版多了 `drag` 动作，本快照没有。
- **步间等待**：本快照 `time.sleep(1)`，小模型版 `time.sleep(5)`。

## 密钥处理

原压缩包内 `agent.py` / `debug_main.py` / `scripted_main.py` / `tests/test_gemini_thinking.py`
中硬编码了明文 Gemini 与豆包密钥，入库前已统一替换为 `os.getenv(...)` 读取（见 `.env.example`）。
**这些密钥曾以明文存在于本地文件中，建议轮换。**
