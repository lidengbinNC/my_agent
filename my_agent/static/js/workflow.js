/**
 * MyAgent 工作流画布交互
 *
 * 面试考点:
 *   - DAG 可视化：节点拖拽定位、SVG 贝塞尔曲线连线
 *   - 拖拽 API：dragstart/dragover/drop + mousedown/mousemove/mouseup
 *   - SSE 流式接收工作流执行事件，实时更新节点状态
 *   - Human-in-the-Loop：审批弹窗 + approve/reject API
 *   - 画布缩放/平移：CSS transform + wheel 事件
 */

// ─────────────────────────────────────────────
//  状态
// ─────────────────────────────────────────────
const state = {
    // 画布变换
    scale: 1,
    panX: 0,
    panY: 0,
    isPanning: false,
    panStart: { x: 0, y: 0 },

    // 工作流数据
    nodes: [],          // { node_id, name, node_type, config, description, position:{x,y} }
    edges: [],          // { edge_id, source, target, condition, condition_expr, label }
    workflowId: null,   // 已保存的工作流 ID
    workflowName: '新工作流',
    workflowDesc: '',

    // 选中
    selectedNodeId: null,
    selectedEdgeId: null,

    // 连线绘制
    drawingEdge: false,
    drawEdgeFrom: null,   // node_id
    drawEdgeLine: null,   // SVG path element

    // 节点拖拽
    draggingNodeId: null,
    dragOffset: { x: 0, y: 0 },

    // 运行
    currentRunId: null,
    isRunning: false,

    // 审批
    pendingApprovalToken: null,
};

// ─────────────────────────────────────────────
//  DOM 引用
// ─────────────────────────────────────────────
const canvasWrap   = document.getElementById('canvas-wrap');
const canvasInner  = document.getElementById('canvas-inner');
const edgesSvg     = document.getElementById('edges-svg');
const wfList       = document.getElementById('wf-list');
const runLog       = document.getElementById('run-log');
const pendingList  = document.getElementById('pending-list');
const runsList     = document.getElementById('runs-list');
const canvasHint   = document.getElementById('canvas-hint');
const tooltip      = document.getElementById('tooltip');

const btnNewWf     = document.getElementById('btn-new-wf');
const btnSaveWf    = document.getElementById('btn-save-wf');
const btnRunWf     = document.getElementById('btn-run-wf');
const btnZoomIn    = document.getElementById('btn-zoom-in');
const btnZoomOut   = document.getElementById('btn-zoom-out');
const btnFit       = document.getElementById('btn-fit');
const btnDeleteNode= document.getElementById('btn-delete-node');
const btnDeleteEdge= document.getElementById('btn-delete-edge');
const btnRefreshPending = document.getElementById('btn-refresh-pending');
const btnRefreshRuns    = document.getElementById('btn-refresh-runs');
const btnClearLog  = document.getElementById('btn-clear-log');

const wfNameInput  = document.getElementById('wf-name-input');
const wfDescInput  = document.getElementById('wf-desc-input');
const wfGoalInput  = document.getElementById('wf-goal-input');
const wfNameBadge  = document.getElementById('wf-name-badge');

const nodePropsSection = document.getElementById('node-props-section');
const edgePropsSection = document.getElementById('edge-props-section');
const wfPropsSection   = document.getElementById('wf-props-section');
const nodePropsForm    = document.getElementById('node-props-form');
const edgeCondSelect   = document.getElementById('edge-condition-select');
const edgeExprRow      = document.getElementById('edge-expr-row');
const edgeExprInput    = document.getElementById('edge-expr-input');
const edgeLabelInput   = document.getElementById('edge-label-input');

const runModal         = document.getElementById('run-modal');
const runGoalInput     = document.getElementById('run-goal-input');
const btnRunCancel     = document.getElementById('btn-run-cancel');
const btnRunConfirm    = document.getElementById('btn-run-confirm');

const approvalModal    = document.getElementById('approval-modal');
const approvalContent  = document.getElementById('approval-content');
const approvalComment  = document.getElementById('approval-comment');
const btnApprove       = document.getElementById('btn-approve');
const btnReject        = document.getElementById('btn-reject');

const nodeCountEl  = document.getElementById('node-count');
const edgeCountEl  = document.getElementById('edge-count');
const zoomLevelEl  = document.getElementById('zoom-level');
const canvasStatus = document.getElementById('canvas-status');

// ─────────────────────────────────────────────
//  节点类型配置
// ─────────────────────────────────────────────
const NODE_CONFIG = {
    agent:     { icon: '🤖', color: '#6366f1', bg: '#eef2ff', label: 'Agent 节点' },
    tool:      { icon: '⚡', color: '#0ea5e9', bg: '#f0f9ff', label: '工具节点' },
    condition: { icon: '🔀', color: '#f59e0b', bg: '#fffbeb', label: '条件节点' },
    human:     { icon: '👤', color: '#f97316', bg: '#fff7ed', label: '人工审批' },
    start:     { icon: '▶',  color: '#10b981', bg: '#ecfdf5', label: '开始' },
    end:       { icon: '⏹',  color: '#64748b', bg: '#f8fafc', label: '结束' },
};

// ─────────────────────────────────────────────
//  初始化
// ─────────────────────────────────────────────
(async () => {
    applyTransform();
    await loadWorkflowList();
    setupCanvasEvents();
    setupPaletteEvents();
    setupSidebarTabs();
    setupTemplateButtons();
    setupHeaderButtons();
    setupPropertyPanelEvents();
    setupApprovalModal();
    await loadPendingApprovals();
})();

// ─────────────────────────────────────────────
//  画布变换
// ─────────────────────────────────────────────
function applyTransform() {
    canvasInner.style.transform = `translate(${state.panX}px, ${state.panY}px) scale(${state.scale})`;
    zoomLevelEl.textContent = `缩放: ${Math.round(state.scale * 100)}%`;
}

function zoom(delta, cx, cy) {
    const newScale = Math.min(2, Math.max(0.3, state.scale + delta));
    if (newScale === state.scale) return;
    const ratio = newScale / state.scale;
    const rect = canvasWrap.getBoundingClientRect();
    const ox = cx !== undefined ? cx - rect.left : rect.width / 2;
    const oy = cy !== undefined ? cy - rect.top  : rect.height / 2;
    state.panX = ox - ratio * (ox - state.panX);
    state.panY = oy - ratio * (oy - state.panY);
    state.scale = newScale;
    applyTransform();
}

