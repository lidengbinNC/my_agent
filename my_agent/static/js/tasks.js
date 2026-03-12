/**
 * tasks.js — 异步任务队列监控前端逻辑
 *
 * 功能：
 *  - 任务列表展示（支持状态筛选）
 *  - 任务统计数据（pending/running/completed/failed/cancelled）
 *  - 点击任务行查看详情 + WebSocket 实时日志
 *  - 取消 pending 任务
 *  - 自动刷新（可关闭）
 *  - 完整结果 Modal 展示
 */

// ── 常量 ──────────────────────────────────────────────────────────

const API = {
    tasks: '/api/v1/tasks',
    taskDetail: (id) => `/api/v1/tasks/${id}`,
    cancelTask: (id) => `/api/v1/tasks/${id}`,
    taskWs: (id) => `ws://${location.host}/api/v1/tasks/${id}/ws`,
    taskStats: '/api/v1/tasks/stats',
};

const STATUS_COLORS = {
    pending: 'bg-yellow-100 text-yellow-700',
    running: 'bg-blue-100 text-blue-700',
    completed: 'bg-green-100 text-green-700',
    failed: 'bg-red-100 text-red-700',
    cancelled: 'bg-gray-100 text-gray-600',
};

const STATUS_LABELS = {
    pending: '等待中',
    running: '执行中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
};

const TYPE_LABELS = {
    agent_chat: '对话',
    eval_single: '单任务评估',
    eval_batch: '批量评估',
    eval_compare: '对比评估',
};

// ── 状态 ──────────────────────────────────────────────────────────

const state = {
    tasks: [],
    selectedTaskId: null,
    statusFilter: '',
    activeWs: null,
    autoRefreshTimer: null,
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
    return d.toLocaleString('zh-CN', {
        month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
}

function escapeHtml(str) {
    if (typeof str !== 'string') return String(str);
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function elapsed(startIso, endIso) {
    if (!startIso) return '—';
    const start = new Date(startIso);
    const end = endIso ? new Date(endIso) : new Date();
    const sec = Math.round((end - start) / 1000);
    if (sec < 60) return `${sec}s`;
    return `${Math.floor(sec / 60)}m ${sec % 60}s`;
}

// ── 统计数据 ──────────────────────────────────────────────────────

async function loadStats() {
    try {
        const res = await fetch(API.taskStats);
        const data = await res.json();
        $('stat-total').textContent = data.total ?? '—';
        $('stat-pending').textContent = data.pending ?? 0;
        $('stat-running').textContent = data.running ?? 0;
        $('stat-completed').textContent = data.completed ?? 0;
        $('stat-failed').textContent = data.failed ?? 0;
        $('stat-cancelled').textContent = data.cancelled ?? 0;
    } catch (e) {
        console.error('加载统计失败', e);
    }
}

// ── 任务列表 ──────────────────────────────────────────────────────

async function loadTasks() {
    const params = new URLSearchParams({ limit: 50 });
    if (state.statusFilter) params.set('status', state.statusFilter);

    try {
        const res = await fetch(`${API.tasks}?${params}`);
        const data = await res.json();
        state.tasks = data.tasks || [];
        renderTaskTable(state.tasks);
        $('task-count').textContent = `共 ${data.total} 条`;
    } catch (e) {
        console.error('加载任务列表失败', e);
    }
}

function renderTaskTable(tasks) {
    const tbody = $('tasks-tbody');
    if (!tasks.length) {
        tbody.innerHTML = `<tr><td colspan="6" class="py-12 text-center text-gray-400">暂无任务记录</td></tr>`;
        return;
    }

    tbody.innerHTML = tasks.map(t => {
        const progress = t.progress !== undefined ? t.progress : null;
        const progressHtml = progress !== null
            ? `<div class="flex items-center gap-2">
                <div class="flex-1 bg-gray-100 rounded-full h-1.5">
                    <div class="${t.status === 'failed' ? 'bg-red-500' : 'bg-primary-600'} h-1.5 rounded-full" style="width:${progress}%"></div>
                </div>
                <span class="text-xs text-gray-500 w-8 text-right">${progress}%</span>
               </div>`
            : '<span class="text-gray-300">—</span>';

        return `
            <tr class="hover:bg-gray-50 transition-colors cursor-pointer task-row ${state.selectedTaskId === t.task_id ? 'bg-primary-50' : ''}"
                data-task-id="${t.task_id}">
                <td class="py-3 pr-4 font-mono text-gray-500 text-xs">${t.task_id.slice(0, 20)}...</td>
                <td class="py-3 pr-4 text-gray-600">${TYPE_LABELS[t.task_type] || t.task_type}</td>
                <td class="py-3 pr-4">
                    <span class="px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_COLORS[t.status] || 'bg-gray-100 text-gray-600'}">
                        ${t.status === 'running' ? '<span class="inline-block w-1.5 h-1.5 bg-blue-500 rounded-full animate-pulse mr-1"></span>' : ''}
                        ${STATUS_LABELS[t.status] || t.status}
                    </span>
                </td>
                <td class="py-3 pr-4 w-48">${progressHtml}</td>
                <td class="py-3 pr-4 text-gray-500">${formatTime(t.created_at)}</td>
                <td class="py-3">
                    ${t.status === 'pending'
                        ? `<button class="cancel-task-btn text-xs text-red-500 hover:text-red-700 hover:bg-red-50 px-2 py-1 rounded-lg transition-colors border border-red-200" data-task-id="${t.task_id}">取消</button>`
                        : `<span class="text-xs text-gray-300">${elapsed(t.started_at, t.finished_at)}</span>`
                    }
                </td>
            </tr>
        `;
    }).join('');

    // 行点击 → 查看详情
    tbody.querySelectorAll('.task-row').forEach(row => {
        row.addEventListener('click', (e) => {
            if (e.target.closest('.cancel-task-btn')) return;
            selectTask(row.dataset.taskId);
        });
    });

    // 取消按钮
    tbody.querySelectorAll('.cancel-task-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            cancelTask(btn.dataset.taskId);
        });
    });
}

