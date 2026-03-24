/**
 * MCP 管理页面前端逻辑
 *
 * 功能：
 *  - Tab 切换
 *  - 加载 MCP Server 工具列表 & 资源列表
 *  - 连接外部 MCP Server（MCP Client）
 *  - 工具测试调用
 *  - Cursor 配置展示 & 复制
 */

const API = '/api/v1/mcp';

/* ── 工具函数 ─────────────────────────────────────────────── */

async function apiFetch(path, options = {}) {
    const res = await fetch(API + path, {
        headers: { 'Content-Type': 'application/json' },
        ...options,
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || res.statusText);
    }
    return res.json();
}

function showToast(msg, type = 'info') {
    const colors = {
        info: 'bg-primary-600',
        success: 'bg-emerald-600',
        error: 'bg-red-500',
    };
    const el = document.createElement('div');
    el.className = `fixed bottom-6 right-6 z-50 text-white text-sm px-4 py-2.5 rounded-xl shadow-lg transition-all ${colors[type] || colors.info}`;
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3000);
}

function setLoading(btn, loading) {
    if (loading) {
        btn.dataset.origText = btn.innerHTML;
        btn.innerHTML = '<span class="spinner"></span><span>处理中...</span>';
        btn.disabled = true;
    } else {
        btn.innerHTML = btn.dataset.origText || btn.innerHTML;
        btn.disabled = false;
    }
}

/* ── Tab 切换 ─────────────────────────────────────────────── */

function initTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tab-btn').forEach(b => {
                b.classList.remove('active');
                b.classList.add('text-gray-500');
                b.classList.remove('text-gray-700');
            });
            btn.classList.add('active');
            btn.classList.remove('text-gray-500');

            const target = btn.dataset.tab;
            document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
            document.getElementById(`tab-${target}`).classList.add('active');

            // 按需加载
            if (target === 'client') loadConnections();
            if (target === 'config') loadCursorConfig();
        });
    });
}

/* ── 服务状态检测 ─────────────────────────────────────────── */

async function checkServerStatus() {
    const dot = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    try {
        await apiFetch('/tools');
        dot.className = 'status-dot ok';
        text.textContent = 'MCP Server 运行中';
    } catch {
        dot.className = 'status-dot err';
        text.textContent = 'MCP Server 异常';
    }
}

/* ── Server Tab：工具列表 ─────────────────────────────────── */

async function loadTools() {
    const container = document.getElementById('tools-list');
    const countEl = document.getElementById('tools-count');
    try {
        const data = await apiFetch('/tools');
        const tools = data.tools || [];
        countEl.textContent = tools.length;

        if (tools.length === 0) {
            container.innerHTML = '<div class="text-sm text-gray-400 col-span-2 py-4 text-center">暂无工具</div>';
            return;
        }

        container.innerHTML = tools.map(t => `
            <div class="tool-card border border-gray-200 rounded-xl p-4 cursor-pointer hover:bg-gray-50"
                 onclick="fillTestTool('${t.name}')">
                <div class="flex items-start justify-between gap-2">
                    <div class="text-sm font-medium text-gray-800 truncate">${t.name}</div>
                    <span class="text-xs bg-primary-50 text-primary-600 px-2 py-0.5 rounded flex-shrink-0">工具</span>
                </div>
                <div class="text-xs text-gray-500 mt-1.5 line-clamp-2">${t.description || '—'}</div>
                ${buildParamBadges(t.inputSchema || t.input_schema)}
            </div>
        `).join('');

        // 同步填充测试 Tab 的下拉框
        populateTestSelect(tools);
    } catch (e) {
        container.innerHTML = `<div class="text-sm text-red-500 col-span-2 py-4 text-center">加载失败：${e.message}</div>`;
    }
}

function buildParamBadges(schema) {
    if (!schema || !schema.properties) return '';
    const keys = Object.keys(schema.properties);
    if (keys.length === 0) return '';
    const required = schema.required || [];
    const badges = keys.slice(0, 4).map(k => {
        const isReq = required.includes(k);
        return `<span class="inline-block text-xs px-1.5 py-0.5 rounded mr-1 mt-1 ${isReq ? 'bg-amber-50 text-amber-700' : 'bg-gray-100 text-gray-500'}">${k}</span>`;
    });
    const more = keys.length > 4 ? `<span class="text-xs text-gray-400">+${keys.length - 4}</span>` : '';
    return `<div class="mt-2">${badges.join('')}${more}</div>`;
}

/* ── Server Tab：资源列表 ─────────────────────────────────── */

