/**
 * evaluation.js — Agent 评估中心前端逻辑
 *
 * 功能：
 *  - 加载并展示评估数据集（支持难度/类别筛选）
 *  - 快速同步评估（单任务，直接显示结果）
 *  - 批量异步评估（提交任务队列，WebSocket 实时进度）
 *  - 对比评估（ReAct vs Plan-and-Execute）
 *  - 任务进度右侧面板（WebSocket 订阅）
 */

// ── 常量 ──────────────────────────────────────────────────────────

const API = {
    dataset: '/api/v1/eval/dataset',
    quickEval: '/api/v1/eval/quick',
    batchEval: '/api/v1/eval/batch',
    compareEval: '/api/v1/eval/compare',
    taskStatus: (id) => `/api/v1/tasks/${id}`,
    taskWs: (id) => `ws://${location.host}/api/v1/tasks/${id}/ws`,
};

const DIFFICULTY_LABELS = { easy: '简单', medium: '中等', hard: '困难' };
const DIFFICULTY_COLORS = {
    easy: 'bg-green-100 text-green-700',
    medium: 'bg-yellow-100 text-yellow-700',
    hard: 'bg-red-100 text-red-700',
};
const STATUS_COLORS = {
    pending: 'bg-yellow-100 text-yellow-700',
    running: 'bg-blue-100 text-blue-700',
    completed: 'bg-green-100 text-green-700',
    failed: 'bg-red-100 text-red-700',
    cancelled: 'bg-gray-100 text-gray-600',
};
const STATUS_LABELS = {
    pending: '等待中', running: '执行中', completed: '已完成', failed: '失败', cancelled: '已取消',
};

// ── 状态 ──────────────────────────────────────────────────────────

const state = {
    activeTab: 'dataset',
    dataset: [],
    results: [],        // 评估结果列表
    compareReport: null,
    activeTasks: {},    // task_id → { ws, data }
};

// ── DOM 引用 ──────────────────────────────────────────────────────

const $ = (id) => document.getElementById(id);

// ── 工具函数 ──────────────────────────────────────────────────────

function showToast(msg, type = 'info') {
    const toast = $('toast');
    const icons = { info: 'ℹ️', success: '✅', error: '❌', warning: '⚠️' };
    $('toast-icon').textContent = icons[type] || 'ℹ️';
    $('toast-msg').textContent = msg;
    toast.classList.remove('hidden');
    setTimeout(() => toast.classList.add('hidden'), 4000);
}

function formatTime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function scoreBar(score, max = 10) {
    const pct = Math.round((score / max) * 100);
    const color = pct >= 70 ? 'bg-green-500' : pct >= 40 ? 'bg-yellow-500' : 'bg-red-500';
    return `<div class="flex items-center gap-2">
        <div class="flex-1 bg-gray-100 rounded-full h-1.5">
            <div class="${color} h-1.5 rounded-full" style="width:${pct}%"></div>
        </div>
        <span class="text-xs font-medium w-8 text-right">${score.toFixed(1)}</span>
    </div>`;
}

// ── Tab 切换 ──────────────────────────────────────────────────────

function switchTab(tabName) {
    state.activeTab = tabName;
    document.querySelectorAll('.eval-tab').forEach(btn => {
        const active = btn.dataset.tab === tabName;
        btn.classList.toggle('border-primary-600', active);
        btn.classList.toggle('text-primary-600', active);
        btn.classList.toggle('font-medium', active);
        btn.classList.toggle('border-transparent', !active);
        btn.classList.toggle('text-gray-500', !active);
    });
    ['dataset', 'results', 'compare'].forEach(t => {
        const el = $(`tab-${t}`);
        el.classList.toggle('hidden', t !== tabName);
    });
}

document.querySelectorAll('.eval-tab').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
});

// ── 数据集加载 ────────────────────────────────────────────────────

async function loadDataset(difficulty = '', category = '') {
    const params = new URLSearchParams({ limit: 50 });
    if (difficulty) params.set('difficulty', difficulty);
    if (category) params.set('category', category);

    try {
        const res = await fetch(`${API.dataset}?${params}`);
        const data = await res.json();
        state.dataset = data.tasks || [];
        renderDatasetTable(state.dataset);
        $('ds-count').textContent = `共 ${data.total} 条`;

        // 同步填充快速评估的任务选择器
        populateQuickTaskSelect(state.dataset);
    } catch (e) {
        showToast('加载数据集失败：' + e.message, 'error');
    }
}