function fitView() {
    if (state.nodes.length === 0) return;
    const xs = state.nodes.map(n => n.position.x);
    const ys = state.nodes.map(n => n.position.y);
    const minX = Math.min(...xs) - 40;
    const minY = Math.min(...ys) - 40;
    const maxX = Math.max(...xs) + 200;
    const maxY = Math.max(...ys) + 100;
    const rect = canvasWrap.getBoundingClientRect();
    const scaleX = rect.width  / (maxX - minX);
    const scaleY = rect.height / (maxY - minY);
    state.scale = Math.min(1, scaleX, scaleY) * 0.9;
    state.panX = (rect.width  - (maxX - minX) * state.scale) / 2 - minX * state.scale;
    state.panY = (rect.height - (maxY - minY) * state.scale) / 2 - minY * state.scale;
    applyTransform();
}

// ─────────────────────────────────────────────
//  画布事件
// ─────────────────────────────────────────────
function setupCanvasEvents() {
    // 滚轮缩放
    canvasWrap.addEventListener('wheel', (e) => {
        e.preventDefault();
        zoom(e.deltaY < 0 ? 0.1 : -0.1, e.clientX, e.clientY);
    }, { passive: false });

    // 平移（鼠标中键或空格+拖拽）
    canvasWrap.addEventListener('mousedown', (e) => {
        if (e.button === 1 || (e.button === 0 && e.target === canvasWrap)) {
            state.isPanning = true;
            state.panStart = { x: e.clientX - state.panX, y: e.clientY - state.panY };
            canvasWrap.classList.add('panning');
            e.preventDefault();
        }
    });
    window.addEventListener('mousemove', (e) => {
        if (state.isPanning) {
            state.panX = e.clientX - state.panStart.x;
            state.panY = e.clientY - state.panStart.y;
            applyTransform();
        }
        if (state.draggingNodeId) {
            const node = state.nodes.find(n => n.node_id === state.draggingNodeId);
            if (node) {
                const rect = canvasWrap.getBoundingClientRect();
                node.position.x = (e.clientX - rect.left - state.panX) / state.scale - state.dragOffset.x;
                node.position.y = (e.clientY - rect.top  - state.panY) / state.scale - state.dragOffset.y;
                renderNode(node);
                renderEdges();
            }
        }
        if (state.drawingEdge && state.drawEdgeLine) {
            const rect = canvasWrap.getBoundingClientRect();
            const mx = (e.clientX - rect.left - state.panX) / state.scale;
            const my = (e.clientY - rect.top  - state.panY) / state.scale;
            const fromNode = state.nodes.find(n => n.node_id === state.drawEdgeFrom);
            if (fromNode) {
                const sx = fromNode.position.x + 80;
                const sy = fromNode.position.y + 70;
                state.drawEdgeLine.setAttribute('d', bezierPath(sx, sy, mx, my));
            }
        }
    });
    window.addEventListener('mouseup', (e) => {
        if (state.isPanning) {
            state.isPanning = false;
            canvasWrap.classList.remove('panning');
        }
        if (state.draggingNodeId) {
            state.draggingNodeId = null;
            markDirty();
        }
        if (state.drawingEdge) {
            // 取消绘制中的临时线
            state.drawEdgeLine?.remove();
            state.drawEdgeLine = null;
            state.drawingEdge = false;
            state.drawEdgeFrom = null;
        }
    });

    // 点击空白处取消选中
    canvasWrap.addEventListener('click', (e) => {
        if (e.target === canvasWrap || e.target === canvasInner || e.target === edgesSvg) {
            deselectAll();
        }
    });

    // 拖拽放置节点
    canvasWrap.addEventListener('dragover', (e) => e.preventDefault());
    canvasWrap.addEventListener('drop', (e) => {
        e.preventDefault();
        const nodeType = e.dataTransfer.getData('node-type');
        if (!nodeType) return;
        const rect = canvasWrap.getBoundingClientRect();
        const x = (e.clientX - rect.left - state.panX) / state.scale - 80;
        const y = (e.clientY - rect.top  - state.panY) / state.scale - 35;
        addNode(nodeType, { x, y });
    });
}

// ─────────────────────────────────────────────
//  调色板拖拽
// ─────────────────────────────────────────────
function setupPaletteEvents() {
    document.querySelectorAll('.palette-item').forEach(item => {
        item.addEventListener('dragstart', (e) => {
            e.dataTransfer.setData('node-type', item.dataset.nodeType);
        });
    });
}

// ─────────────────────────────────────────────
//  侧边栏 Tab
// ─────────────────────────────────────────────
function setupSidebarTabs() {
    document.querySelectorAll('.sidebar-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.sidebar-tab').forEach(t => {
                t.classList.remove('text-primary-600', 'border-primary-600');
                t.classList.add('text-gray-500', 'border-transparent');
            });
            tab.classList.add('text-primary-600', 'border-primary-600');
            tab.classList.remove('text-gray-500', 'border-transparent');

            ['tab-props', 'tab-run', 'tab-runs'].forEach(id => {
                document.getElementById(id).classList.add('hidden');
            });
            document.getElementById(`tab-${tab.dataset.tab}`).classList.remove('hidden');

            if (tab.dataset.tab === 'runs') loadRunHistory();
        });
    });
}

