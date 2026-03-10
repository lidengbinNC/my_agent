/**
 * MyAgent 前端对话交互 — SSE 接收 ReAct 思考过程，折叠式卡片展示
 *
 * 面试考点:
 *   - fetch + ReadableStream 处理多事件类型 SSE
 *   - 结构化事件解析: thinking / tool_call / tool_result / content / done / error
 *   - 折叠式 Thought/Action/Observation 卡片 UI
 */

const messagesEl = document.getElementById('messages');
const chatForm   = document.getElementById('chat-form');
const userInput  = document.getElementById('user-input');
const sendBtn    = document.getElementById('send-btn');
const statusEl   = document.getElementById('status-indicator');
const tokenUsageEl = document.getElementById('token-usage');
const streamToggle = document.getElementById('stream-toggle');
const streamModeText = document.getElementById('stream-mode-text');
const newSessionBtn = document.getElementById('new-session-btn');
const deepThinkBtn  = document.getElementById('deep-think-btn');
const deepThinkText = document.getElementById('deep-think-text');

let isStreaming = false;
let streamMode = true;     // 默认开启流式输出
let deepThinkMode = false; // 默认关闭深度思考
let currentSessionId = null;
let defaultPlanAgentId = null; // 服务端预创建的 Plan-and-Execute Agent ID

// 启动时拉取默认 Plan Agent ID
(async () => {
    try {
        const resp = await fetch('/api/v1/agents/default');
        if (resp.ok) {
            const data = await resp.json();
            defaultPlanAgentId = data.id;
        }
    } catch (_) {
        // 网络异常时深度思考按钮将保持禁用
    }
    // 拉取完成后才激活深度思考按钮
    if (!defaultPlanAgentId) {
        deepThinkBtn.disabled = true;
        deepThinkBtn.title = '深度思考 Agent 初始化失败';
        deepThinkBtn.classList.add('opacity-40', 'cursor-not-allowed');
    }
})();

// 深度思考切换
deepThinkBtn.addEventListener('click', () => {
    if (deepThinkBtn.disabled) return;
    deepThinkMode = !deepThinkMode;
    if (deepThinkMode) {
        deepThinkBtn.classList.remove('border-gray-200', 'text-gray-500', 'hover:border-violet-300', 'hover:text-violet-600', 'hover:bg-violet-50');
        deepThinkBtn.classList.add('border-violet-500', 'text-violet-700', 'bg-violet-50');
        deepThinkText.textContent = '深度思考 开';
    } else {
        deepThinkBtn.classList.remove('border-violet-500', 'text-violet-700', 'bg-violet-50');
        deepThinkBtn.classList.add('border-gray-200', 'text-gray-500', 'hover:border-violet-300', 'hover:text-violet-600', 'hover:bg-violet-50');
        deepThinkText.textContent = '深度思考';
    }
});

// 流式开关切换
streamToggle.addEventListener('change', (e) => {
    streamMode = e.target.checked;
    streamModeText.textContent = streamMode ? '流式输出' : '非流式输出';
});

// 新建会话按钮
newSessionBtn.addEventListener('click', () => {
    if (isStreaming) return;

    currentSessionId = null;

    // 重置深度思考按钮状态
    if (deepThinkMode) {
        deepThinkMode = false;
        deepThinkBtn.classList.remove('border-violet-500', 'text-violet-700', 'bg-violet-50');
        deepThinkBtn.classList.add('border-gray-200', 'text-gray-500', 'hover:border-violet-300', 'hover:text-violet-600', 'hover:bg-violet-50');
        deepThinkText.textContent = '深度思考';
    }
    
    // 清空消息列表
    messagesEl.innerHTML = `
        <div class="text-center py-16">
            <div class="w-16 h-16 bg-primary-100 rounded-2xl flex items-center justify-center mx-auto mb-4">
                <span class="text-primary-600 text-2xl font-bold">A</span>
            </div>
            <h2 class="text-xl font-semibold text-gray-700 mb-2">欢迎使用 MyAgent</h2>
            <p class="text-gray-500 text-sm max-w-md mx-auto">
                智能多 Agent 任务执行平台，支持 ReAct 推理、工具调用、多模型路由。
            </p>
        </div>`;
    
    // 清空 token 统计
    tokenUsageEl.textContent = '';
    
    // 聚焦输入框
    userInput.focus();
});

chatForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const message = userInput.value.trim();
    if (!message || isStreaming) return;

    const welcome = messagesEl.querySelector('.text-center.py-16');
    if (welcome) welcome.remove();

    appendUserMessage(message);
    userInput.value = '';
    setStreaming(true);

    if (deepThinkMode && defaultPlanAgentId) {
        // 深度思考：走 Plan-and-Execute 引擎
        await streamDeepThink(message);
    } else if (streamMode) {
        await streamChat(message);
    } else {
        await nonStreamChat(message);
    }
});

userInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        chatForm.dispatchEvent(new Event('submit'));
    }
});

// ===== 核心：非流式处理 =====

async function nonStreamChat(message) {
    const msgWrapper = appendAssistantWrapper();
    const answerBubble = msgWrapper.querySelector('.answer-bubble');

    try {
        const resp = await fetch('/api/v1/chat/completions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                message, 
                stream: false,
                session_id: currentSessionId  // 携带会话 ID
            }),
        });

        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

        const data = await resp.json();
        
        // 保存会话 ID
        if (data.session_id) {
            currentSessionId = data.session_id;
        }
        
        // 显示最终答案
        answerBubble.innerHTML = formatMarkdown(data.content || '无响应');
        answerBubble.classList.remove('hidden');
        
        // 显示 Token 使用情况
        if (data.usage) showTokenUsage(data.usage);
        
    } catch (err) {
        answerBubble.innerHTML = `<span class="text-red-600">❌ 错误: ${escapeHtml(err.message)}</span>`;
        answerBubble.classList.remove('hidden');
    } finally {
        setStreaming(false);
        scrollToBottom();
    }
}

// ===== 核心：SSE 流处理 =====

async function streamChat(message) {
    const msgWrapper = appendAssistantWrapper();
    const stepsContainer = msgWrapper.querySelector('.steps-container');
    const answerBubble   = msgWrapper.querySelector('.answer-bubble');

    let currentEvent = '';

    try {
        const resp = await fetch('/api/v1/chat/completions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                message, 
                stream: true,
                session_id: currentSessionId  // 携带会话 ID
            }),
        });

        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

        const reader  = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let answerContent = '';

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
                    try { data = JSON.parse(line.slice(6)); }
                    catch { continue; }

                    switch (currentEvent) {
                        case 'thinking':
                            handleThinking(stepsContainer, data);
                            break;
                        case 'tool_call':
                            handleToolCall(stepsContainer, data);
                            break;
                        case 'tool_result':
                            handleToolResult(stepsContainer, data);
                            break;
                        case 'content':
                            answerContent += data.delta || '';
                            answerBubble.innerHTML = formatMarkdown(answerContent);
                            answerBubble.classList.remove('hidden');
                            scrollToBottom();
                            break;
                        case 'done':
                            // 保存会话 ID
                            if (data.session_id) {
                                currentSessionId = data.session_id;
                            }
                            if (data.usage) showTokenUsage(data.usage);
                            break;
                        case 'error':
                            appendErrorCard(stepsContainer, data.error || '未知错误');
                            break;
                    }
                }
            }
        }
    } catch (err) {
        appendErrorCard(stepsContainer, err.message);
    } finally {
        setStreaming(false);
        scrollToBottom();
    }
}

// ===== 核心：深度思考（Plan-and-Execute）流处理 =====

async function streamDeepThink(message) {
    const msgWrapper = appendAssistantWrapper();
    const stepsContainer = msgWrapper.querySelector('.steps-container');
    const answerBubble   = msgWrapper.querySelector('.answer-bubble');

    // 标记为深度思考模式
    const badge = document.createElement('div');
    badge.className = 'flex items-center gap-1.5 text-xs text-violet-600 bg-violet-50 border border-violet-200 rounded-lg px-3 py-1.5 mb-2';
    badge.innerHTML = `
        <svg class="w-3.5 h-3.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                  d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.346.346a1 1 0 01-.707.293H9.38a1 1 0 01-.707-.293l-.346-.346z"/>
        </svg>
        <span>深度思考模式 — 正在规划执行计划...</span>`;
    stepsContainer.appendChild(badge);

    let currentEvent = '';

    try {
        const resp = await fetch(`/api/v1/agents/${defaultPlanAgentId}/run`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ goal: message, stream: true }),
        });

        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

        const reader  = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let answerContent = '';

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

                    switch (currentEvent) {
                        case 'thinking':
                            handleDeepThinkThinking(stepsContainer, badge, data);
                            break;
                        case 'tool_result':
                            handleDeepThinkStepDone(stepsContainer, data);
                            break;
                        case 'content':
                            answerContent += data.delta || '';
                            answerBubble.innerHTML = formatMarkdown(answerContent);
                            answerBubble.classList.remove('hidden');
                            scrollToBottom();
                            break;
                        case 'done':
                            break;
                        case 'error':
                            appendErrorCard(stepsContainer, data.error || '未知错误');
                            break;
                    }
                }
            }
        }
    } catch (err) {
        appendErrorCard(stepsContainer, err.message);
    } finally {
        setStreaming(false);
        scrollToBottom();
    }
}

