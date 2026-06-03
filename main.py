"""
UI Agent - 简化版

使用方法：
1. 修改 prompt.yaml 中的 prompt 内容
2. 修改 tasks.yaml 中的任务内容
3. 运行 python main.py 进行测试
"""
import yaml
import argparse
from agent import run_agent_task


def load_config():
    """加载配置"""
    with open("prompt.yaml", "r", encoding="utf-8") as f:
        prompts = yaml.safe_load(f)
    with open("tasks.yaml", "r", encoding="utf-8") as f:
        tasks_config = yaml.safe_load(f)
    return prompts, tasks_config


def list_tasks(tasks_config):
    """列出所有任务"""
    print("\n📋 任务列表：")
    print("=" * 60)
    for i, task in enumerate(tasks_config['tasks']):
        status = "✅" if task.get('enabled', True) else "❌"
        print(f"{status} [{i}] {task['name']}")
        print(f"    {task['description'][:80]}...")
    print("=" * 60)


def find_task(tasks_config, task_selector):
    """根据索引或名称查找任务"""
    tasks = tasks_config['tasks']

    # 尝试按索引查找
    try:
        idx = int(task_selector)
        if 0 <= idx < len(tasks):
            return tasks[idx]
    except ValueError:
        pass

    # 按名称查找
    for task in tasks:
        if task['name'] == task_selector:
            return task

    return None


def run_task(prompts, task, verbose=False):
    """运行任务"""
    print(f"\n🚀 运行任务: {task['name']}")
    if verbose:
        print(f"📝 任务描述: {task['description']}")
    print("-" * 60)

    run_agent_task(
        task['description'],
        prompts,
        max_steps=task.get('max_steps', 50)
    )


def main():
    parser = argparse.ArgumentParser(description='UI Agent')
    parser.add_argument('--task', '-t', type=str, help='任务名称或索引')
    parser.add_argument('--list', '-l', action='store_true', help='列出所有任务')
    parser.add_argument('--verbose', '-v', action='store_true', help='显示详细信息')

    args = parser.parse_args()

    # 加载配置
    prompts, tasks_config = load_config()

    # 列出任务
    if args.list:
        list_tasks(tasks_config)
        return

    # 选择任务
    if args.task:
        selected_task = find_task(tasks_config, args.task)
        if selected_task:
            run_task(prompts, selected_task, args.verbose)
        else:
            print(f"❌ 未找到任务: {args.task}")
            list_tasks(tasks_config)
    else:
        # 使用默认任务
        default_selector = tasks_config.get('default_task', 0)
        selected_task = find_task(tasks_config, default_selector)
        if selected_task:
            run_task(prompts, selected_task, args.verbose)
        else:
            print("❌ 默认任务未配置")
            list_tasks(tasks_config)


if __name__ == "__main__":
    main()