// ─────────────────────────────────────────────
//  快速模板
// ─────────────────────────────────────────────
const TEMPLATES = {
    linear: {
        name: '线性流程示例',
        desc: '顺序执行：开始 → Agent → 工具 → 结束',
        nodes: [
            { node_id: 'start1',  name: '开始',       node_type: 'start',  position: { x: 200, y: 80  }, config: {}, description: '' },
            { node_id: 'agent1',  name: 'Agent 分析',  node_type: 'agent',  position: { x: 200, y: 200 }, config: { agent_type: 'react', prompt: '分析用户输入的目标' }, description: '使用 ReAct 引擎分析任务' },
            { node_id: 'tool1',   name: '计算器',      node_type: 'tool',   position: { x: 200, y: 320 }, config: { tool_name: 'calculator', tool_args: {} }, description: '执行数学计算' },
            { node_id: 'end1',    name: '结束',        node_type: 'end',    position: { x: 200, y: 440 }, config: {}, description: '' },
        ],
        edges: [
            { source: 'start1', target: 'agent1', condition: 'default', label: '' },
            { source: 'agent1', target: 'tool1',  condition: 'on_success', label: '成功' },
            { source: 'tool1',  target: 'end1',   condition: 'default', label: '' },
        ],
    },
    branch: {
        name: '条件分支示例',
        desc: '根据 Agent 输出结果走不同分支',
        nodes: [
            { node_id: 'start1',  name: '开始',      node_type: 'start',     position: { x: 240, y: 60  }, config: {}, description: '' },
            { node_id: 'agent1',  name: '任务分析',   node_type: 'agent',     position: { x: 240, y: 180 }, config: { agent_type: 'react', prompt: '分析任务并给出判断' }, description: '' },
            { node_id: 'cond1',   name: '结果判断',   node_type: 'condition', position: { x: 240, y: 300 }, config: { condition_expr: "output contains '成功'" }, description: '判断分析结果' },
            { node_id: 'tool_ok', name: '成功处理',   node_type: 'tool',      position: { x: 100, y: 420 }, config: { tool_name: 'calculator', tool_args: {} }, description: '成功路径' },
            { node_id: 'tool_fail','name': '失败处理', node_type: 'tool',     position: { x: 380, y: 420 }, config: { tool_name: 'web_search', tool_args: { query: '重试方案' } }, description: '失败路径' },
            { node_id: 'end1',    name: '结束',       node_type: 'end',       position: { x: 240, y: 540 }, config: {}, description: '' },
        ],
        edges: [
            { source: 'start1',   target: 'agent1',    condition: 'default',    label: '' },
            { source: 'agent1',   target: 'cond1',     condition: 'default',    label: '' },
            { source: 'cond1',    target: 'tool_ok',   condition: 'on_success', label: '是' },
            { source: 'cond1',    target: 'tool_fail', condition: 'on_failure', label: '否' },
            { source: 'tool_ok',  target: 'end1',      condition: 'default',    label: '' },
            { source: 'tool_fail',target: 'end1',      condition: 'default',    label: '' },
        ],
    },
    human_review: {
        name: '人工审批示例',
        desc: 'Agent 生成内容 → 人工审批 → 执行',
        nodes: [
            { node_id: 'start1',  name: '开始',      node_type: 'start',  position: { x: 200, y: 60  }, config: {}, description: '' },
            { node_id: 'agent1',  name: '内容生成',   node_type: 'agent',  position: { x: 200, y: 180 }, config: { agent_type: 'react', prompt: '根据目标生成执行方案' }, description: 'Agent 生成方案' },
            { node_id: 'human1',  name: '人工审批',   node_type: 'human',  position: { x: 200, y: 300 }, config: { prompt: '请审批以下执行方案', timeout_seconds: 3600 }, description: 'Human-in-the-Loop 审批节点' },
            { node_id: 'tool1',   name: '执行方案',   node_type: 'tool',   position: { x: 200, y: 420 }, config: { tool_name: 'web_search', tool_args: {} }, description: '审批通过后执行' },
            { node_id: 'end1',    name: '结束',       node_type: 'end',    position: { x: 200, y: 540 }, config: {}, description: '' },
        ],
        edges: [
            { source: 'start1', target: 'agent1', condition: 'default',    label: '' },
            { source: 'agent1', target: 'human1', condition: 'on_success', label: '生成完成' },
            { source: 'human1', target: 'tool1',  condition: 'on_success', label: '已批准' },
            { source: 'tool1',  target: 'end1',   condition: 'default',    label: '' },
        ],
    },
};

function setupTemplateButtons() {
    document.querySelectorAll('.tpl-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const tpl = TEMPLATES[btn.dataset.tpl];
            if (!tpl) return;
            if (state.nodes.length > 0 && !confirm('加载模板将清空当前画布，确认继续？')) return;
            loadTemplate(tpl);
        });
    });
}

function loadTemplate(tpl) {
    clearCanvas();
    state.workflowName = tpl.name;
    state.workflowDesc = tpl.desc;
    wfNameInput.value = tpl.name;
    wfDescInput.value = tpl.desc;

    tpl.nodes.forEach(n => {
        state.nodes.push({ ...n, position: { ...n.position } });
        renderNode(state.nodes[state.nodes.length - 1]);
    });
    tpl.edges.forEach(e => {
        const edge = {
            edge_id: genId(),
            source: e.source,
            target: e.target,
            condition: e.condition || 'default',
            condition_expr: e.condition_expr || '',
            label: e.label || '',
        };
        state.edges.push(edge);
    });
    renderEdges();
    updateCounts();
    markDirty();
    setTimeout(fitView, 50);
    hideHint();
}

// ─────────────────────────────────────────────
//  Header 按钮
// ─────────────────────────────────────────────
function setupHeaderButtons() {
    btnNewWf.addEventListener('click', () => {
        if (state.nodes.length > 0 && !confirm('新建将清空当前画布，确认继续？')) return;
        clearCanvas();
        state.workflowId = null;
        state.workflowName = '新工作流';
        wfNameInput.value = '新工作流';
        wfDescInput.value = '';
        wfGoalInput.value = '';
        wfNameBadge.classList.add('hidden');
        btnSaveWf.disabled = false;
        btnRunWf.disabled = true;
        updateCounts();
    });

    btnSaveWf.addEventListener('click', saveWorkflow);
    btnRunWf.addEventListener('click', () => {
        runGoalInput.value = wfGoalInput.value;
        runModal.classList.remove('hidden');
    });
    btnRunCancel.addEventListener('click', () => runModal.classList.add('hidden'));
    btnRunConfirm.addEventListener('click', () => {
        runModal.classList.add('hidden');
        runWorkflow(runGoalInput.value.trim());
    });

    btnZoomIn.addEventListener('click',  () => zoom(0.15));
    btnZoomOut.addEventListener('click', () => zoom(-0.15));
    btnFit.addEventListener('click',     fitView);

    btnDeleteNode.addEventListener('click', deleteSelectedNode);
    btnDeleteEdge.addEventListener('click', deleteSelectedEdge);

    btnRefreshPending.addEventListener('click', loadPendingApprovals);
    btnRefreshRuns.addEventListener('click',    loadRunHistory);
    btnClearLog.addEventListener('click', () => {
        runLog.innerHTML = '<div class="text-gray-400">等待运行...</div>';
    });

    wfNameInput.addEventListener('input', () => {
        state.workflowName = wfNameInput.value;
        markDirty();
    });
    wfDescInput.addEventListener('input', () => {
        state.workflowDesc = wfDescInput.value;
        markDirty();
    });
}