// ── 状态筛选 ──────────────────────────────────────────────────────

document.querySelectorAll('.status-filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        state.statusFilter = btn.dataset.status;

        document.querySelectorAll('.status-filter-btn').forEach(b => {
            const active = b === btn;
            b.classList.toggle('active', active);
            b.classList.toggle('border-primary-300', active);
            b.classList.toggle('bg-primary-50', active);
            b.classList.toggle('text-primary-700', active);
            b.classList.toggle('border-gray-200', !active);
            b.classList.toggle('text-gray-600', !active);
        });

        loadTasks();
    });
});

// ── 取消任务 ──────────────────────────────────────────────────────

async function cancelTask(taskId) {
    try {
        const res = await fetch(API.cancelTask(taskId), { method: 'DELETE' });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || '取消失败');
        showToast('任务已取消', 'success');
        await loadTasks();
        await loadStats();
    } catch (e) {
        showToast('取消失败：' + e.message, 'error');
    }
}

// ── 任务详情 ──────────────────────────────────────────────────────

async function selectTask(taskId) {
    state.selectedTaskId = taskId;

    // 高亮选中行
    document.querySelectorAll('.task-row').forEach(row => {
        row.classList.toggle('bg-primary-50', row.dataset.taskId === taskId);
    });

    // 关闭旧 WebSocket
    if (state.activeWs) {
        state.activeWs.close();
        state.activeWs = null;
    }

    // 显示详情面板
    $('detail-empty').classList.add('hidden');
    $('detail-content').classList.remove('hidden');

    // 加载任务数据
    try {
        const res = await fetch(API.taskDetail(taskId));
        const task = await res.json();
        renderDetailPanel(task);

        // 如果任务还在运行，开启 WebSocket
        if (['pending', 'running'].includes(task.status)) {
            connectDetailWs(taskId);
        }
    } catch (e) {
        showToast('加载任务详情失败：' + e.message, 'error');
    }
}

function renderDetailPanel(task) {
    // 状态徽章
    const badge = $('detail-status-badge');
    badge.className = `text-xs px-2 py-0.5 rounded-full font-medium ${STATUS_COLORS[task.status] || 'bg-gray-100 text-gray-600'}`;
    badge.textContent = STATUS_LABELS[task.status] || task.status;

    // 取消按钮
    const cancelBtn = $('detail-cancel-btn');
    if (task.status === 'pending') {
        cancelBtn.classList.remove('hidden');
        cancelBtn.onclick = () => cancelTask(task.task_id);
    } else {
        cancelBtn.classList.add('hidden');
    }

    $('detail-task-id').textContent = task.task_id;
    $('detail-type').textContent = TYPE_LABELS[task.task_type] || task.task_type;
    $('detail-priority').textContent = task.priority ?? '—';
    $('detail-created-at').textContent = formatTime(task.created_at);

    // 进度条
    if (task.progress !== undefined) {
        $('detail-progress-bar-wrap').classList.remove('hidden');
        $('detail-progress-bar').style.width = `${task.progress}%`;
        $('detail-progress-msg').textContent = task.message || '';
    } else {
        $('detail-progress-bar-wrap').classList.add('hidden');
    }

    // 结果预览
    if (task.result) {
        $('detail-result-wrap').classList.remove('hidden');
        const preview = typeof task.result === 'string'
            ? task.result.slice(0, 200)
            : JSON.stringify(task.result, null, 2).slice(0, 200);
        $('detail-result-preview').textContent = preview + (preview.length >= 200 ? '...' : '');
        $('detail-view-result-btn').onclick = () => showResultModal(task.result);
    } else {
        $('detail-result-wrap').classList.add('hidden');
    }

    // 清空日志
    $('ws-log').innerHTML = '<div class="text-gray-400 text-center py-4">等待日志...</div>';
    $('ws-status').textContent = '';
}