function renderDatasetTable(tasks) {
    const tbody = $('dataset-tbody');
    if (!tasks.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="py-8 text-center text-gray-400">暂无数据</td></tr>';
        return;
    }
    tbody.innerHTML = tasks.map(t => `
        <tr class="hover:bg-gray-50 transition-colors cursor-pointer" data-task-id="${t.task_id}">
            <td class="py-2.5 pr-4 font-mono text-gray-500">${t.task_id}</td>
            <td class="py-2.5 pr-4 text-gray-700 max-w-xs">
                <span class="line-clamp-2">${escapeHtml(t.question)}</span>
            </td>
            <td class="py-2.5 pr-4">
                <span class="px-2 py-0.5 rounded-full text-xs font-medium ${DIFFICULTY_COLORS[t.difficulty] || 'bg-gray-100 text-gray-600'}">
                    ${DIFFICULTY_LABELS[t.difficulty] || t.difficulty}
                </span>
            </td>
            <td class="py-2.5 pr-4 text-gray-600">${t.category}</td>
            <td class="py-2.5 text-gray-500">${(t.expected_tools || []).join(', ') || '—'}</td>
        </tr>
    `).join('');
}

function populateQuickTaskSelect(tasks) {
    const sel = $('quick-task-select');
    sel.innerHTML = tasks.map(t =>
        `<option value="${t.task_id}">[${DIFFICULTY_LABELS[t.difficulty] || t.difficulty}] ${t.question.slice(0, 40)}...</option>`
    ).join('');
}

function escapeHtml(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// 数据集筛选
$('ds-filter-btn').addEventListener('click', () => {
    loadDataset($('ds-difficulty-filter').value, $('ds-category-filter').value);
});
$('ds-difficulty-filter').addEventListener('change', () => {
    loadDataset($('ds-difficulty-filter').value, $('ds-category-filter').value);
});
$('ds-category-filter').addEventListener('change', () => {
    loadDataset($('ds-difficulty-filter').value, $('ds-category-filter').value);
});

// ── 快速评估 ──────────────────────────────────────────────────────

$('quick-eval-btn').addEventListener('click', async () => {
    const taskId = $('quick-task-select').value;
    const agentType = $('quick-agent-type').value;
    if (!taskId) { showToast('请选择评估任务', 'warning'); return; }

    const btn = $('quick-eval-btn');
    btn.disabled = true;
    btn.innerHTML = '<svg class="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path></svg> 评估中...';

    try {
        const res = await fetch(API.quickEval, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ eval_task_id: taskId, agent_type: agentType }),
        });
        const result = await res.json();
        if (!res.ok) throw new Error(result.detail || '评估失败');
        showQuickResultModal(result);
    } catch (e) {
        showToast('快速评估失败：' + e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg> 立即评估';
    }
});

