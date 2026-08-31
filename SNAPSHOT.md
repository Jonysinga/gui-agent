# 快照说明：小模型版

来源压缩包：`ui-agent-refactored-小模型.zip`（文件时间戳 2026-02-28 ~ 03-16）

## 这个快照是什么

把双模型全部换成 OpenRouter 上 Qwen3-VL 小模型的试验分支。

| 角色 | 模型 | 后端 |
|---|---|---|
| Analyst（决策脑） | `qwen/qwen3-vl-32b-instruct` | OpenRouter |
| Executor（执行脑） | `qwen/qwen3-vl-8b-instruct` | OpenRouter |

`main.py`、`README.md`、`requirements.txt` 与 `main` 分支及 `snapshot/312-2` 完全一致，
差异全部集中在 `agent.py`、`prompt.yaml`、`tasks.yaml`。

## 与 snapshot/312-2 的关键差异

- **模型后端**：312-2 走 Gemini 3 Pro + 自建 UI-TARS-1.5-7B；本快照全部走 OpenRouter Qwen3-VL
  （32B 决策 + 8B 执行），并删掉了豆包的注释配置块。
- **坐标处理（本快照较弱）**：312-2 有 `normalize_point()`，探测 Retina/DPI 缩放比，
  兼容归一化坐标（≤1000）与物理像素坐标（>1000）并做边界裁剪。本快照没有该函数，
  每处动作内联 `point / 1000 * 屏幕宽高`，只支持归一化坐标。
  边缘防抖也退回在原始坐标上与 `SCREEN_HEIGHT * 0.9` 比较，量纲并不一致。
- **动作集（本快照更全）**：新增 `drag` 动作（`start_point` / `end_point` → `pyautogui.dragTo`），
  `prompt.yaml` 的动作列表同步补上了 `double_click` 与 `drag`。
  312-2 的 agent 里有 `double_click` 实现但 prompt 未声明，且完全没有 `drag`。
- **步间等待**：本快照 `time.sleep(5)`（补偿小模型响应与页面加载更慢），312-2 为 `time.sleep(1)`。
- **任务集**：本快照 20 条（5 条启用，其余为注释掉的"简单-N"占位）；312-2 为 160 条批量评测任务。

## 目录整理

历史版本快照 `agent_021023.py`、`agent_021117.py` 与各版本 prompt（`prompt_0206` / `prompt_0210` /
`prompt_021017` / `prompt_021023` / `prompt——021117`）已按仓库约定归入 `configs/`。

## 密钥处理

原压缩包 `agent.py` 中 OpenRouter 密钥以 `os.getenv` 的默认值形式明文硬编码，
两个历史 `agent_*.py` 中硬编码了明文豆包密钥，入库前均已改为从环境变量读取
（`.env.example` 已补上 `OPENROUTER_API_KEY`）。
**这些密钥曾以明文存在于本地文件中，建议轮换。**

## 已排除

`.venv/`、`__pycache__/`、`.idea/`、`.DS_Store`、`__MACOSX/`，
以及一次运行留下的 `screenshots_20260316_152839/` 截图目录。