// ─────────────────────────────────────────────
//  属性面板事件
// ─────────────────────────────────────────────
function setupPropertyPanelEvents() {
    edgeCondSelect.addEventListener('change', () => {
        const edge = state.edges.find(e => e.edge_id === state.selectedEdgeId);
        if (edge) {
            edge.condition = edgeCondSelect.value;
            edgeExprRow.classList.toggle('hidden', edge.condition !== 'expr');
            renderEdges();
            markDirty();
        }
    });
    edgeExprInput.addEventListener('input', () => {
        const edge = state.edges.find(e => e.edge_id === state.selectedEdgeId);
        if (edge) { edge.condition_expr = edgeExprInput.value; markDirty(); }
    });
    edgeLabelInput.addEventListener('input', () => {
        const edge = state.edges.find(e => e.edge_id === state.selectedEdgeId);
        if (edge) { edge.label = edgeLabelInput.value; renderEdges(); markDirty(); }
    });
}

// ─────────────────────────────────────────────
//  审批弹窗
// ─────────────────────────────────────────────
function setupApprovalModal() {
    btnApprove.addEventListener('click', async () => {
        if (!state.pendingApprovalToken) return;
        await approveNode(state.pendingApprovalToken, true, approvalComment.value);
        approvalModal.classList.remove('show');
        state.pendingApprovalToken = null;
    });
    btnReject.addEventListener('click', async () => {
        if (!state.pendingApprovalToken) return;
        await approveNode(state.pendingApprovalToken, false, approvalComment.value);
        approvalModal.classList.remove('show');
        state.pendingApprovalToken = null;
    });
}

// ─────────────────────────────────────────────
//  节点操作
// ─────────────────────────────────────────────
let _nodeSeq = 1;
function genId() { return `n${Date.now()}_${_nodeSeq++}`; }

function addNode(nodeType, position) {
    const cfg = NODE_CONFIG[nodeType] || NODE_CONFIG.agent;
    const node = {
        node_id: genId(),
        name: cfg.label,
        node_type: nodeType,
        config: defaultConfig(nodeType),
        description: '',
        position: { x: Math.round(position.x), y: Math.round(position.y) },
    };
    state.nodes.push(node);
    renderNode(node);
    updateCounts();
    markDirty();
    hideHint();
    selectNode(node.node_id);
}

function defaultConfig(nodeType) {
    switch (nodeType) {
        case 'agent':     return { agent_type: 'react', prompt: '' };
        case 'tool':      return { tool_name: 'calculator', tool_args: {} };
        case 'condition': return { condition_expr: '' };
        case 'human':     return { prompt: '请审批以下内容', timeout_seconds: 3600 };
        default:          return {};
    }
}

function renderNode(node) {
    const cfg = NODE_CONFIG[node.node_type] || NODE_CONFIG.agent;
    let el = document.getElementById(`node-${node.node_id}`);
    if (!el) {
        el = document.createElement('div');
        el.id = `node-${node.node_id}`;
        el.className = 'wf-node';
        canvasInner.appendChild(el);

        // 节点拖拽
        el.addEventListener('mousedown', (e) => {
            if (e.target.classList.contains('port')) return;
            e.stopPropagation();
            state.draggingNodeId = node.node_id;
            const rect = el.getBoundingClientRect();
            const canvasRect = canvasWrap.getBoundingClientRect();
            state.dragOffset.x = (e.clientX - canvasRect.left - state.panX) / state.scale - node.position.x;
            state.dragOffset.y = (e.clientY - canvasRect.top  - state.panY) / state.scale - node.position.y;
            selectNode(node.node_id);
        });

        el.addEventListener('click', (e) => {
            e.stopPropagation();
            selectNode(node.node_id);
        });
    }

    el.style.left = `${node.position.x}px`;
    el.style.top  = `${node.position.y}px`;
    el.innerHTML = `
        <div class="node-header" style="background:${cfg.bg}">
            <span style="font-size:16px;line-height:1">${cfg.icon}</span>
            <div class="flex-1 min-w-0">
                <div class="text-xs font-semibold truncate" style="color:${cfg.color}">${escHtml(node.name)}</div>
                <div class="text-gray-400" style="font-size:10px">${cfg.label}</div>
            </div>
        </div>
        ${node.description ? `<div class="node-body">${escHtml(node.description)}</div>` : ''}
        <!-- 连接端口 -->
        <div class="port port-out" title="拖拽连线到目标节点" data-node="${node.node_id}" data-port="out"></div>
        <div class="port port-in"  title="接收来自其他节点的连线" data-node="${node.node_id}" data-port="in"></div>`;

    // 端口事件
    el.querySelector('.port-out').addEventListener('mousedown', (e) => {
        e.stopPropagation();
        startDrawEdge(node.node_id, e);
    });
    el.querySelector('.port-in').addEventListener('mouseup', (e) => {
        e.stopPropagation();
        if (state.drawingEdge && state.drawEdgeFrom !== node.node_id) {
            finishDrawEdge(node.node_id);
        }
    });
}

function startDrawEdge(fromNodeId, e) {
    state.drawingEdge = true;
    state.drawEdgeFrom = fromNodeId;
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    line.classList.add('wf-edge', 'drawing');
    edgesSvg.appendChild(line);
    state.drawEdgeLine = line;
}

function finishDrawEdge(toNodeId) {
    if (state.drawEdgeLine) {
        state.drawEdgeLine.remove();
        state.drawEdgeLine = null;
    }
    state.drawingEdge = false;

    // 防止重复连线
    const exists = state.edges.some(
        e => e.source === state.drawEdgeFrom && e.target === toNodeId
    );
    if (!exists) {
        const edge = {
            edge_id: genId(),
            source: state.drawEdgeFrom,
            target: toNodeId,
            condition: 'default',
            condition_expr: '',
            label: '',
        };
        state.edges.push(edge);
        renderEdges();
        updateCounts();
        markDirty();
        selectEdge(edge.edge_id);
    }
    state.drawEdgeFrom = null;
}

