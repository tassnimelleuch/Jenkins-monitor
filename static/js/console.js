let _isRunning = true;
let _autoScroll = true;
let _pollHandle = null;
let _lastLineCount = 0;
let _consoleStream = null;
let _consoleStreamReceived = false;
let _consolePollingFallbackStarted = false;

// ── LINE CLASSIFIER
function cls(line) {
  const l = line.toLowerCase();
  if (/finished:\s*success/.test(l)) return 'l-finish';
  if (/finished:\s*(failure|aborted)/.test(l)) return 'l-error';
  if (/error|exception|failed|failure/.test(l)) return 'l-error';
  if (/warning|warn/.test(l)) return 'l-warn';
  if (/\[pipeline\]|\[stage\]/.test(l)) return 'l-stage';
  if (/started by|building in|checking out/.test(l)) return 'l-info';
  if (/success/.test(l)) return 'l-success';
  return '';
}

// ── RENDER / APPEND LOG LINES
function renderLines(text) {
  const lines = text.split('\n');
  const tbody = document.getElementById('logBody');
  const frag = document.createDocumentFragment();
  const start = _lastLineCount;

  for (let i = start; i < lines.length; i++) {
    const tr = document.createElement('tr');
    const tdN = document.createElement('td');
    const tdC = document.createElement('td');

    tdN.className = 'ln';
    tdN.textContent = i + 1;

    tdC.className = 'lc ' + cls(lines[i]);
    tdC.textContent = lines[i] || ' ';

    tr.appendChild(tdN);
    tr.appendChild(tdC);
    frag.appendChild(tr);
  }

  tbody.appendChild(frag);
  _lastLineCount = lines.length;

  const lc = document.getElementById('lineCount');
  if (lc) lc.textContent = lines.length + ' lines';

  detectFinish(text);
  if (_autoScroll) scrollToBottom();
}

// ── DETECT FINISH
function detectFinish(text) {
  if (/Finished:\s*(SUCCESS|FAILURE|ABORTED)/i.test(text)) {
    _isRunning = false;
    stopPolling();
    updateBadge(text);

    const li = document.getElementById('liveIndicator');
    if (li) li.style.display = 'none';
  }
}

// ── UPDATE BADGE
function updateBadge(text) {
  const badge = document.getElementById('resultBadge');
  if (!badge) return;

  if (/Finished:\s*SUCCESS/i.test(text)) {
    badge.textContent = '✓ SUCCESS';
    badge.className = 'tb-badge badge-success';
  } else if (/Finished:\s*FAILURE/i.test(text)) {
    badge.textContent = '✗ FAILURE';
    badge.className = 'tb-badge badge-failure';
  } else if (/Finished:\s*ABORTED/i.test(text)) {
    badge.textContent = '⊘ ABORTED';
    badge.className = 'tb-badge badge-aborted';
  } else {
    badge.textContent = '● RUNNING';
    badge.className = 'tb-badge badge-running';
  }
}

// ── FETCH LOG
async function fetchLog() {
  try {
    const res = await fetch('/api/log/' + BUILD_NUMBER);
    const data = await res.json();
    renderLines(data.log || '');
  } catch (e) {
    console.error('Log fetch error:', e);
  }
}

function startPolling() {
  _consolePollingFallbackStarted = true;
  fetchLog();
  _pollHandle = setInterval(fetchLog, 3000);
}

function stopPolling() {
  if (_pollHandle) {
    clearInterval(_pollHandle);
    _pollHandle = null;
  }
}

function getConsoleStreamUrl() {
  return document.body.dataset.consoleStreamUrl || '';
}

function closeConsoleStream() {
  if (_consoleStream) {
    _consoleStream.close();
    _consoleStream = null;
  }
}

function startConsoleStream() {
  if (typeof window.EventSource === 'undefined' || !getConsoleStreamUrl()) {
    updateBadge('Live stream unavailable');
    return;
  }

  _consoleStream = new EventSource(getConsoleStreamUrl());
  _consoleStream.addEventListener('open', () => {
    _consoleStreamReceived = true;
  });
  _consoleStream.addEventListener('stream_ready', () => {
    _consoleStreamReceived = true;
  });
  _consoleStream.addEventListener('log_snapshot', event => {
    _consoleStreamReceived = true;
    try {
      const payload = JSON.parse(event.data);
      renderLines(payload.log || '');
    } catch (error) {
      console.error('Console SSE parse error:', error);
    }
  });
  _consoleStream.addEventListener('build_result', event => {
    _consoleStreamReceived = true;
    try {
      const payload = JSON.parse(event.data);
      updateBadge(`Finished: ${payload.result || 'UNKNOWN'}`);
    } catch (error) {
      console.error('Console result SSE parse error:', error);
    }
    _isRunning = false;
    stopPolling();
    closeConsoleStream();

    const li = document.getElementById('liveIndicator');
    if (li) li.style.display = 'none';
  });
  _consoleStream.onerror = () => {
    closeConsoleStream();
    updateBadge('Live stream disconnected');
    console.warn('Console SSE stream closed. REST fallback polling is disabled.');
  };
}

function scrollToBottom() {
  document.getElementById('logBottom').scrollIntoView({ behavior: 'smooth' });
}

window.addEventListener('scroll', () => {
  const dist = document.body.scrollHeight - window.scrollY - window.innerHeight;
  _autoScroll = dist < 120;
});

document.addEventListener('DOMContentLoaded', () => {
  startConsoleStream();
});

window.addEventListener('beforeunload', closeConsoleStream);
