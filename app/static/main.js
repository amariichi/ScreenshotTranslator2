const dropzone = document.getElementById('dropzone');
const preview = document.getElementById('preview');
const statusEl = document.getElementById('status');
const markdownEl = document.getElementById('markdown');
const promptInput = document.getElementById('prompt');
const submitBtn = document.getElementById('submit');
const copyBtn = document.getElementById('copy');

let currentFile = null;
let statusTimer = null;

function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function renderMarkdown(md) {
  if (!md) return '';
  let html = escapeHtml(md);
  // code blocks ```
  html = html.replace(/```([\s\S]*?)```/g, (_, code) => `<pre><code>${code}</code></pre>`);
  // inline code
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
  // bold **text**
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  // italics *text*
  html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  // bullet lists
  html = html.replace(/(^|\n)[*-] (.+)/g, '<ul><li>$2</li></ul>');
  // line breaks
  html = html.replace(/\n\n+/g, '</p><p>');
  html = `<p>${html}</p>`;
  return html;
}

function setStatus(text) {
  if (typeof text === 'string' && text.includes('モデル読み込み中') && !text.includes('準備完了と表示されてから')) {
    text = `${text}\n準備完了と表示されてから画像を読み込ませてください`;
  }
  statusEl.textContent = text;
}

async function pollStatus(interval = 3000) {
  try {
    const res = await fetch('/api/llama-status');
    if (res.ok) {
      const data = await res.json();
      setStatus(data.status || '状態取得に失敗');
    }
  } catch (e) {
    setStatus('llama-server 起動中 (状態確認待ち)');
  } finally {
    if (!statusTimer) {
      statusTimer = setInterval(() => pollStatus(15000), 15000);
    }
  }
}

function setPreview(file) {
  const reader = new FileReader();
  reader.onload = e => {
    preview.src = e.target.result;
    preview.style.display = 'block';
  };
  reader.readAsDataURL(file);
}

async function send() {
  if (!currentFile) {
    setStatus('画像を貼り付けてください');
    return;
  }
  if (submitBtn.disabled) return;
  submitBtn.disabled = true;
  setStatus('送信中...');

  const fd = new FormData();
  fd.append('image', currentFile, currentFile.name || 'image.png');
  fd.append('prompt', promptInput.value.trim());

  try {
    const res = await fetch('/api/translate', { method: 'POST', body: fd });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || res.statusText);
    }
    const data = await res.json();
    const html = renderMarkdown(data.markdown || '');
    markdownEl.innerHTML = html;
    setStatus('完了');
  } catch (err) {
    console.error(err);
    setStatus(`エラー: ${err.message}`);
  } finally {
    submitBtn.disabled = false;
  }
}

function acceptFile(file) {
  currentFile = file;
  setPreview(file);
  setStatus(`${file.name || 'pasted image'} (${Math.round(file.size/1024)} KB)`);
  // 自動送信
  send();
}

// 初期ステータスチェック
pollStatus();

// Paste handler
window.addEventListener('paste', e => {
  const items = e.clipboardData?.items;
  if (!items) return;
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      const file = item.getAsFile();
      if (file) acceptFile(file);
      e.preventDefault();
      break;
    }
  }
});

// Drag-drop
['dragenter','dragover'].forEach(ev => dropzone.addEventListener(ev, e => {
  e.preventDefault();
  dropzone.classList.add('dragging');
}));
['dragleave','drop'].forEach(ev => dropzone.addEventListener(ev, e => {
  e.preventDefault();
  dropzone.classList.remove('dragging');
}));
dropzone.addEventListener('drop', e => {
  const file = e.dataTransfer?.files?.[0];
  if (file) acceptFile(file);
});

// Click to open file picker
const hiddenInput = document.getElementById('file-input');
dropzone.addEventListener('click', () => hiddenInput.click());
hiddenInput.addEventListener('change', e => {
  const file = e.target.files?.[0];
  if (file) acceptFile(file);
});

submitBtn.addEventListener('click', send);

copyBtn.addEventListener('click', async () => {
  const text = markdownEl.innerText;
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    setStatus('コピーしました');
  } catch {
    setStatus('コピーに失敗しました');
  }
});

// Defaults
promptInput.value = '英語などの自然文は必ず日本語で全文訳してください。要約・省略禁止。コードや数式は原文のままとしますが、その後に自然文がある場合も必ず日本語に翻訳してください。';
promptInput.title = '翻訳厳守: コード/数式は原文のまま。自然文は位置に関係なく必ず日本語訳。要約禁止';