function deleteSelectedNode() {
    if (!state.selectedNodeId) return;
    const id = state.selectedNodeId;
    document.getElementById(`node-${id}`)?.remove();
    state.nodes = state.nodes.filter(n => n.node_id !== id);
    state.edges = state.edges.filter(e => e.source !== id && e.target !== id);
    state.selectedNodeId = null;
    renderEdges();
    updateCounts();
    showWfProps();
    markDirty();
}

function deleteSelectedEdge() {
    if (!state.selectedEdgeId) return;
    state.edges = state.edges.filter(e => e.edge_id !== state.selectedEdgeId);
    state.selectedEdgeId = null;
    renderEdges();
    updateCounts();
    showWfProps();
    markDirty();
}

// ─────────────────────────────────────────────
//  边渲染
// ─────────────────────────────────────────────
function renderEdges() {
    // 清除旧边（保留 defs 和临时绘制线）
    Array.from(edgesSvg.querySelectorAll('.wf-edge:not(.drawing)')).forEach(el => el.remove());
    Array.from(edgesSvg.querySelectorAll('.edge-label')).forEach(el => el.remove());

    state.edges.forEach(edge => {
        const fromNode = state.nodes.find(n => n.node_id === edge.source);
        const toNode   = state.nodes.find(n => n.node_id === edge.target);
        if (!fromNode || !toNode) return;

        const sx = fromNode.position.x + 80;
        const sy = fromNode.position.y + 70;
        const tx = toNode.position.x + 80;
        const ty = toNode.position.y;

        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', bezierPath(sx, sy, tx, ty));
        path.classList.add('wf-edge');
        if (edge.condition === 'on_success') path.classList.add('success');
        if (edge.condition === 'on_failure') path.classList.add('failure');
        if (edge.edge_id === state.selectedEdgeId) {
            path.style.stroke = '#6366f1';
            path.style.strokeWidth = '3';
        }
        path.style.markerEnd = edge.condition === 'on_success'
            ? 'url(#arrow-success)'
            : edge.condition === 'on_failure'
            ? 'url(#arrow-failure)'
            : 'url(#arrow)';

        // 点击选中边
        path.style.pointerEvents = 'stroke';
        path.style.cursor = 'pointer';
        path.addEventListener('click', (e) => {
            e.stopPropagation();
            selectEdge(edge.edge_id);
        });
        edgesSvg.appendChild(path);

        // 边标签
        if (edge.label) {
            const mx = (sx + tx) / 2;
            const my = (sy + ty) / 2;
            const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
            text.setAttribute('x', mx);
            text.setAttribute('y', my - 4);
            text.setAttribute('text-anchor', 'middle');
            text.setAttribute('font-size', '10');
            text.setAttribute('fill', '#64748b');
            text.classList.add('edge-label');
            text.textContent = edge.label;
            edgesSvg.appendChild(text);
        }
    });
}

function bezierPath(sx, sy, tx, ty) {
    const dy = Math.abs(ty - sy) * 0.5;
    return `M${sx},${sy} C${sx},${sy + dy} ${tx},${ty - dy} ${tx},${ty}`;
}

// ─────────────────────────────────────────────
//  选中逻辑
// ─────────────────────────────────────────────
function selectNode(nodeId) {
    deselectAll();
    state.selectedNodeId = nodeId;
    document.getElementById(`node-${nodeId}`)?.classList.add('selected');
    showNodeProps(nodeId);
}

function selectEdge(edgeId) {
    deselectAll();
    state.selectedEdgeId = edgeId;
    renderEdges();
    showEdgeProps(edgeId);
}

function deselectAll() {
    state.selectedNodeId = null;
    state.selectedEdgeId = null;
    document.querySelectorAll('.wf-node.selected').forEach(el => el.classList.remove('selected'));
    renderEdges();
    showWfProps();
}

// ─────────────────────────────────────────────
//  属性面板渲染
// ─────────────────────────────────────────────
function showWfProps() {
    wfPropsSection.classList.remove('hidden');
    nodePropsSection.classList.add('hidden');
    edgePropsSection.classList.add('hidden');
}

function showNodeProps(nodeId) {
    const node = state.nodes.find(n => n.node_id === nodeId);
    if (!node) return;
    wfPropsSection.classList.add('hidden');
    nodePropsSection.classList.remove('hidden');
    edgePropsSection.classList.add('hidden');

    const cfg = NODE_CONFIG[node.node_type] || NODE_CONFIG.agent;
    nodePropsForm.innerHTML = `
        <div>
            <div class="prop-label">节点名称</div>
            <input type="text" class="prop-input" id="np-name" value="${escHtml(node.name)}">
        </div>
        <div>
            <div class="prop-label">节点类型</div>
            <div class="flex items-center gap-2 text-xs text-gray-600 bg-gray-50 rounded-lg px-3 py-2">
                <span>${cfg.icon}</span><span>${cfg.label}</span>
            </div>
        </div>
        <div>
            <div class="prop-label">描述</div>
            <textarea class="prop-input" id="np-desc" rows="2" style="resize:none;field-sizing:unset">${escHtml(node.description)}</textarea>
        </div>
        ${renderNodeConfigFields(node)}`;

    // 绑定通用字段
    document.getElementById('np-name').addEventListener('input', (e) => {
        node.name = e.target.value;
        renderNode(node);
        markDirty();
    });
    document.getElementById('np-desc').addEventListener('input', (e) => {
        node.description = e.target.value;
        renderNode(node);
        markDirty();
    });
    // 绑定配置字段
    bindNodeConfigEvents(node);
}

