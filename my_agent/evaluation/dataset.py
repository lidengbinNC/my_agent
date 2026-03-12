"""基准测试数据集 — 20+ 评估任务，覆盖不同难度和类别。

面试考点:
  - 评估数据集设计原则：
      覆盖多种难度（Easy/Medium/Hard）
      覆盖多种能力（数学计算、信息搜索、代码执行、多步推理）
      有明确的参考答案（便于 LLM-as-Judge 对比）
      标注期望工具（便于工具准确率计算）
  - 数据集分层：
      Easy (8题)：单步工具调用，答案明确
      Medium (8题)：2-3步推理，需要组合工具
      Hard (6题)：多步规划，需要 Plan-and-Execute
"""

from __future__ import annotations

from my_agent.evaluation.models import EvalTask, TaskDifficulty

_BENCHMARK_TASKS: list[EvalTask] = [

    # ── Easy: 数学计算 ────────────────────────────────────────────
    EvalTask(
        task_id="math_001",
        question="计算 (123 + 456) * 789 - 321 的结果",
        expected_answer="573,930 或 573930",
        expected_tools=["calculator"],
        difficulty=TaskDifficulty.EASY,
        category="math",
        tags=["arithmetic"],
    ),
    EvalTask(
        task_id="math_002",
        question="2的10次方是多少？",
        expected_answer="1024",
        expected_tools=["calculator"],
        difficulty=TaskDifficulty.EASY,
        category="math",
        tags=["power"],
    ),
    EvalTask(
        task_id="math_003",
        question="圆的面积公式是什么？半径为5时面积是多少（π取3.14159）？",
        expected_answer="面积 = π×r² ≈ 78.54",
        expected_tools=["calculator"],
        difficulty=TaskDifficulty.EASY,
        category="math",
        tags=["geometry"],
    ),

    # ── Easy: 直接知识问答 ────────────────────────────────────────
    EvalTask(
        task_id="know_001",
        question="Python 中 list 和 tuple 的主要区别是什么？",
        expected_answer="list 可变，tuple 不可变；list 用方括号，tuple 用圆括号",
        expected_tools=[],
        difficulty=TaskDifficulty.EASY,
        category="knowledge",
        tags=["python", "data_structure"],
    ),
    EvalTask(
        task_id="know_002",
        question="HTTP 状态码 404 和 500 分别代表什么？",
        expected_answer="404 表示资源未找到，500 表示服务器内部错误",
        expected_tools=[],
        difficulty=TaskDifficulty.EASY,
        category="knowledge",
        tags=["http", "web"],
    ),
    EvalTask(
        task_id="know_003",
        question="什么是 RESTful API？",
        expected_answer="基于 REST 架构风格的 API，使用 HTTP 方法（GET/POST/PUT/DELETE）操作资源",
        expected_tools=[],
        difficulty=TaskDifficulty.EASY,
        category="knowledge",
        tags=["api", "web"],
    ),

    # ── Easy: 代码执行 ────────────────────────────────────────────
    EvalTask(
        task_id="code_001",
        question="用 Python 计算斐波那契数列的前10项",
        expected_answer="0, 1, 1, 2, 3, 5, 8, 13, 21, 34",
        expected_tools=["code_executor"],
        difficulty=TaskDifficulty.EASY,
        category="code",
        tags=["fibonacci", "python"],
    ),
    EvalTask(
        task_id="code_002",
        question="用 Python 写一个函数判断一个数是否是质数，并测试 17 和 18",
        expected_answer="17 是质数，18 不是质数",
        expected_tools=["code_executor"],
        difficulty=TaskDifficulty.EASY,
        category="code",
        tags=["prime", "python"],
    ),

    # ── Medium: 多步推理 ──────────────────────────────────────────
    EvalTask(
        task_id="reason_001",
        question="如果一个正方形的周长是 36cm，它的面积是多少？",
        expected_answer="边长 = 9cm，面积 = 81cm²",
        expected_tools=["calculator"],
        difficulty=TaskDifficulty.MEDIUM,
        category="math",
        tags=["geometry", "multi_step"],
    ),
    EvalTask(
        task_id="reason_002",
        question="小明有 100 元，买了 3 本书每本 15 元，2 支笔每支 5 元，还剩多少钱？",
        expected_answer="剩余 = 100 - 3×15 - 2×5 = 100 - 45 - 10 = 45 元",
        expected_tools=["calculator"],
        difficulty=TaskDifficulty.MEDIUM,
        category="math",
        tags=["word_problem"],
    ),
    EvalTask(
        task_id="reason_003",
        question="解释 Python 的 GIL（全局解释器锁）是什么，以及它对多线程的影响",
        expected_answer="GIL 是 CPython 的互斥锁，同一时刻只允许一个线程执行 Python 字节码，导致 CPU 密集型任务无法真正并行",
        expected_tools=[],
        difficulty=TaskDifficulty.MEDIUM,
        category="knowledge",
        tags=["python", "concurrency"],
    ),
    EvalTask(
        task_id="code_003",
        question="用 Python 实现快速排序算法，并对列表 [64, 34, 25, 12, 22, 11, 90] 排序",
        expected_answer="[11, 12, 22, 25, 34, 64, 90]",
        expected_tools=["code_executor"],
        difficulty=TaskDifficulty.MEDIUM,
        category="code",
        tags=["sorting", "algorithm"],
    ),
    EvalTask(
        task_id="code_004",
        question="用 Python 统计字符串 'hello world hello python' 中每个单词出现的次数",
        expected_answer="hello: 2, world: 1, python: 1",
        expected_tools=["code_executor"],
        difficulty=TaskDifficulty.MEDIUM,
        category="code",
        tags=["string", "counter"],
    ),
    EvalTask(
        task_id="reason_004",
        question="比较 Docker 和虚拟机的主要区别，各自适用什么场景？",
        expected_answer="Docker 共享宿主机内核，轻量快速，适合微服务；VM 完全隔离，安全性高，适合不同OS环境",
        expected_tools=[],
        difficulty=TaskDifficulty.MEDIUM,
        category="knowledge",
        tags=["docker", "devops"],
    ),
    EvalTask(
        task_id="reason_005",
        question="设计一个 LRU 缓存，说明其数据结构选择和时间复杂度",
        expected_answer="使用哈希表 + 双向链表，get/put 均为 O(1)",
        expected_tools=[],
        difficulty=TaskDifficulty.MEDIUM,
        category="knowledge",
        tags=["cache", "data_structure"],
    ),

    # ── Hard: 复杂多步任务 ────────────────────────────────────────
    EvalTask(
        task_id="hard_001",
        question="用 Python 实现一个简单的 Stack 类，支持 push、pop、peek 操作，并演示使用",
        expected_answer="包含 push/pop/peek 方法的 Stack 类，演示入栈出栈操作",
        expected_tools=["code_executor"],
        difficulty=TaskDifficulty.HARD,
        category="code",
        tags=["data_structure", "oop"],
    ),
    EvalTask(
        task_id="hard_002",
        question="解释 CAP 定理，并举例说明 Zookeeper 和 Cassandra 分别满足哪两个特性",
        expected_answer="CAP = 一致性/可用性/分区容错；Zookeeper 满足 CP，Cassandra 满足 AP",
        expected_tools=[],
        difficulty=TaskDifficulty.HARD,
        category="knowledge",
        tags=["distributed", "database"],
    ),
    EvalTask(
        task_id="hard_003",
        question="用 Python 实现归并排序，分析其时间和空间复杂度，并与快速排序对比",
        expected_answer="归并排序 O(n log n) 时间，O(n) 空间，稳定；快排平均 O(n log n)，最坏 O(n²)，不稳定",
        expected_tools=["code_executor"],
        difficulty=TaskDifficulty.HARD,
        category="code",
        tags=["sorting", "complexity"],
    ),
    EvalTask(
        task_id="hard_004",
        question="设计一个高并发秒杀系统的架构，说明如何防止超卖",
        expected_answer="Redis 原子操作预减库存 + 消息队列异步处理 + 数据库乐观锁兜底",
        expected_tools=[],
        difficulty=TaskDifficulty.HARD,
        category="knowledge",
        tags=["system_design", "concurrency"],
    ),
    EvalTask(
        task_id="hard_005",
        question="用 Python 实现二叉搜索树的插入、查找和中序遍历",
        expected_answer="包含 insert/search/inorder 方法的 BST 类，中序遍历输出有序序列",
        expected_tools=["code_executor"],
        difficulty=TaskDifficulty.HARD,
        category="code",
        tags=["tree", "data_structure"],
    ),
    EvalTask(
        task_id="hard_006",
        question="解释 Transformer 的注意力机制（Self-Attention），说明 Q、K、V 的含义和计算过程",
        expected_answer="Q=Query K=Key V=Value，Attention(Q,K,V)=softmax(QK^T/√d_k)V，捕获序列中任意位置的依赖关系",
        expected_tools=[],
        difficulty=TaskDifficulty.HARD,
        category="knowledge",
        tags=["ai", "transformer"],
    ),
]


def get_benchmark_dataset(
    difficulty: TaskDifficulty | None = None,
    category: str | None = None,
    limit: int | None = None,
) -> list[EvalTask]:
    """获取基准测试数据集，支持按难度/类别过滤。"""
    tasks = list(_BENCHMARK_TASKS)

    if difficulty:
        tasks = [t for t in tasks if t.difficulty == difficulty]
    if category:
        tasks = [t for t in tasks if t.category == category]
    if limit:
        tasks = tasks[:limit]

    return tasks