// ===== 深度思考专属事件处理器 =====

function handleDeepThinkThinking(container, badge, data) {
    const msg = data.message || '';

    // 计划已生成 — 渲染步骤列表
    if (data.plan && Array.isArray(data.plan)) {
        badge.querySelector('span').textContent = `深度思考模式 — 计划已生成，共 ${data.plan.length} 步`;
        const planCard = document.createElement('div');
        planCard.className = 'border border-violet-200 rounded-xl overflow-hidden mb-2 shadow-sm';
        planCard.innerHTML = `
            <button class="step-header w-full flex items-center gap-3 px-4 py-2.5 bg-violet-50 hover:bg-violet-100 transition-colors text-left"
                    onclick="toggleStep(this)">
                <span class="text-violet-500 text-sm">📋</span>
                <div class="flex-1 text-xs font-semibold text-violet-700">执行计划</div>
                <span class="chevron text-gray-400 text-xs transition-transform">▼</span>
            </button>
            <div class="step-body px-4 py-3 bg-white border-t border-violet-100">
                <ol class="space-y-1.5 text-xs text-gray-700 list-decimal list-inside">
                    ${data.plan.map(s => `<li><span class="text-gray-500">Step ${s.step_id}:</span> ${escapeHtml(s.description)}</li>`).join('')}
                </ol>
            </div>`;
        container.appendChild(planCard);
        scrollToBottom();
        return;
    }

    // 步骤开始
    if (data.step_id != null) {
        let indicator = container.querySelector('.thinking-live');
        if (!indicator) {
            indicator = document.createElement('div');
            indicator.className = 'thinking-live flex items-center gap-2 text-xs text-violet-600 bg-violet-50 border border-violet-200 rounded-lg px-3 py-2 mb-2';
            indicator.innerHTML = `
                <div class="flex gap-1">
                    <span class="w-1.5 h-1.5 bg-violet-500 rounded-full animate-bounce" style="animation-delay:0s"></span>
                    <span class="w-1.5 h-1.5 bg-violet-500 rounded-full animate-bounce" style="animation-delay:.15s"></span>
                    <span class="w-1.5 h-1.5 bg-violet-500 rounded-full animate-bounce" style="animation-delay:.3s"></span>
                </div>
                <span class="thinking-text"></span>`;
            container.appendChild(indicator);
        }
        indicator.querySelector('.thinking-text').textContent = msg || `Step ${data.step_id} 执行中...`;
        scrollToBottom();
        return;
    }

    // 合成中或其他通用消息
    if (msg) {
        let indicator = container.querySelector('.thinking-live');
        if (!indicator) {
            indicator = document.createElement('div');
            indicator.className = 'thinking-live flex items-center gap-2 text-xs text-violet-600 bg-violet-50 border border-violet-200 rounded-lg px-3 py-2 mb-2';
            indicator.innerHTML = `
                <div class="flex gap-1">
                    <span class="w-1.5 h-1.5 bg-violet-500 rounded-full animate-bounce" style="animation-delay:0s"></span>
                    <span class="w-1.5 h-1.5 bg-violet-500 rounded-full animate-bounce" style="animation-delay:.15s"></span>
                    <span class="w-1.5 h-1.5 bg-violet-500 rounded-full animate-bounce" style="animation-delay:.3s"></span>
                </div>
                <span class="thinking-text"></span>`;
            container.appendChild(indicator);
        }
        indicator.querySelector('.thinking-text').textContent = msg;
        scrollToBottom();
    }
}