function renderNodeConfigFields(node) {
    switch (node.node_type) {
        case 'agent':
            return `
                <div>
                    <div class="prop-label">Agent 类型</div>
                    <select class="prop-input" id="np-agent-type">
                        <option value="react" ${node.config.agent_type === 'react' ? 'selected' : ''}>ReAct</option>
                        <option value="plan_execute" ${node.config.agent_type === 'plan_execute' ? 'selected' : ''}>Plan-and-Execute</option>
                    </select>
                </div>
                <div>
                    <div class="prop-label">系统 Prompt</div>
                    <textarea class="prop-input" id="np-prompt" rows="3" placeholder="Agent 的任务指令..." style="resize:none;field-sizing:unset">${escHtml(node.config.prompt || '')}</textarea>
                </div>`;
        case 'tool':
            return `
                <div>
                    <div class="prop-label">工具名称</div>
                    <select class="prop-input" id="np-tool-name">
                        <option value="calculator"  ${node.config.tool_name === 'calculator'  ? 'selected' : ''}>calculator — 数学计算</option>
                        <option value="web_search"  ${node.config.tool_name === 'web_search'  ? 'selected' : ''}>web_search — 网络搜索</option>
                        <option value="code_executor" ${node.config.tool_name === 'code_executor' ? 'selected' : ''}>code_executor — 代码执行</option>
                        <option value="http_request" ${node.config.tool_name === 'http_request' ? 'selected' : ''}>http_request — HTTP 请求</option>
                    </select>
                </div>
                <div>
                    <div class="prop-label">工具参数（JSON）</div>
                    <textarea class="prop-input font-mono" id="np-tool-args" rows="3" placeholder="{}" style="resize:none;field-sizing:unset;font-size:11px">${escHtml(JSON.stringify(node.config.tool_args || {}, null, 2))}</textarea>
                </div>`;
        case 'condition':
            return `
                <div>
                    <div class="prop-label">条件表达式</div>
                    <input type="text" class="prop-input" id="np-cond-expr" value="${escHtml(node.config.condition_expr || '')}" placeholder="如: output contains '成功'">
                    <div class="text-gray-400 mt-1" style="font-size:10px">支持: contains / not contains / == / > / &lt;</div>
                </div>`;
        case 'human':
            return `
                <div>
                    <div class="prop-label">审批提示</div>
                    <textarea class="prop-input" id="np-human-prompt" rows="2" style="resize:none;field-sizing:unset">${escHtml(node.config.prompt || '')}</textarea>
                </div>
                <div>
                    <div class="prop-label">超时时间（秒）</div>
                    <input type="number" class="prop-input" id="np-timeout" value="${node.config.timeout_seconds || 3600}" min="60">
                </div>`;
        default:
            return '';
    }
}

function bindNodeConfigEvents(node) {
    const bind = (id, key, transform) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.addEventListener('input', (e) => {
            try {
                node.config[key] = transform ? transform(e.target.value) : e.target.value;
                markDirty();
            } catch (_) {}
        });
    };
    switch (node.node_type) {
        case 'agent':
            bind('np-agent-type', 'agent_type');
            bind('np-prompt', 'prompt');
            break;
        case 'tool':
            bind('np-tool-name', 'tool_name');
            bind('np-tool-args', 'tool_args', v => JSON.parse(v));
            break;
        case 'condition':
            bind('np-cond-expr', 'condition_expr');
            break;
        case 'human':
            bind('np-human-prompt', 'prompt');
            bind('np-timeout', 'timeout_seconds', v => parseInt(v));
            break;
    }
}

function showEdgeProps(edgeId) {
    const edge = state.edges.find(e => e.edge_id === edgeId);
    if (!edge) return;
    wfPropsSection.classList.add('hidden');
    nodePropsSection.classList.add('hidden');
    edgePropsSection.classList.remove('hidden');

    edgeCondSelect.value = edge.condition;
    edgeExprInput.value  = edge.condition_expr || '';
    edgeLabelInput.value = edge.label || '';
    edgeExprRow.classList.toggle('hidden', edge.condition !== 'expr');
}

// ─────────────────────────────────────────────
//  工作流 API
// ─────────────────────────────────────────────
async function loadWorkflowList() {
    try {
        const resp = await fetch('/api/v1/workflows');
        if (!resp.ok) return;
        const list = await resp.json();
        if (list.length === 0) {
            wfList.innerHTML = '<div class="text-xs text-gray-400 px-2 py-3 text-center">暂无工作流</div>';
            return;
        }
        wfList.innerHTML = list.map(wf => `
            <div class="wf-list-item ${wf.workflow_id === state.workflowId ? 'active' : ''}"
                 data-id="${wf.workflow_id}" title="${escHtml(wf.description || '')}">
                <div class="min-w-0">
                    <div class="text-xs font-medium text-gray-700 truncate">${escHtml(wf.name)}</div>
                    <div class="text-gray-400" style="font-size:10px">${wf.node_count} 节点 · ${wf.edge_count} 连线</div>
                </div>
                <button class="wf-delete-btn text-gray-300 hover:text-red-500 transition-colors flex-shrink-0 ml-1" data-id="${wf.workflow_id}" title="删除">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
                    </svg>
                </button>
            </div>`).join('');

        wfList.querySelectorAll('.wf-list-item').forEach(item => {
            item.addEventListener('click', (e) => {
                if (e.target.closest('.wf-delete-btn')) return;
                loadWorkflow(item.dataset.id);
            });
        });
        wfList.querySelectorAll('.wf-delete-btn').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                if (!confirm('确认删除该工作流？')) return;
                await fetch(`/api/v1/workflows/${btn.dataset.id}`, { method: 'DELETE' });
                if (state.workflowId === btn.dataset.id) {
                    clearCanvas();
                    state.workflowId = null;
                    btnRunWf.disabled = true;
                }
                await loadWorkflowList();
            });
        });
    } catch (_) {}
}

async function loadWorkflow(workflowId) {
    try {
        const resp = await fetch(`/api/v1/workflows/${workflowId}`);
        if (!resp.ok) return;
        const wf = await resp.json();

        // 这里 API 只返回 WorkflowInfo（无节点详情），需要从 store 获取完整定义
        // 实际项目中应有 GET /workflows/{id}/detail 端点，此处用已有端点模拟
        // 暂时只更新元数据，完整节点数据需后端扩展
        state.workflowId = workflowId;
        state.workflowName = wf.name;
        wfNameInput.value = wf.name;
        wfDescInput.value = wf.description || '';
        wfNameBadge.textContent = wf.name;
        wfNameBadge.classList.remove('hidden');
        btnRunWf.disabled = false;
        btnSaveWf.disabled = false;

        wfList.querySelectorAll('.wf-list-item').forEach(item => {
            item.classList.toggle('active', item.dataset.id === workflowId);
        });
        setStatus('已加载', 'green');
    } catch (_) {}
}

