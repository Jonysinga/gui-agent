# UI Agent 使用文档

测试携程订票功能，完成配置后，运行main.py(具体指令见后面)，运行成功后打开chrome浏览器空页面，全屏。  然后等待即可，程序会自行操作（macos下需要一些权限设置，比如 点击屏幕，截图，滚轮等等，第一次使用需要注意开一下这些权限），一次测试可能需要5min以上，具体看任务的复杂程度和设置的max_steps数，到携程支付界面就算成功（需要先登录携程账号）

注意点：

1. 运行程序需要先进行授权，比如 点击屏幕，截图，滚轮等等，macos应该会有相应提示

2. 运行成功后只能全屏等待，如果需要干其他事需要再开一个显示器

3. 关于task_prompt，需合法（符合携程网页），比如今天是2月2日，task里不能让他订1月的酒店，属于模型既定无法完成的任务

## 一、环境准备

### 1. 安装 Python

确保已安装 Python 3.9 或更高版本。检查版本：

```bash
python --version
# 或
python3 --version
```

如果没有安装，请访问 https://www.python.org/downloads/ 下载安装，或者问ai如何安装

### 2. 创建虚拟环境（推荐）

```bash
# 进入项目目录
cd ui-agent-refactored

# 创建虚拟环境
python -m venv .venv

# 激活虚拟环境
# macOS/Linux:
source .venv/bin/activate

# Windows:
.venv\Scripts\activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

## 二、修改配置（主要部分）


### 1. 修改 Prompt（System Prompt）

打开 `prompt.yaml` 文件，修改以下内容：

- **system_prompt**: 系统 Prompt，定义 Agent 的角色和行为规则
- **task_template**: 任务 Prompt 模板
- **warnings**: 警告信息模板（可选）

### 2. 修改任务（Task Prompt）

打开 `tasks.yaml` 文件，可以：

- 添加新任务
- 修改现有任务描述
- 调整最大步数 (max_steps)
- 通过 `enabled` 字段启用/禁用任务
- 修改 `default_task` 索引来选择默认运行的任务

示例：

```yaml
tasks:
  - name: "我的新任务"
    description: "任务详细描述..."
    max_steps: 50
    enabled: true

default_task: 0  # 运行第 0 个任务
```

## 三、运行测试

### 方式 1: 运行默认任务

```bash
python main.py
```

### 方式 2: 查看所有任务

```bash
python main.py --list
```

输出示例：
```
📋 任务列表：
============================================================
✅ [0] 携程-北京酒店预订
    在携程预订2月13日到2月15日，北京市清华科技园附近5公里内的酒店...
❌ [1] 携程-上海酒店预订
    请在携程预订上海外滩附近6公里内，2月6日到2月9日的酒店...
============================================================
```

### 方式 3: 运行指定任务（按索引）

```bash
python main.py --task 1
```

### 方式 4: 运行指定任务（按名称）

```bash
python main.py --task "携程-上海酒店预订"
```

### 方式 5: 显示详细信息

```bash
python main.py --task 0 --verbose
```


## 四、常见问题

### Q: 如何添加新任务？

A: 打开 `tasks.yaml`，在 `tasks` 列表中添加新任务：

```yaml
tasks:
  - name: "任务名称"
    description: "任务描述"
    max_steps: 50
    enabled: true
```

### Q: 如何切换默认任务？

A: 修改 `tasks.yaml` 中的 `default_task` 索引：

```yaml
default_task: 1  # 运行第 2 个任务
```

### Q: 运行前需要做什么？

A: 确保目标应用程序（如浏览器）已打开并可见，程序启动后会给你 3 秒时间切换窗口。

### Q: 截图保存在哪里？

A: 自动保存到 `screenshots_时间戳/` 目录中。

## 六、完整使用流程总结

```bash
# 1. 进入项目目录
cd ui-agent-refactored

# 2. 创建并激活虚拟环境
python -m venv .venv
source .venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 修改配置文件
# - 打开 agent.py 修改 API Key
# - 打开 prompt.yaml 修改 Prompt
# - 打开 tasks.yaml 修改任务

# 5. 运行测试
python main.py --list      # 先查看任务（可选）
python main.py --task 0   # 运行指定任务
```