function showQuickResultModal(result) {
    const body = $('quick-result-body');
    const metrics = result.metrics || {};
    body.innerHTML = `
        <div class="space-y-4">-
            <div class="grid grid-cols-2 gap-3">
                <div class="bg-gray-50 rounded-lg p-3">
                    <p class="text-xs text-gray-400 mb-1">任务 ID</p>
                    <p class="text-xs font-mono text-gray-700">${result.task_id || '—'}</p>
                </div>
                <div class="bg-gray-50 rounded-lg p-3">
                    <p class="text-xs text-gray-400 mb-1">Agent 类型</p>
                    <p class="text-xs text-gray-700">${result.agent_type || '—'}</p>
                </div>
                <div class="bg-gray-50 rounded-lg p-3">
                    <p class="text-xs text-gray-400 mb-1">任务完成</p>
                    <p class="text-sm font-bold ${metrics.task_completed ? 'text-green-600' : 'text-red-600'}">${metrics.task_completed ? '✅ 成功' : '❌ 失败'}</p>
                </div>
                <div class="bg-gray-50 rounded-lg p-3">
                    <p class="text-xs text-gray-400 mb-1">耗时</p>
                    <p class="text-sm font-bold text-gray-700">${(metrics.elapsed_seconds || 0).toFixed(1)}s</p>
                </div>
            </div>
            ${metrics ? `
            <div class="space-y-2">
                <p class="text-xs font-semibold text-gray-600">评估指标</p>
                <div class="space-y-2">
                    <div class="flex items-center justify-between">
                        <span class="text-xs text-gray-500">LLM Judge 评分</span>
                        <div class="w-48">${scoreBar(metrics.judge_score || 0)}</div>
                    </div>
                    <div class="flex items-center justify-between">
                        <span class="text-xs text-gray-500">工具准确率</span>
                        <span class="text-xs font-medium text-gray-700">${((metrics.tool_accuracy || 0) * 100).toFixed(0)}%</span>
                    </div>
                    <div class="flex items-center justify-between">
                        <span class="text-xs text-gray-500">步骤效率</span>
                        <span class="text-xs font-medium text-gray-700">${(metrics.step_efficiency || 0).toFixed(2)}</span>
                    </div>
                    <div class="flex items-center justify-between">
                        <span class="text-xs text-gray-500">Token 效率</span>
                        <span class="text-xs font-medium text-gray-700">${(metrics.token_efficiency || 0).toFixed(2)}</span>
                    </div>
                    <div class="flex items-center justify-between">
                        <span class="text-xs text-gray-500">迭代次数</span>
                        <span class="text-xs font-medium text-gray-700">${metrics.total_iterations || 0}</span>
                    </div>
                    <div class="flex items-center justify-between">
                        <span class="text-xs text-gray-500">Token 消耗</span>
                        <span class="text-xs font-medium text-gray-700">${metrics.total_tokens || 0}</span>
                    </div>
                </div>
            </div>` : ''}
            ${result.answer ? `
            <div>
                <p class="text-xs font-semibold text-gray-600 mb-1">Agent 回答</p>
                <div class="bg-gray-50 rounded-lg p-3 text-xs text-gray-700 max-h-32 overflow-auto">${escapeHtml(result.answer)}</div>
            </div>` : ''}
            ${result.judge_reasoning ? `
            <div>
                <p class="text-xs font-semibold text-gray-600 mb-1">Judge 评语</p>
                <div class="bg-amber-50 border border-amber-200 rounded-lg p-3 text-xs text-amber-800 max-h-24 overflow-auto">${escapeHtml(result.judge_reasoning)}</div>
            </div>` : ''}
        </div>
    `;
    $('quick-result-modal').classList.remove('hidden');
}

$('close-quick-modal').addEventListener('click', () => {
    $('quick-result-modal').classList.add('hidden');
});
$('quick-result-modal').addEventListener('click', (e) => {
    if (e.target === $('quick-result-modal')) $('quick-result-modal').classList.add('hidden');
});

// ── 批量评估 ──────────────────────────────────────────────────────

$('batch-eval-btn').addEventListener('click', async () => {
    const body = {
        difficulty: $('batch-difficulty').value || null,
        category: $('batch-category').value || null,
        limit: parseInt($('batch-limit').value) || 10,
        agent_type: $('batch-agent-type').value,
    };

    try {
        const res = await fetch(API.batchEval, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '提交失败');
        showToast(`批量评估已提交，task_id: ${data.task_id.slice(0, 8)}...`, 'success');
        addTaskToProgressPanel(data.task_id, 'eval_batch');
    } catch (e) {
        showToast('提交批量评估失败：' + e.message, 'error');
    }
});

// ── 对比评估 ──────────────────────────────────────────────────────

$('compare-eval-btn').addEventListener('click', async () => {
    const body = {
        difficulty: $('compare-difficulty').value,
        limit: parseInt($('compare-limit').value) || 5,
    };

    try {
        const res = await fetch(API.compareEval, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '提交失败');
        showToast(`对比评估已提交，task_id: ${data.task_id.slice(0, 8)}...`, 'success');
        addTaskToProgressPanel(data.task_id, 'eval_compare');
    } catch (e) {
        showToast('提交对比评估失败：' + e.message, 'error');
    }
});

// ── 任务进度面板 ──────────────────────────────────────────────────

function addTaskToProgressPanel(taskId, taskType) {
    const list = $('task-progress-list');

    // 移除空提示
    const empty = list.querySelector('.text-center');
    if (empty) empty.remove();

    const card = document.createElement('div');
    card.id = `task-card-${taskId}`;
    card.className = 'bg-gray-50 border border-gray-200 rounded-lg p-3 space-y-2';
    card.innerHTML = `
        <div class="flex items-center justify-between">
            <span class="text-xs font-mono text-gray-500">${taskId.slice(0, 12)}...</span>
            <span class="task-status-badge text-xs px-1.5 py-0.5 rounded-full ${STATUS_COLORS.pending}">${STATUS_LABELS.pending}</span>
        </div>
        <div class="text-xs text-gray-500">${taskType}</div>
        <div class="w-full bg-gray-200 rounded-full h-1">
            <div class="task-progress-bar bg-primary-600 h-1 rounded-full transition-all duration-500" style="width: 0%"></div>
        </div>
        <p class="task-progress-msg text-xs text-gray-400"></p>
    `;
    list.prepend(card);

    // WebSocket 订阅
    subscribeTaskWs(taskId, card);
}