async function saveWorkflow() {
    if (state.nodes.length === 0) {
        alert('请先添加节点');
        return;
    }
    btnSaveWf.disabled = true;
    btnSaveWf.textContent = '保存中...';

    const body = {
        name: wfNameInput.value || '新工作流',
        description: wfDescInput.value || '',
        nodes: state.nodes.map(n => ({
            node_id:   n.node_id,
            name:      n.name,
            node_type: n.node_type,
            config:    n.config,
            description: n.description,
            position:  n.position,
        })),
        edges: state.edges.map(e => ({
            source:         e.source,
            target:         e.target,
            condition:      e.condition,
            condition_expr: e.condition_expr,
            label:          e.label,
        })),
    };

    try {
        let resp, data;
        if (state.workflowId) {
            // 更新：先删后建（后端无 PUT 端点时的简化方案）
            await fetch(`/api/v1/workflows/${state.workflowId}`, { method: 'DELETE' });
        }
        resp = await fetch('/api/v1/workflows', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        data = await resp.json();
        if (!resp.ok) {
            alert(`保存失败: ${data.detail || JSON.stringify(data)}`);
            return;
        }
        state.workflowId = data.workflow_id;
        wfNameBadge.textContent = data.name;
        wfNameBadge.classList.remove('hidden');
        btnRunWf.disabled = false;
        setStatus('已保存', 'green');
        await loadWorkflowList();
        logLine('done', `✅ 工作流已保存 (${data.workflow_id})`);
    } catch (err) {
        alert(`保存出错: ${err.message}`);
    } finally {
        btnSaveWf.disabled = false;
        btnSaveWf.innerHTML = `
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7H5a2 2 0 00-2 2v9a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-3m-1 4l-3 3m0 0l-3-3m3 3V4"/>
            </svg>
            保存`;
    }
}

// ─────────────────────────────────────────────
//  运行工作流（SSE）
// ─────────────────────────────────────────────
async function runWorkflow(goal) {
    if (!state.workflowId) {
        alert('请先保存工作流');
        return;
    }
    if (state.isRunning) return;
    state.isRunning = true;
    btnRunWf.disabled = true;

    // 切换到执行日志 Tab
    document.querySelector('.sidebar-tab[data-tab="run"]').click();
    runLog.innerHTML = '';
    logLine('start', `▶ 开始运行工作流: ${escHtml(state.workflowName)}`);
    if (goal) logLine('start', `🎯 目标: ${escHtml(goal)}`);

    // 重置节点状态样式
    state.nodes.forEach(n => {
        const el = document.getElementById(`node-${n.node_id}`);
        if (el) el.className = 'wf-node';
    });

    try {
        const resp = await fetch(`/api/v1/workflows/${state.workflowId}/run`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ goal: goal || '执行工作流', stream: true }),
        });
        if (!resp.ok) {
            const err = await resp.json();
            logLine('failed', `❌ 请求失败: ${err.detail || resp.status}`);
            return;
        }

        const reader  = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let currentEvent = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (line.startsWith('event: ')) {
                    currentEvent = line.slice(7).trim();
                } else if (line.startsWith('data: ')) {
                    let data;
                    try { data = JSON.parse(line.slice(6)); } catch { continue; }
                    handleWorkflowEvent(currentEvent, data);
                }
            }
        }
    } catch (err) {
        logLine('failed', `❌ 连接错误: ${err.message}`);
    } finally {
        state.isRunning = false;
        btnRunWf.disabled = false;
        setStatus('就绪', 'gray');
    }
}

function handleWorkflowEvent(eventType, data) {
    switch (eventType) {
        case 'thinking': {
            const nodeId   = data.node_id;
            const nodeName = data.node_name || nodeId || '';
            const nodeType = data.node_type || '';
            const msg      = data.message || '';
            const runId    = data.run_id;

            if (runId) {
                state.currentRunId = runId;
                logLine('start', `🆔 Run ID: ${runId}`);
            }

            if (nodeId) {
                setNodeStatus(nodeId, 'running');
                logLine('running', `⟳ [${nodeName}] 执行中...`);
            } else if (msg) {
                logLine('running', `  ${msg}`);
            }
            setStatus('执行中', 'amber');
            break;
        }
        case 'tool_result': {
            const nodeId    = data.node_id;
            const nodeName  = data.node_name || nodeId || '';
            const output    = data.output || '';
            const humanToken = data.human_token;

            if (humanToken) {
                // Human-in-the-Loop 暂停
                setNodeStatus(nodeId, 'waiting');
                logLine('waiting', `⏸ [${nodeName}] 等待人工审批 (token: ${humanToken})`);
                showApprovalModal(humanToken, output || data.message || '请审批');
            } else {
                setNodeStatus(nodeId, 'success');
                logLine('success', `✅ [${nodeName}] 完成: ${output.slice(0, 80)}${output.length > 80 ? '…' : ''}`);
            }
            break;
        }
        case 'content': {
            // 最终输出片段
            if (data.delta) {
                logLine('done', data.delta.replace(/\n/g, ' '));
            }
            break;
        }
        case 'done': {
            logLine('done', `✅ 工作流执行完成`);
            setStatus('完成', 'green');
            loadRunHistory();
            break;
        }
        case 'error': {
            const nodeId = data.node_id;
            if (nodeId) setNodeStatus(nodeId, 'failed');
            logLine('failed', `❌ 错误: ${data.error || '未知错误'}`);
            setStatus('失败', 'red');
            break;
        }
    }
}

function setNodeStatus(nodeId, status) {
    const el = document.getElementById(`node-${nodeId}`);
    if (!el) return;
    el.classList.remove('running', 'success', 'failed', 'waiting', 'skipped');
    if (status !== 'pending') el.classList.add(status);
}

// ─────────────────────────────────────────────
//  Human-in-the-Loop
// ─────────────────────────────────────────────
function showApprovalModal(token, content) {
    state.pendingApprovalToken = token;
    approvalContent.textContent = content;
    approvalComment.value = '';
    approvalModal.classList.add('show');
}