function handleDeepThinkStepDone(container, data) {
    container.querySelector('.thinking-live')?.remove();

    const card = document.createElement('div');
    const failed = data.error != null;
    card.className = 'step-card border rounded-xl overflow-hidden mb-2 shadow-sm ' +
        (failed ? 'border-red-200' : 'border-green-200');
    card.innerHTML = `
        <button class="step-header w-full flex items-center gap-3 px-4 py-2.5 transition-colors text-left ${failed ? 'bg-red-50 hover:bg-red-100' : 'bg-green-50 hover:bg-green-100'}"
                onclick="toggleStep(this)">
            <span class="text-sm">${failed ? '❌' : '✅'}</span>
            <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2">
                    <span class="text-xs font-semibold ${failed ? 'text-red-700' : 'text-green-700'}">Step ${data.step_id ?? ''}</span>
                    <span class="text-xs text-gray-500 truncate">${escapeHtml(data.desc || '')}</span>
                </div>
            </div>
            <span class="chevron text-gray-400 text-xs transition-transform">▼</span>
        </button>
        <div class="step-body hidden px-4 py-3 bg-white border-t ${failed ? 'border-red-100' : 'border-green-100'} text-xs">
            <div class="font-semibold text-gray-500 mb-1">${failed ? '❌ 错误' : '📤 执行结果'}</div>
            <pre class="rounded-lg p-2 overflow-x-auto font-mono whitespace-pre-wrap ${failed ? 'bg-red-50 text-red-800' : 'bg-green-50 text-green-800'}">${escapeHtml(data.result || data.error || '')}</pre>
        </div>`;
    container.appendChild(card);
    scrollToBottom();
}

// ===== 事件处理器 =====

function handleThinking(container, data) {
    // 更新或创建 thinking 指示器
    let indicator = container.querySelector('.thinking-live');
    if (!indicator) {
        indicator = document.createElement('div');
        indicator.className = 'thinking-live flex items-center gap-2 text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mb-2';
        indicator.innerHTML = `
            <div class="flex gap-1">
                <span class="w-1.5 h-1.5 bg-amber-500 rounded-full animate-bounce" style="animation-delay:0s"></span>
                <span class="w-1.5 h-1.5 bg-amber-500 rounded-full animate-bounce" style="animation-delay:.15s"></span>
                <span class="w-1.5 h-1.5 bg-amber-500 rounded-full animate-bounce" style="animation-delay:.3s"></span>
            </div>
            <span class="thinking-text"></span>`;
        container.appendChild(indicator);
    }
    indicator.querySelector('.thinking-text').textContent =
        data.message || `第 ${data.iteration} 步：思考中...`;
    scrollToBottom();
}

function handleToolCall(container, data) {
    // 移除 thinking 指示器
    container.querySelector('.thinking-live')?.remove();

    const card = document.createElement('div');
    card.className = 'step-card border border-gray-200 rounded-xl overflow-hidden mb-2 shadow-sm';
    card.innerHTML = `
        <button class="step-header w-full flex items-center gap-3 px-4 py-2.5 bg-blue-50 hover:bg-blue-100 transition-colors text-left"
                onclick="toggleStep(this)">
            <span class="text-blue-500 text-sm">⚡</span>
            <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2">
                    <span class="text-xs font-semibold text-blue-700">工具调用</span>
                    <code class="text-xs bg-blue-100 text-blue-800 px-1.5 py-0.5 rounded font-mono">${escapeHtml(data.tool)}</code>
                    <span class="text-xs text-gray-400">第 ${data.iteration} 步</span>
                </div>
                ${data.thought ? `<div class="text-xs text-gray-500 mt-0.5 truncate">${escapeHtml(data.thought)}</div>` : ''}
            </div>
            <span class="chevron text-gray-400 text-xs transition-transform">▼</span>
        </button>
        <div class="step-body hidden px-4 py-3 bg-white border-t border-gray-100 text-xs space-y-2">
            ${data.thought ? `
            <div>
                <div class="font-semibold text-gray-500 mb-1">💭 思考</div>
                <div class="text-gray-700 leading-relaxed">${escapeHtml(data.thought)}</div>
            </div>` : ''}
            <div>
                <div class="font-semibold text-gray-500 mb-1">📥 输入参数</div>
                <pre class="bg-gray-50 rounded-lg p-2 overflow-x-auto text-gray-700 font-mono">${escapeHtml(JSON.stringify(data.args, null, 2))}</pre>
            </div>
        </div>`;
    container.appendChild(card);
    scrollToBottom();
}