function subscribeTaskWs(taskId, card) {
    const ws = new WebSocket(API.taskWs(taskId));
    state.activeTasks[taskId] = { ws };

    ws.onmessage = (e) => {
        const data = JSON.parse(e.data);
        updateTaskCard(card, data);

        // 如果是对比评估完成，渲染对比报告
        if (data.status === 'completed' && data.result) {
            if (data.task_type === 'eval_compare' || data.result.react || data.result.plan_execute) {
                renderCompareReport(data.result);
            } else if (data.result.results) {
                renderBatchResults(data.result);
            }
        }
    };

    ws.onerror = () => {
        updateTaskCardStatus(card, 'failed');
    };

    ws.onclose = () => {
        delete state.activeTasks[taskId];
    };

    // 心跳
    const heartbeat = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send('ping');
        else clearInterval(heartbeat);
    }, 15000);
}

function updateTaskCard(card, data) {
    const status = data.status || 'pending';
    updateTaskCardStatus(card, status);

    const progressBar = card.querySelector('.task-progress-bar');
    const progressMsg = card.querySelector('.task-progress-msg');

    if (data.progress !== undefined) {
        progressBar.style.width = `${data.progress}%`;
    }
    if (data.message) {
        progressMsg.textContent = data.message;
    }
}

function updateTaskCardStatus(card, status) {
    const badge = card.querySelector('.task-status-badge');
    badge.className = `task-status-badge text-xs px-1.5 py-0.5 rounded-full ${STATUS_COLORS[status] || 'bg-gray-100 text-gray-600'}`;
    badge.textContent = STATUS_LABELS[status] || status;
}

// ── 批量结果渲染 ──────────────────────────────────────────────────

function renderBatchResults(report) {
    switchTab('results');
    const container = $('results-content');
    $('results-empty').classList.add('hidden');
    container.classList.remove('hidden');

    const results = report.results || [];
    const summary = report.summary || {};

    const summaryHtml = `
        <div class="bg-white border border-gray-200 rounded-xl p-4">
            <h3 class="text-sm font-semibold text-gray-700 mb-3">评估摘要</h3>
            <div class="grid grid-cols-4 gap-4">
                <div class="text-center">
                    <p class="text-2xl font-bold text-gray-800">${results.length}</p>
                    <p class="text-xs text-gray-400 mt-1">评估任务数</p>
                </div>
                <div class="text-center">
                    <p class="text-2xl font-bold text-green-600">${((summary.task_completion_rate || 0) * 100).toFixed(0)}%</p>
                    <p class="text-xs text-gray-400 mt-1">任务完成率</p>
                </div>
                <div class="text-center">
                    <p class="text-2xl font-bold text-primary-600">${(summary.avg_judge_score || 0).toFixed(1)}</p>
                    <p class="text-xs text-gray-400 mt-1">平均 Judge 评分</p>
                </div>
                <div class="text-center">
                    <p class="text-2xl font-bold text-gray-700">${(summary.avg_elapsed || 0).toFixed(1)}s</p>
                    <p class="text-xs text-gray-400 mt-1">平均耗时</p>
                </div>
            </div>
        </div>
    `;

    const resultsHtml = results.map(r => {
        const m = r.metrics || {};
        return `
            <div class="bg-white border border-gray-200 rounded-xl p-4">
                <div class="flex items-start justify-between mb-3">
                    <div>
                        <span class="text-xs font-mono text-gray-400">${r.task_id}</span>
                        <p class="text-sm text-gray-700 mt-1">${escapeHtml((r.question || '').slice(0, 80))}${(r.question || '').length > 80 ? '...' : ''}</p>
                    </div>
                    <span class="ml-3 flex-shrink-0 text-sm font-bold ${r.success ? 'text-green-600' : 'text-red-500'}">${r.success ? '✅' : '❌'}</span>
                </div>
                <div class="grid grid-cols-4 gap-3 text-xs">
                    <div>
                        <p class="text-gray-400">Judge 评分</p>
                        <p class="font-semibold text-gray-700">${(m.judge_score || 0).toFixed(1)}/10</p>
                    </div>
                    <div>
                        <p class="text-gray-400">工具准确率</p>
                        <p class="font-semibold text-gray-700">${((m.tool_accuracy || 0) * 100).toFixed(0)}%</p>
                    </div>
                    <div>
                        <p class="text-gray-400">迭代次数</p>
                        <p class="font-semibold text-gray-700">${m.iterations || 0}</p>
                    </div>
                    <div>
                        <p class="text-gray-400">耗时</p>
                        <p class="font-semibold text-gray-700">${(r.elapsed_seconds || 0).toFixed(1)}s</p>
                    </div>
                </div>
            </div>
        `;
    }).join('');

    container.innerHTML = summaryHtml + resultsHtml;
    state.results.push(report);
}