async function approveNode(token, approved, comment) {
    const endpoint = approved ? 'approve' : 'reject';
    try {
        const resp = await fetch(`/api/v1/workflows/human/${token}/${endpoint}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ comment: comment || '' }),
        });
        const data = await resp.json();
        logLine(approved ? 'success' : 'failed',
            `${approved ? '✅ 已批准' : '✗ 已拒绝'} (token: ${token}) ${comment ? '— ' + comment : ''}`);
        await loadPendingApprovals();
    } catch (err) {
        logLine('failed', `❌ 审批请求失败: ${err.message}`);
    }
}

async function loadPendingApprovals() {
    try {
        const resp = await fetch('/api/v1/workflows/human/pending');
        if (!resp.ok) return;
        const data = await resp.json();
        const tokens = data.tokens || [];
        if (tokens.length === 0) {
            pendingList.innerHTML = '<div class="text-xs text-gray-400 text-center py-2">暂无待审批项</div>';
            return;
        }
        pendingList.innerHTML = tokens.map(token => `
            <div class="border border-amber-200 rounded-lg p-2.5 bg-amber-50">
                <div class="text-xs text-amber-700 font-medium mb-1.5">⏸ 等待审批</div>
                <div class="font-mono text-gray-500 mb-2" style="font-size:10px;word-break:break-all">${token}</div>
                <div class="flex gap-1.5">
                    <button class="flex-1 py-1 text-xs rounded border border-red-200 text-red-600 hover:bg-red-50 transition-colors"
                            onclick="approveNode('${token}', false, '')">拒绝</button>
                    <button class="flex-1 py-1 text-xs rounded bg-emerald-600 text-white hover:bg-emerald-700 transition-colors"
                            onclick="approveNode('${token}', true, '')">批准</button>
                </div>
            </div>`).join('');
    } catch (_) {}
}

// ─────────────────────────────────────────────
//  运行历史
// ─────────────────────────────────────────────
async function loadRunHistory() {
    if (!state.workflowId) return;
    try {
        const resp = await fetch(`/api/v1/workflows/${state.workflowId}/runs`);
        if (!resp.ok) return;
        const runs = await resp.json();
        if (runs.length === 0) {
            runsList.innerHTML = '<div class="text-xs text-gray-400 text-center py-2">暂无运行记录</div>';
            return;
        }
        const statusIcon = { pending: '⏳', running: '⟳', success: '✅', failed: '❌', rejected: '✗' };
        const statusColor = { pending: 'text-gray-500', running: 'text-amber-600', success: 'text-emerald-600', failed: 'text-red-600', rejected: 'text-red-500' };
        runsList.innerHTML = runs.slice(0, 20).map(run => `
            <div class="border border-gray-200 rounded-lg p-2.5 hover:bg-gray-50 cursor-pointer transition-colors"
                 onclick="loadRunDetail('${run.run_id}')">
                <div class="flex items-center justify-between mb-1">
                    <span class="text-xs font-medium ${statusColor[run.status] || 'text-gray-600'}">
                        ${statusIcon[run.status] || '?'} ${run.status}
                    </span>
                    <span class="font-mono text-gray-400" style="font-size:10px">${run.run_id.slice(0, 8)}</span>
                </div>
                <div class="text-gray-500 truncate" style="font-size:11px">${escHtml(run.goal || '')}</div>
            </div>`).join('');
    } catch (_) {}
}

async function loadRunDetail(runId) {
    try {
        const resp = await fetch(`/api/v1/workflows/runs/${runId}`);
        if (!resp.ok) return;
        const run = await resp.json();

        // 恢复节点状态
        Object.entries(run.node_runs || {}).forEach(([nodeId, nr]) => {
            setNodeStatus(nodeId, nr.status);
        });

        // 切换到执行日志
        document.querySelector('.sidebar-tab[data-tab="run"]').click();
        runLog.innerHTML = '';
        logLine('start', `📋 运行记录: ${runId}`);
        logLine('start', `🎯 目标: ${run.goal}`);
        logLine(run.status === 'success' ? 'success' : 'failed', `状态: ${run.status}`);
        Object.entries(run.node_runs || {}).forEach(([nodeId, nr]) => {
            const icon = nr.status === 'success' ? '✅' : nr.status === 'failed' ? '❌' : '⏸';
            logLine(nr.status, `${icon} [${nodeId}] ${nr.status}: ${(nr.output || '').slice(0, 60)}`);
        });
    } catch (_) {}
}

// ─────────────────────────────────────────────
//  工具函数
// ─────────────────────────────────────────────
function clearCanvas() {
    state.nodes = [];
    state.edges = [];
    state.selectedNodeId = null;
    state.selectedEdgeId = null;
    // 移除所有节点 DOM
    Array.from(canvasInner.querySelectorAll('.wf-node')).forEach(el => el.remove());
    // 清除边
    Array.from(edgesSvg.querySelectorAll('.wf-edge, .edge-label')).forEach(el => el.remove());
    updateCounts();
    showWfProps();
}

function updateCounts() {
    nodeCountEl.textContent = `节点: ${state.nodes.length}`;
    edgeCountEl.textContent = `连线: ${state.edges.length}`;
    btnSaveWf.disabled = state.nodes.length === 0;
}

function markDirty() {
    // 有未保存改动时提示
    if (!document.title.startsWith('*')) {
        document.title = '* ' + document.title.replace(/^\* /, '');
    }
}

function hideHint() {
    canvasHint.style.opacity = '0';
    setTimeout(() => canvasHint.style.display = 'none', 300);
}

function setStatus(text, color) {
    const colors = { green: 'bg-green-500', amber: 'bg-amber-500 animate-pulse', red: 'bg-red-500', gray: 'bg-gray-300' };
    const dot  = canvasStatus.querySelector('span:first-child');
    const span = canvasStatus.querySelector('span:last-child');
    dot.className = `w-1.5 h-1.5 rounded-full ${colors[color] || colors.gray}`;
    span.textContent = text;
}

function logLine(type, msg) {
    const div = document.createElement('div');
    div.className = `log-${type}`;
    div.textContent = msg;
    runLog.appendChild(div);
    runLog.scrollTop = runLog.scrollHeight;
}

function escHtml(str) {
    if (typeof str !== 'string') str = String(str || '');
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
}
