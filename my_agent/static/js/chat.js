/**
 * MyAgent 前端对话交互 — 支持流式（SSE）与非流式两种模式切换
 *
 * 面试考点:
 *   - EventSource API 接收 SSE
 *   - fetch + ReadableStream 处理自定义 SSE 事件
 *   - 结构化事件: thinking / content / tool_call / tool_result / done / error
 */

const messagesEl = document.getElementById('messages');
const chatForm = document.getElementById('chat-form');
const userInput = document.getElementById('user-input');
const sendBtn = document.getElementById('send-btn');
const statusEl = document.getElementById('status-indicator');
const tokenUsageEl = document.getElementById('token-usage');
const streamToggle = document.getElementById('stream-toggle');
const streamModeText = document.getElementById('stream-mode-text');

let isStreaming = false;

// 切换模式时更新文字提示
streamToggle.addEventListener('change', () => {
    streamModeText.textContent = streamToggle.checked ? '流式输出' : '非流式输出';
});

chatForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const message = userInput.value.trim();
    if (!message || isStreaming) return;

    // 清除欢迎消息
    const welcome = messagesEl.querySelector('.text-center.py-16');
    if (welcome) welcome.remove();

    appendUserMessage(message);
    userInput.value = '';
    userInput.style.height = 'auto';
    setStreaming(true);

    if (streamToggle.checked) {
        await streamChat(message);
    } else {
        await normalChat(message);
    }
});

// Shift+Enter 换行，Enter 发送
userInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        chatForm.dispatchEvent(new Event('submit'));
    }
});

// ===== 流式响应 =====

async function streamChat(message) {
    const assistantEl = appendAssistantMessage();
    const bubbleEl = assistantEl.querySelector('.msg-bubble');
    const thinkingEl = appendThinking();

    try {
        const resp = await fetch('/api/v1/chat/completions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message, stream: true }),
        });

        if (!resp.ok) {
            throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let content = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (line.startsWith('event: ')) continue;
                if (line.startsWith('data: ')) {
                    const dataStr = line.slice(6);
                    try {
                        const data = JSON.parse(dataStr);
                        if (data.delta !== undefined) {
                            // content 事件：逐字追加
                            if (thinkingEl.parentNode) thinkingEl.remove();
                            content += data.delta;
                            bubbleEl.innerHTML = formatMarkdown(content);
                            scrollToBottom();
                        } else if (data.message && data.message.includes('思考')) {
                            // thinking 事件：保持显示
                        } else if (data.content !== undefined && data.session_id) {
                            // done 事件
                            if (thinkingEl.parentNode) thinkingEl.remove();
                            if (data.usage) showTokenUsage(data.usage);
                        } else if (data.error) {
                            // error 事件
                            if (thinkingEl.parentNode) thinkingEl.remove();
                            bubbleEl.innerHTML = `<span class="text-red-500">错误: ${escapeHtml(data.error)}</span>`;
                        }
                    } catch (_) {
                        // 忽略无法解析的行
                    }
                }
            }
        }

        if (!content) {
            if (thinkingEl.parentNode) thinkingEl.remove();
            bubbleEl.innerHTML = '<span class="text-gray-400">（无响应内容）</span>';
        }

    } catch (err) {
        if (thinkingEl.parentNode) thinkingEl.remove();
        bubbleEl.innerHTML = `<span class="text-red-500">请求失败: ${escapeHtml(err.message)}</span>`;
    } finally {
        setStreaming(false);
        scrollToBottom();
    }
}

// ===== 非流式响应 =====

async function normalChat(message) {
    const assistantEl = appendAssistantMessage();
    const bubbleEl = assistantEl.querySelector('.msg-bubble');
    const thinkingEl = appendThinking();

    try {
        const resp = await fetch('/api/v1/chat/completions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message, stream: false }),
        });

        if (!resp.ok) {
            const errText = await resp.text();
            throw new Error(`HTTP ${resp.status}: ${errText || resp.statusText}`);
        }

        const data = await resp.json();

        if (thinkingEl.parentNode) thinkingEl.remove();
        bubbleEl.innerHTML = formatMarkdown(data.content || '（无响应内容）');
        if (data.usage) showTokenUsage(data.usage);

    } catch (err) {
        if (thinkingEl.parentNode) thinkingEl.remove();
        bubbleEl.innerHTML = `<span class="text-red-500">请求失败: ${escapeHtml(err.message)}</span>`;
    } finally {
        setStreaming(false);
        scrollToBottom();
    }
}

// ===== DOM Helpers =====

function appendUserMessage(text) {
    const div = document.createElement('div');
    div.className = 'msg-user';
    div.innerHTML = `<div class="msg-bubble">${escapeHtml(text)}</div>`;
    messagesEl.appendChild(div);
    scrollToBottom();
}

function appendAssistantMessage() {
    const div = document.createElement('div');
    div.className = 'msg-assistant';
    div.innerHTML = `
        <div class="flex items-start gap-3 max-w-[80%]">
            <div class="w-7 h-7 bg-primary-100 rounded-lg flex items-center justify-center flex-shrink-0 mt-1">
                <span class="text-primary-600 text-xs font-bold">A</span>
            </div>
            <div class="msg-bubble"></div>
        </div>`;
    messagesEl.appendChild(div);
    return div;
}

function appendThinking() {
    const div = document.createElement('div');
    div.className = 'thinking-indicator';
    div.innerHTML = `
        <div class="dot-pulse"><span></span><span></span><span></span></div>
        <span>正在思考...</span>`;
    messagesEl.appendChild(div);
    scrollToBottom();
    return div;
}

function setStreaming(val) {
    isStreaming = val;
    sendBtn.disabled = val;
    streamToggle.disabled = val;
    const statusDot = statusEl.querySelector('span:first-child');
    const statusText = statusEl.querySelector('span:last-child');
    if (val) {
        statusDot.className = 'w-2 h-2 bg-amber-500 rounded-full animate-pulse';
        statusText.textContent = streamToggle.checked ? '流式生成中...' : '请求中...';
    } else {
        statusDot.className = 'w-2 h-2 bg-green-500 rounded-full';
        statusText.textContent = '就绪';
    }
}

function showTokenUsage(usage) {
    if (!usage || !usage.total_tokens) return;
    tokenUsageEl.textContent = `Token: ${usage.prompt_tokens || 0} + ${usage.completion_tokens || 0} = ${usage.total_tokens}`;
}

function scrollToBottom() {
    requestAnimationFrame(() => {
        messagesEl.scrollTop = messagesEl.scrollHeight;
    });
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatMarkdown(text) {
    return escapeHtml(text)
        .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre class="bg-gray-100 rounded-lg p-3 my-2 overflow-x-auto text-xs"><code>$2</code></pre>')
        .replace(/`([^`]+)`/g, '<code class="bg-gray-100 px-1.5 py-0.5 rounded text-xs text-pink-600">$1</code>')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\n/g, '<br>');
}