function connectDetailWs(taskId) {
    const ws = new WebSocket(API.taskWs(taskId));
    state.activeWs = ws;
    $('ws-status').textContent = '连接中...';

    ws.onopen = () => {
        $('ws-status').textContent = '● 已连接';
        $('ws-status').className = 'text-xs text-green-500';
    };

    ws.onmessage = (e) => {
        const data = JSON.parse(e.data);
        if (data.type === 'pong') return;

        appendWsLog(data);

        // 更新进度条
        if (data.progress !== undefined) {
            $('detail-progress-bar-wrap').classList.remove('hidden');
            $('detail-progress-bar').style.width = `${data.progress}%`;
        }
        if (data.message) {
            $('detail-progress-msg').textContent = data.message;
        }

        // 更新状态徽章
        if (data.status) {
            const badge = $('detail-status-badge');
            badge.className = `text-xs px-2 py-0.5 rounded-full font-medium ${STATUS_COLORS[data.status] || 'bg-gray-100 text-gray-600'}`;
            badge.textContent = STATUS_LABELS[data.status] || data.status;
        }

        // 任务完成
        if (['completed', 'failed', 'cancelled'].includes(data.status)) {
            $('ws-status').textContent = '● 已断开';
            $('ws-status').className = 'text-xs text-gray-400';
            if (data.result) {
                $('detail-result-wrap').classList.remove('hidden');
                const preview = typeof data.result === 'string'
                    ? data.result.slice(0, 200)
                    : JSON.stringify(data.result, null, 2).slice(0, 200);
                $('detail-result-preview').textContent = preview + (preview.length >= 200 ? '...' : '');
                $('detail-view-result-btn').onclick = () => showResultModal(data.result);
            }
            // 刷新列表和统计
            loadTasks();
            loadStats();
        }
    };

    ws.onerror = () => {
        $('ws-status').textContent = '● 连接失败';
        $('ws-status').className = 'text-xs text-red-500';
    };

    ws.onclose = () => {
        if (state.activeWs === ws) {
            $('ws-status').textContent = '● 已断开';
            $('ws-status').className = 'text-xs text-gray-400';
        }
    };

    // 心跳
    const heartbeat = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send('ping');
        else clearInterval(heartbeat);
    }, 15000);
}

function appendWsLog(data) {
    const log = $('ws-log');
    const empty = log.querySelector('.text-center');
    if (empty) empty.remove();

    const time = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    const statusClass = data.status === 'failed' ? 'text-red-500'
        : data.status === 'completed' ? 'text-green-600'
        : 'text-gray-600';

    const line = document.createElement('div');
    line.className = `flex gap-2 ${statusClass}`;
    line.innerHTML = `
        <span class="text-gray-400 flex-shrink-0">${time}</span>
        <span class="flex-1 break-all">${escapeHtml(data.message || data.status || JSON.stringify(data))}</span>
    `;
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;
}

// ── 结果 Modal ────────────────────────────────────────────────────

function showResultModal(result) {
    const body = $('result-modal-body');
    const pre = body.querySelector('pre');
    pre.textContent = typeof result === 'string' ? result : JSON.stringify(result, null, 2);
    $('result-modal').classList.remove('hidden');
}

$('close-result-modal').addEventListener('click', () => {
    $('result-modal').classList.add('hidden');
});
$('result-modal').addEventListener('click', (e) => {
    if (e.target === $('result-modal')) $('result-modal').classList.add('hidden');
});

// ── 刷新控制 ──────────────────────────────────────────────────────

function startAutoRefresh() {
    stopAutoRefresh();
    state.autoRefreshTimer = setInterval(async () => {
        await loadTasks();
        await loadStats();
    }, 5000);
}

function stopAutoRefresh() {
    if (state.autoRefreshTimer) {
        clearInterval(state.autoRefreshTimer);
        state.autoRefreshTimer = null;
    }
}

$('refresh-btn').addEventListener('click', async () => {
    await loadTasks();
    await loadStats();
});

$('auto-refresh').addEventListener('change', (e) => {
    if (e.target.checked) startAutoRefresh();
    else stopAutoRefresh();
});

// ── 初始化 ────────────────────────────────────────────────────────

async function init() {
    await Promise.all([loadStats(), loadTasks()]);
    startAutoRefresh();
}

init();