async function loadResources() {
    const container = document.getElementById('resources-list');
    const countEl = document.getElementById('resources-count');
    try {
        const data = await apiFetch('/resources');
        const resources = data.resources || [];
        countEl.textContent = resources.length;

        if (resources.length === 0) {
            container.innerHTML = '<div class="text-sm text-gray-400 py-4 text-center">暂无资源</div>';
            return;
        }

        container.innerHTML = resources.map(r => `
            <div class="flex items-start gap-3 border border-gray-100 rounded-xl p-3 hover:bg-gray-50 transition-colors">
                <div class="w-8 h-8 bg-emerald-50 rounded-lg flex items-center justify-center flex-shrink-0">
                    <svg class="w-4 h-4 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                              d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582 4 8 4s8 1.79 8 4"/>
                    </svg>
                </div>
                <div class="flex-1 min-w-0">
                    <div class="text-sm font-medium text-gray-800">${r.name || r.uri}</div>
                    <div class="text-xs text-gray-400 font-mono truncate mt-0.5">${r.uri}</div>
                    <div class="text-xs text-gray-500 mt-0.5">${r.description || ''}</div>
                </div>
                <span class="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded flex-shrink-0">${r.mimeType || r.mime_type || 'text'}</span>
            </div>
        `).join('');
    } catch (e) {
        container.innerHTML = `<div class="text-sm text-red-500 py-4 text-center">加载失败：${e.message}</div>`;
    }
}

/* ── Client Tab：连接管理 ─────────────────────────────────── */

async function loadConnections() {
    const container = document.getElementById('connections-list');
    try {
        const data = await apiFetch('/client/connections');
        const conns = data.connections || [];

        if (conns.length === 0) {
            container.innerHTML = '<div class="text-sm text-gray-400 py-8 text-center">暂无连接</div>';
            return;
        }

        container.innerHTML = conns.map(c => `
            <div class="border border-gray-200 rounded-xl p-4 mb-3">
                <div class="flex items-center justify-between mb-2">
                    <span class="text-sm font-medium text-gray-800">${c.name}</span>
                    <span class="conn-badge connected">
                        <span class="w-1.5 h-1.5 bg-emerald-500 rounded-full"></span>
                        已连接
                    </span>
                </div>
                <div class="text-xs text-gray-500 mb-2">${c.tool_count} 个工具已发现并注册</div>
                <div class="flex flex-wrap gap-1">
                    ${(c.tools || []).map(t => `<span class="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded">${t}</span>`).join('')}
                </div>
            </div>
        `).join('');
    } catch (e) {
        container.innerHTML = `<div class="text-sm text-red-500 py-4 text-center">加载失败：${e.message}</div>`;
    }
}

function initClientConnect() {
    const btn = document.getElementById('connect-btn');
    const resultEl = document.getElementById('connect-result');

    btn.addEventListener('click', async () => {
        const name = document.getElementById('conn-name').value.trim();
        const url = document.getElementById('conn-url').value.trim();

        if (!name || !url) {
            showToast('请填写连接名称和 Server URL', 'error');
            return;
        }

        setLoading(btn, true);
        resultEl.className = 'hidden rounded-lg p-3 text-sm';

        try {
            const data = await apiFetch('/client/connect', {
                method: 'POST',
                body: JSON.stringify({ name, server_url: url }),
            });
            resultEl.className = 'rounded-lg p-3 text-sm bg-emerald-50 text-emerald-800 border border-emerald-200';
            resultEl.innerHTML = `
                <div class="font-medium mb-1">${data.message}</div>
                <div class="flex flex-wrap gap-1 mt-2">
                    ${(data.tools || []).map(t => `<span class="text-xs bg-emerald-100 text-emerald-700 px-2 py-0.5 rounded">${t}</span>`).join('')}
                </div>
            `;
            showToast('连接成功', 'success');
            loadConnections();
            // 刷新工具列表（新工具已注册）
            loadTools();
        } catch (e) {
            resultEl.className = 'rounded-lg p-3 text-sm bg-red-50 text-red-700 border border-red-200';
            resultEl.textContent = `连接失败：${e.message}`;
            showToast('连接失败', 'error');
        } finally {
            setLoading(btn, false);
        }
    });

    // 快捷示例
    document.querySelectorAll('.quick-connect').forEach(el => {
        el.addEventListener('click', () => {
            document.getElementById('conn-name').value = el.dataset.name;
            document.getElementById('conn-url').value = el.dataset.url;
        });
    });
}

/* ── Test Tab：工具测试 ───────────────────────────────────── */

let _toolsCache = [];

function populateTestSelect(tools) {
    _toolsCache = tools;
    const sel = document.getElementById('test-tool-select');
    sel.innerHTML = '<option value="">— 选择工具 —</option>' +
        tools.map(t => `<option value="${t.name}">${t.name}</option>`).join('');
}

function fillTestTool(name) {
    // 切换到 Test Tab
    document.querySelectorAll('.tab-btn').forEach(b => {
        b.classList.remove('active');
        b.classList.add('text-gray-500');
    });
    const testBtn = document.querySelector('[data-tab="test"]');
    testBtn.classList.add('active');
    testBtn.classList.remove('text-gray-500');
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    document.getElementById('tab-test').classList.add('active');

    const sel = document.getElementById('test-tool-select');
    sel.value = name;
    sel.dispatchEvent(new Event('change'));
}