function handleToolResult(container, data) {
    // 找到对应工具调用卡片，追加结果
    const cards = container.querySelectorAll('.step-card');
    const lastCard = cards[cards.length - 1];

    if (lastCard) {
        const body = lastCard.querySelector('.step-body');
        const resultDiv = document.createElement('div');
        resultDiv.innerHTML = `
            <div class="font-semibold text-gray-500 mb-1">📤 执行结果</div>
            <pre class="bg-green-50 border border-green-200 rounded-lg p-2 overflow-x-auto text-green-800 font-mono whitespace-pre-wrap">${escapeHtml(data.result)}</pre>`;
        body.appendChild(resultDiv);

        // 更新 header 状态
        const header = lastCard.querySelector('.step-header');
        header.classList.remove('bg-blue-50', 'hover:bg-blue-100');
        header.classList.add('bg-green-50', 'hover:bg-green-100');
        header.querySelector('.text-blue-500').textContent = '✅';
    }
    scrollToBottom();
}

function appendErrorCard(container, errorMsg) {
    container.querySelector('.thinking-live')?.remove();
    const div = document.createElement('div');
    div.className = 'flex items-start gap-2 text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2 mb-2';
    div.innerHTML = `<span>❌</span><span>${escapeHtml(errorMsg)}</span>`;
    container.appendChild(div);
}

// ===== DOM 构建 =====

function appendUserMessage(text) {
    const div = document.createElement('div');
    div.className = 'flex justify-end';
    div.innerHTML = `
        <div class="bg-indigo-600 text-white rounded-2xl rounded-br-md px-4 py-3 max-w-[80%] text-sm leading-relaxed shadow-sm">
            ${escapeHtml(text)}
        </div>`;
    messagesEl.appendChild(div);
    scrollToBottom();
}

function appendAssistantWrapper() {
    const div = document.createElement('div');
    div.className = 'flex justify-start';
    div.innerHTML = `
        <div class="flex items-start gap-3 max-w-[90%] w-full">
            <div class="w-7 h-7 bg-indigo-100 rounded-lg flex items-center justify-center flex-shrink-0 mt-1">
                <span class="text-indigo-600 text-xs font-bold">A</span>
            </div>
            <div class="flex-1 min-w-0 space-y-2">
                <div class="steps-container"></div>
                <div class="answer-bubble hidden bg-white border border-gray-200 text-gray-800 rounded-2xl rounded-bl-md px-4 py-3 text-sm leading-relaxed shadow-sm"></div>
            </div>
        </div>`;
    messagesEl.appendChild(div);
    return div;
}

// ===== 折叠/展开步骤卡片 =====
function toggleStep(btn) {
    const body = btn.nextElementSibling;
    const chevron = btn.querySelector('.chevron');
    const isHidden = body.classList.contains('hidden');
    body.classList.toggle('hidden', !isHidden);
    chevron.style.transform = isHidden ? 'rotate(180deg)' : '';
}

// ===== 工具函数 =====

function setStreaming(val) {
    isStreaming = val;
    sendBtn.disabled = val;
    const dot  = statusEl.querySelector('span:first-child');
    const text = statusEl.querySelector('span:last-child');
    dot.className  = val ? 'w-2 h-2 bg-amber-500 rounded-full animate-pulse' : 'w-2 h-2 bg-green-500 rounded-full';
    text.textContent = val ? '推理中...' : '就绪';
}

function showTokenUsage(usage) {
    if (!usage?.total_tokens) return;
    tokenUsageEl.textContent = `Token: ${usage.prompt_tokens||0} + ${usage.completion_tokens||0} = ${usage.total_tokens}`;
}

function scrollToBottom() {
    requestAnimationFrame(() => { messagesEl.scrollTop = messagesEl.scrollHeight; });
}

function escapeHtml(text) {
    if (typeof text !== 'string') text = JSON.stringify(text);
    const d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
}

function formatMarkdown(text) {
    return escapeHtml(text)
        .replace(/```(\w*)\n([\s\S]*?)```/g,
            '<pre class="bg-gray-100 rounded-lg p-3 my-2 overflow-x-auto text-xs font-mono"><code>$2</code></pre>')
        .replace(/`([^`]+)`/g,
            '<code class="bg-gray-100 px-1.5 py-0.5 rounded text-xs text-pink-600 font-mono">$1</code>')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\n/g, '<br>');
}
