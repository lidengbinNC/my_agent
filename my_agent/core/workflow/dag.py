"""DAG 拓扑排序 + 循环依赖检测。

面试考点:
  - Kahn 算法（BFS 拓扑排序）：
      1. 计算每个节点的入度（in-degree）
      2. 将入度为 0 的节点加入队列
      3. 逐步移除节点，更新后继入度
      4. 若最终处理节点数 < 总节点数 → 存在环
  - 时间复杂度：O(V + E)，V=节点数，E=边数
  - 并行层（execution levels）：入度为 0 且同层的节点可并行执行
    通过 BFS 分层，每一层内的节点互相无依赖
"""

from __future__ import annotations

from collections import defaultdict, deque

from my_agent.domain.workflow.models import WorkflowDef


class CyclicDependencyError(ValueError):
    """DAG 中存在循环依赖。"""


class DAGSorter:
    """基于 Kahn 算法的 DAG 拓扑排序器。"""

    def __init__(self, workflow: WorkflowDef) -> None:
        self._workflow = workflow

    def validate(self) -> None:
        """检测循环依赖，存在则抛出 CyclicDependencyError。"""
        self.topological_order()  # 排序失败即有环

    def topological_order(self) -> list[str]:
        """返回拓扑排序后的节点 ID 列表。

        Raises:
            CyclicDependencyError: 图中存在循环依赖
        """
        nodes = {n.node_id for n in self._workflow.nodes}
        in_degree: dict[str, int] = {n: 0 for n in nodes}
        adjacency: dict[str, list[str]] = defaultdict(list)

        for edge in self._workflow.edges:
            if edge.source in nodes and edge.target in nodes:
                adjacency[edge.source].append(edge.target)
                in_degree[edge.target] += 1

        queue: deque[str] = deque(n for n in nodes if in_degree[n] == 0)
        order: list[str] = []

        while queue:
            node_id = queue.popleft()
            order.append(node_id)
            for neighbor in adjacency[node_id]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(order) != len(nodes):
            cycle_nodes = [n for n in nodes if n not in order]
            raise CyclicDependencyError(
                f"DAG 中存在循环依赖，涉及节点: {cycle_nodes}"
            )

        return order

    def execution_levels(self) -> list[list[str]]:
        """将节点划分为执行层级：同层内节点可并行，层间有序。

        返回示例：
            [[start], [node_a, node_b], [node_c], [end]]
            第 1 层 start 完成后，node_a 和 node_b 可并行执行。

        面试考点：这是 asyncio.gather 并行调度的基础
        """
        nodes = {n.node_id for n in self._workflow.nodes}
        in_degree: dict[str, int] = {n: 0 for n in nodes}
        adjacency: dict[str, list[str]] = defaultdict(list)

        for edge in self._workflow.edges:
            if edge.source in nodes and edge.target in nodes:
                adjacency[edge.source].append(edge.target)
                in_degree[edge.target] += 1

        levels: list[list[str]] = []
        current_level = [n for n in nodes if in_degree[n] == 0]

        while current_level:
            levels.append(sorted(current_level))  # 排序保证确定性
            next_level: list[str] = []
            for node_id in current_level:
                for neighbor in adjacency[node_id]:
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0:
                        next_level.append(neighbor)
            current_level = next_level

        total = sum(len(lvl) for lvl in levels)
        if total != len(nodes):
            raise CyclicDependencyError("DAG 中存在循环依赖")

        return levels

    def get_ready_nodes(self, completed: set[str], all_nodes: set[str]) -> list[str]:
        """返回当前可以执行的节点（前驱全部完成且尚未执行）。

        用于增量调度：每完成一批节点后，找出下一批可执行的。
        """
        pending = all_nodes - completed
        result = []
        for node_id in pending:
            preds = [
                e.source
                for e in self._workflow.edges
                if e.target == node_id and e.source in all_nodes
            ]
            if all(p in completed for p in preds):
                result.append(node_id)
        return result
