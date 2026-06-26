/**
 * 专业文献助手 - 前端交互逻辑 v2
 * 支持: Markdown / 来源引用 / 文件上传
 */

document.addEventListener('DOMContentLoaded', function () {
    // ==================== DOM ====================
    const chatMessages = document.getElementById('chat-messages');
    const questionInput = document.getElementById('question-input');
    const sendBtn = document.getElementById('send-btn');
    const initBtn = document.getElementById('init-btn');
    const fileUpload = document.getElementById('file-upload');
    const statusIndicator = document.getElementById('status-indicator');
    const statusText = document.getElementById('status-text');
    const fileCountEl = document.getElementById('file-count');

    let isInitialized = false;

    // ==================== 欢迎消息 ====================
    addMessage('bot',
        '你好！我是 **专业文献助手** 📚\n\n' +
        '**使用方法：**\n' +
        '1️⃣ 将 PDF/TXT 论文放入项目根目录的 <code>papers</code> 文件夹\n' +
        '2️⃣ 点击「📚 初始化知识库」按钮构建索引\n' +
        '3️⃣ 也可以直接「📎 上传论文」立即提问\n\n' +
        '现在就开始吧！'
    );

    checkStatus();

    // ==================== 事件 ====================
    initBtn.addEventListener('click', initializeKnowledgeBase);
    sendBtn.addEventListener('click', sendMessage);
    fileUpload.addEventListener('change', handleFileUpload);

    questionInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // ==================== 状态 ====================

    function checkStatus() {
        fetch('/status')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                isInitialized = data.initialized;
                updateFileCount(data.pdf_count + data.txt_count);
                updateUIForStatus();
                if (data.initialized) {
                    addMessage('bot', '✅ 知识库已就绪，可以开始提问！');
                }
            })
            .catch(function (err) { console.error('Status:', err); });
    }

    function updateFileCount(count) {
        fileCountEl.textContent = '论文：' + count;
    }

    function updateUIForStatus() {
        if (isInitialized) {
            statusIndicator.className = 'status online';
            statusText.textContent = '知识库已就绪';
            questionInput.disabled = false;
            sendBtn.disabled = false;
            questionInput.focus();
        } else {
            statusIndicator.className = 'status offline';
            statusText.textContent = '知识库未初始化';
            questionInput.disabled = true;
            sendBtn.disabled = true;
        }
        initBtn.textContent = '📚 初始化知识库';
    }

    // ==================== 初始化知识库 ====================

    var initProgressTimer = null;

    function showInitProgress(phase, message, current, total) {
        var bar = document.getElementById('init-progress-bar');
        var fill = document.getElementById('init-progress-fill');
        var text = document.getElementById('init-progress-text');
        if (!bar) return;
        bar.style.display = 'flex';
        if (phase === 'embed' && total > 0) {
            var pct = Math.min(Math.round((current / total) * 100), 100);
            fill.style.width = pct + '%';
            text.textContent = message || (pct + '%');
        } else {
            fill.style.width = '100%';
            text.textContent = message || '处理中...';
        }
    }

    function hideInitProgress() {
        var bar = document.getElementById('init-progress-bar');
        if (bar) bar.style.display = 'none';
    }

    function pollInitProgress() {
        fetch('/init_progress')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data.running) {
                    if (initProgressTimer) {
                        clearInterval(initProgressTimer);
                        initProgressTimer = null;
                    }
                    return;
                }
                showInitProgress(data.phase, data.message, data.current, data.total);
            })
            .catch(function () {});
    }

    function initializeKnowledgeBase() {
        initBtn.disabled = true;
        initBtn.textContent = '⏳ 正在初始化...';
        statusIndicator.className = 'status loading';
        statusText.textContent = '正在构建向量库...';

        addMessage('bot', '🔄 正在初始化知识库（加载论文 → 分块 → 向量化）...');
        showInitProgress('start', '准备中...', 0, 0);

        // 每 1 秒轮询一次进度
        if (initProgressTimer) clearInterval(initProgressTimer);
        initProgressTimer = setInterval(pollInitProgress, 1000);

        fetch('/init', { method: 'POST' })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (initProgressTimer) {
                    clearInterval(initProgressTimer);
                    initProgressTimer = null;
                }
                hideInitProgress();
                if (data.success) {
                    isInitialized = true;
                    updateUIForStatus();
                    fetch('/status').then(function (r) { return r.json(); }).then(function (s) { updateFileCount(s.pdf_count + s.txt_count); }).catch(function () {});
                    addMessage('bot', '✅ ' + data.message);
                    if (data.details && data.details.length) {
                        var detailMsg = data.details.map(function (d) { return d.trim(); }).filter(Boolean).join('\n');
                        if (detailMsg) addMessage('bot', '📋 加载详情：\n' + detailMsg);
                    }
                } else {
                    addMessage('error', '❌ ' + data.message);
                    statusIndicator.className = 'status offline';
                    statusText.textContent = '初始化失败';
                }
            })
            .catch(function (err) {
                if (initProgressTimer) {
                    clearInterval(initProgressTimer);
                    initProgressTimer = null;
                }
                hideInitProgress();
                addMessage('error', '❌ 请求失败：' + err.message);
                statusIndicator.className = 'status offline';
                statusText.textContent = '网络错误';
            })
            .finally(function () { initBtn.disabled = false; });
    }

    // ==================== 文件上传 ====================

    function handleFileUpload(e) {
        var file = e.target.files[0];
        if (!file) return;

        var validTypes = ['.pdf', '.txt'];
        var ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();
        if (validTypes.indexOf(ext) === -1) {
            addMessage('error', '❌ 不支持的文件格式，请上传 PDF 或 TXT 文件');
            return;
        }

        addMessage('bot', '📎 正在上传并索引: **' + file.name + '** ...');

        var formData = new FormData();
        formData.append('file', file);

        fetch('/upload', { method: 'POST', body: formData })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.success) {
                    isInitialized = true;
                    updateUIForStatus();
                    addMessage('bot', '✅ ' + data.message + '\n现在可以提问了！');
                } else {
                    addMessage('error', '❌ ' + data.message);
                }
            })
            .catch(function (err) {
                addMessage('error', '❌ 上传失败：' + err.message);
            });

        // 重置 input 以便重复上传同一文件
        e.target.value = '';
    }

    // ==================== 发送消息 ====================

    function sendMessage() {
        var question = questionInput.value.trim();
        if (!question) return;

        addMessage('user', question);
        questionInput.value = '';
        setInputState(true);

        var typingId = addTypingIndicator();

        fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question: question }),
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                removeTypingIndicator(typingId);

                if (data.error) {
                    addMessage('error', '❌ ' + data.error);
                } else {
                    var html = renderMarkdown(data.reply);
                    // 添加来源引用
                    if (data.sources && data.sources.length > 0) {
                        html += renderSources(data.sources);
                    }
                    addMessage('bot', html, true);  // isRaw = true
                }
            })
            .catch(function (err) {
                removeTypingIndicator(typingId);
                addMessage('error', '❌ 网络请求失败：' + err.message);
            })
            .finally(function () { setInputState(false); });
    }

    // ==================== Markdown ====================

    function renderMarkdown(text) {
        if (typeof marked !== 'undefined') {
            return marked.parse(text, { breaks: true });
        }
        // fallback: 简单转义
        return text.replace(/\n/g, '<br>');
    }

    // ==================== 来源引用 ====================

    function renderSources(sources) {
        var html = '<div class="sources-collapse">' +
            '<button class="sources-toggle" onclick="this.nextElementSibling.style.display=\'block\';this.style.display=\'none\'">' +
            '📎 查看 ' + sources.length + ' 个引用来源</button>' +
            '<div class="sources-list">';
        sources.forEach(function (src, i) {
            html += '<div class="source-item">' +
                '<span class="source-file">📄 ' + escapeHtml(src.file) + '</span>' +
                '<span class="source-snippet">' + escapeHtml(src.snippet) + '</span>' +
                '</div>';
        });
        html += '</div></div>';
        return html;
    }

    // ==================== UI 辅助 ====================

    function setInputState(loading) {
        if (loading) {
            sendBtn.disabled = true;
            sendBtn.textContent = '思考中...';
            questionInput.disabled = true;
        } else {
            sendBtn.disabled = false;
            sendBtn.textContent = '发送';
            questionInput.disabled = false;
            questionInput.focus();
        }
    }

    function addMessage(type, content, isRaw) {
        var div = document.createElement('div');
        div.className = 'message ' + type;
        if (isRaw) {
            div.innerHTML = '<div class="message-content">' + content + '</div>';
        } else {
            div.innerHTML = '<div class="message-content">' + escapeHtml(content).replace(/\n/g, '<br>') + '</div>';
        }
        chatMessages.appendChild(div);
        scrollToBottom();
        return div;
    }

    function addTypingIndicator() {
        var id = 'typing-' + Date.now();
        var div = document.createElement('div');
        div.className = 'message bot';
        div.id = id;
        div.innerHTML = '<div class="message-content"><span class="typing-dots">思考中<span>.</span><span>.</span><span>.</span></span></div>';
        chatMessages.appendChild(div);
        scrollToBottom();
        return id;
    }

    function removeTypingIndicator(id) {
        var el = document.getElementById(id);
        if (el) el.remove();
    }

    function scrollToBottom() {
        setTimeout(function () { chatMessages.scrollTop = chatMessages.scrollHeight; }, 50);
    }

    function escapeHtml(text) {
        var div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
});