function initTestTab() {
    const sel = document.getElementById('test-tool-select');
    const schemaEl = document.getElementById('test-tool-schema');
    const argsEl = document.getElementById('test-args');

    sel.addEventListener('change', () => {
        const name = sel.value;
        const tool = _toolsCache.find(t => t.name === name);
        if (!tool) { schemaEl.textContent = ''; return; }

        const schema = tool.inputSchema || tool.input_schema;
        if (schema && schema.properties) {
            const keys = Object.keys(schema.properties);
            schemaEl.textContent = `参数：${keys.join(', ')}`;
            // 自动生成示例 JSON
            const example = {};
            keys.forEach(k => {
                const prop = schema.properties[k];
                if (prop.type === 'number' || prop.type === 'integer') example[k] = 0;
                else if (prop.type === 'boolean') example[k] = false;
                else example[k] = '';
            });
            argsEl.value = JSON.stringify(example, null, 2);
        } else {
            schemaEl.textContent = '无参数';
            argsEl.value = '{}';
        }
    });

    const callBtn = document.getElementById('test-call-btn');
    const resultWrap = document.getElementById('test-result-wrap');
    const badge = document.getElementById('test-status-badge');

    callBtn.addEventListener('click', async () => {
        const name = sel.value;
        if (!name) { showToast('请选择工具', 'error'); return; }

        let args = {};
        try {
            args = JSON.parse(argsEl.value || '{}');
        } catch {
            showToast('参数 JSON 格式有误', 'error');
            return;
        }

        setLoading(callBtn, true);
        badge.className = 'hidden';
        resultWrap.innerHTML = '<div class="flex items-center gap-2 text-sm text-gray-400 py-8 justify-center"><span class="spinner"></span> 调用中...</div>';

        try {
            const data = await apiFetch('/tools/call', {
                method: 'POST',
                body: JSON.stringify({ name, arguments: args }),
            });

            const result = data.result || data;
            const isError = result.isError || false;
            const content = (result.content || []).map(c => c.text || '').join('\n') || JSON.stringify(result, null, 2);

            badge.className = `conn-badge ml-auto ${isError ? 'conn-badge disconnected' : 'conn-badge connected'}`;
            badge.innerHTML = `<span class="w-1.5 h-1.5 rounded-full ${isError ? 'bg-red-500' : 'bg-emerald-500'}"></span>${isError ? '失败' : '成功'}`;

            resultWrap.innerHTML = `<pre class="json-pre">${escapeHtml(content)}</pre>`;
        } catch (e) {
            badge.className = 'conn-badge conn-badge disconnected ml-auto';
            badge.innerHTML = '<span class="w-1.5 h-1.5 bg-red-500 rounded-full"></span>错误';
            resultWrap.innerHTML = `<div class="text-sm text-red-600 bg-red-50 rounded-xl p-4">${escapeHtml(e.message)}</div>`;
        } finally {
            setLoading(callBtn, false);
        }
    });
}

function escapeHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

/* ── Config Tab：Cursor 配置 ──────────────────────────────── */

async function loadCursorConfig() {
    const stdioEl = document.getElementById('stdio-config-pre');
    const sseEl = document.getElementById('sse-config-pre');
    try {
        const data = await apiFetch('/cursor-config');
        stdioEl.textContent = JSON.stringify(data.stdio_config, null, 2);
        sseEl.textContent = JSON.stringify(data.sse_config, null, 2);
    } catch (e) {
        stdioEl.textContent = `加载失败：${e.message}`;
        sseEl.textContent = `加载失败：${e.message}`;
    }
}

function initCopyBtns() {
    document.querySelectorAll('.copy-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const target = document.getElementById(btn.dataset.target);
            if (!target) return;
            navigator.clipboard.writeText(target.textContent).then(() => {
                const orig = btn.textContent;
                btn.textContent = '已复制！';
                btn.classList.add('bg-emerald-50', 'text-emerald-600', 'border-emerald-300');
                setTimeout(() => {
                    btn.textContent = orig;
                    btn.classList.remove('bg-emerald-50', 'text-emerald-600', 'border-emerald-300');
                }, 2000);
            }).catch(() => showToast('复制失败，请手动复制', 'error'));
        });
    });
}

/* ── 刷新按钮 ─────────────────────────────────────────────── */

function initRefresh() {
    document.getElementById('refresh-btn').addEventListener('click', () => {
        loadTools();
        loadResources();
        checkServerStatus();
        showToast('已刷新', 'info');
    });
}

/* ── 初始化 ───────────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    initRefresh();
    initClientConnect();
    initTestTab();
    initCopyBtns();

    checkServerStatus();
    loadTools();
    loadResources();
});