// ── 对比报告渲染 ──────────────────────────────────────────────────

function renderCompareReport(report) {
    switchTab('compare');
    const container = $('compare-content');
    $('compare-empty').classList.add('hidden');
    container.classList.remove('hidden');

    const react = report.react || {};
    const planExec = report.plan_execute || {};
    const reactSummary = react.summary || {};
    const planSummary = planExec.summary || {};

    function metricRow(label, reactVal, planVal, higherIsBetter = true) {
        const reactNum = parseFloat(reactVal) || 0;
        const planNum = parseFloat(planVal) || 0;
        const reactWins = higherIsBetter ? reactNum > planNum : reactNum < planNum;
        const planWins = higherIsBetter ? planNum > reactNum : planNum < reactNum;
        return `
            <tr class="border-b border-gray-100">
                <td class="py-2 pr-4 text-xs text-gray-500">${label}</td>
                <td class="py-2 pr-4 text-xs font-medium ${reactWins ? 'text-green-600' : 'text-gray-700'}">${reactVal}</td>
                <td class="py-2 text-xs font-medium ${planWins ? 'text-green-600' : 'text-gray-700'}">${planVal}</td>
            </tr>
        `;
    }

    container.innerHTML = `
        <div class="bg-white border border-gray-200 rounded-xl p-5">
            <h3 class="text-sm font-semibold text-gray-700 mb-4">ReAct vs Plan-and-Execute 对比报告</h3>
            <table class="w-full">
                <thead>
                    <tr class="border-b border-gray-200">
                        <th class="text-left pb-3 pr-4 text-xs font-semibold text-gray-400 w-40">指标</th>
                        <th class="text-left pb-3 pr-4 text-xs font-semibold text-indigo-600">ReAct</th>
                        <th class="text-left pb-3 text-xs font-semibold text-violet-600">Plan-and-Execute</th>
                    </tr>
                </thead>
                <tbody>
                    ${metricRow('任务完成率', `${((reactSummary.task_completion_rate || 0) * 100).toFixed(0)}%`, `${((planSummary.task_completion_rate || 0) * 100).toFixed(0)}%`)}
                    ${metricRow('平均 Judge 评分', (reactSummary.avg_judge_score || 0).toFixed(2), (planSummary.avg_judge_score || 0).toFixed(2))}
                    ${metricRow('平均工具准确率', `${((reactSummary.avg_tool_accuracy || 0) * 100).toFixed(0)}%`, `${((planSummary.avg_tool_accuracy || 0) * 100).toFixed(0)}%`)}
                    ${metricRow('平均步骤效率', (reactSummary.avg_step_efficiency || 0).toFixed(3), (planSummary.avg_step_efficiency || 0).toFixed(3))}
                    ${metricRow('平均 Token 效率', (reactSummary.avg_token_efficiency || 0).toFixed(3), (planSummary.avg_token_efficiency || 0).toFixed(3))}
                    ${metricRow('平均耗时', `${(reactSummary.avg_elapsed || 0).toFixed(1)}s`, `${(planSummary.avg_elapsed || 0).toFixed(1)}s`, false)}
                    ${metricRow('平均 Token 消耗', reactSummary.avg_tokens || 0, planSummary.avg_tokens || 0, false)}
                </tbody>
            </table>
        </div>
        ${report.winner ? `
        <div class="bg-green-50 border border-green-200 rounded-xl p-4">
            <p class="text-sm font-semibold text-green-700">综合胜者：${report.winner === 'react' ? 'ReAct' : 'Plan-and-Execute'}</p>
            ${report.analysis ? `<p class="text-xs text-green-600 mt-1">${escapeHtml(report.analysis)}</p>` : ''}
        </div>` : ''}
    `;
    state.compareReport = report;
}

// ── 初始化 ────────────────────────────────────────────────────────

loadDataset();
