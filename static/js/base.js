//prometheus data
function getCssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function getUserDisplayPreferences() {
  const body = document.body;
  return {
    timeFormat: body?.dataset.timeFormat === '12h' ? '12h' : '24h',
    dateFormat: ['dd/mm/yyyy', 'mm/dd/yyyy', 'yyyy-mm-dd'].includes(body?.dataset.dateFormat)
      ? body.dataset.dateFormat
      : 'dd/mm/yyyy',
    timeZone: body?.dataset.timeZone || 'browser',
    showSeconds: body?.dataset.showSeconds === 'true'
  };
}

function getDisplayTimeZone() {
  const { timeZone } = getUserDisplayPreferences();
  return timeZone && timeZone !== 'browser' ? timeZone : undefined;
}

function normalizeDateValue(value) {
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value;
  }
  if (value === null || value === undefined || value === '') return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function getUserDateParts(dateValue) {
  const date = normalizeDateValue(dateValue);
  if (!date) return null;

  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: getDisplayTimeZone(),
    year: 'numeric',
    month: '2-digit',
    day: '2-digit'
  }).formatToParts(date);

  return parts.reduce((acc, part) => {
    if (part.type !== 'literal') acc[part.type] = part.value;
    return acc;
  }, {});
}

function formatUserDate(dateValue, opts = {}) {
  const parts = getUserDateParts(dateValue);
  if (!parts) return opts.fallback ?? '--';

  const { dateFormat } = getUserDisplayPreferences();
  const includeYear = opts.includeYear !== false;
  const year = parts.year;
  const month = parts.month;
  const day = parts.day;

  if (!includeYear) {
    if (dateFormat === 'mm/dd/yyyy') return `${month}/${day}`;
    if (dateFormat === 'yyyy-mm-dd') return `${month}-${day}`;
    return `${day}/${month}`;
  }

  if (dateFormat === 'mm/dd/yyyy') return `${month}/${day}/${year}`;
  if (dateFormat === 'yyyy-mm-dd') return `${year}-${month}-${day}`;
  return `${day}/${month}/${year}`;
}

function formatUserTime(dateValue, opts = {}) {
  const date = normalizeDateValue(dateValue);
  if (!date) return opts.fallback ?? '--';

  const { timeFormat, showSeconds } = getUserDisplayPreferences();
  return new Intl.DateTimeFormat(undefined, {
    timeZone: getDisplayTimeZone(),
    hour: '2-digit',
    minute: '2-digit',
    second: (opts.includeSeconds ?? showSeconds) ? '2-digit' : undefined,
    hour12: timeFormat === '12h'
  }).format(date);
}

function formatUserDateTime(dateValue, opts = {}) {
  const date = normalizeDateValue(dateValue);
  if (!date) return opts.fallback ?? '--';

  const includeDate = opts.includeDate !== false;
  const includeTime = opts.includeTime !== false;
  const parts = [];

  if (includeDate) {
    parts.push(formatUserDate(date, {
      includeYear: opts.includeYear !== false,
      fallback: opts.fallback
    }));
  }
  if (includeTime) {
    parts.push(formatUserTime(date, {
      includeSeconds: opts.includeSeconds,
      fallback: opts.fallback
    }));
  }
  return parts.join(' ');
}

function formatUserDateRange(startValue, endValue) {
  const start = normalizeDateValue(startValue);
  const end = normalizeDateValue(endValue);
  if (!start || !end) return '--';
  return `${formatUserDate(start, { includeYear: false })} - ${formatUserDate(end)}`;
}

function formatUserMonthYearLabel(dateValue, style = 'long') {
  const date = normalizeDateValue(dateValue);
  if (!date) return '--';
  return new Intl.DateTimeFormat(undefined, {
    timeZone: getDisplayTimeZone(),
    month: style === 'short' ? 'short' : 'long',
    year: 'numeric'
  }).format(date);
}

function formatTimeLabel(tsSeconds) {
  return formatUserTime(new Date(tsSeconds * 1000), { includeSeconds: false, fallback: '--' });
}

function avgValue(values = [], digits = 1) {
  if (!values.length) return null;
  const sum = values.reduce((a, b) => a + b, 0);
  return (sum / values.length).toFixed(digits);
}

function avgFromSeriesMap(seriesMap, digits = 1) {
  const values = [];
  Object.values(seriesMap || {}).forEach(points => {
    if (!Array.isArray(points)) return;
    points.forEach(([, v]) => {
      const num = Number(v);
      if (!Number.isNaN(num)) values.push(num);
    });
  });
  return avgValue(values, digits);
}

function applyLineDefaults(datasets, opts = {}) {
  const tension = opts.tension ?? 0.25;
  return (datasets || []).map(ds => {
    const borderColor = ds.borderColor || ds.color;
    const backgroundColor = ds.backgroundColor ?? (borderColor ? `${borderColor}22` : undefined);
    return {
      borderWidth: 2,
      pointRadius: 0,
      pointHoverRadius: 4,
      tension,
      fill: false,
      ...ds,
      borderColor,
      backgroundColor
    };
  });
}

function buildLineChart(ctx, labels, datasets, opts = {}) {
  const unit = opts.unit || '';
  const min = opts.min ?? 0;
  const max = opts.max ?? undefined;
  const maxTicksLimit = opts.maxTicksLimit ?? 10;
  const legendPosition = opts.legendPosition || 'bottom';
  const showLegend = opts.showLegend !== false;
  const enableDecimation = opts.decimation !== false;

  return new Chart(ctx, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: showLegend ? {
          position: legendPosition,
          labels: { color: getCssVar('--text2'), boxWidth: 10, boxHeight: 10 }
        } : { display: false },
        tooltip: {
          enabled: true,
          mode: 'index',
          intersect: false,
          callbacks: {
            label(context) {
              const value = context.raw;
              return `${context.dataset.label}: ${value}${unit}`;
            }
          }
        },
        decimation: enableDecimation ? { enabled: true, algorithm: 'lttb' } : { enabled: false }
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { color: getCssVar('--text2'), maxTicksLimit, autoSkip: true }
        },
        y: {
          min,
          max,
          grid: { color: getCssVar('--border') },
          ticks: {
            color: getCssVar('--text2'),
            callback: v => `${v}${unit}`
          }
        }
      }
    }
  });
}

// ── Chatbot ( for later )
let chatOpen=false;
let chatFullscreen=false;
let chatSending=false;
let chatHistory=[];
function setChatFullscreen(fullscreen){
  const panel=document.getElementById('chatPanel');
  const button=document.getElementById('chatFullscreenBtn');
  chatFullscreen=Boolean(fullscreen);
  if(panel)panel.classList.toggle('fullscreen',chatFullscreen);
  if(button){
    button.setAttribute('aria-pressed', chatFullscreen ? 'true' : 'false');
    button.setAttribute('aria-label', chatFullscreen ? 'Exit fullscreen chat' : 'Open fullscreen chat');
  }
}
function toggleChatFullscreen(){
  setChatFullscreen(!chatFullscreen);
  if(chatOpen)setTimeout(()=>document.getElementById('chatInput')?.focus(),120);
}
function toggleChat(){
  const panel=document.getElementById('chatPanel');
  if(!panel)return;
  chatOpen=!chatOpen;
  panel.classList.toggle('open',chatOpen);
  panel.setAttribute('aria-hidden', chatOpen ? 'false' : 'true');
  if(chatOpen)setTimeout(()=>document.getElementById('chatInput')?.focus(),320);
}
function resize(el){el.style.height='auto';el.style.height=Math.min(el.scrollHeight,78)+'px';}
function onKey(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();}}
function closeChat(){
  if(!chatOpen)return;
  const panel=document.getElementById('chatPanel');
  if(!panel)return;
  setChatFullscreen(false);
  chatOpen=false;
  panel.classList.remove('open');
  panel.setAttribute('aria-hidden','true');
}
function useSugg(el){
  const input=document.getElementById('chatInput');
  const suggestions=document.getElementById('chatSugg');
  if(!input)return;
  input.value=el.textContent;
  if(suggestions)suggestions.style.display='none';
  send();
}
function nowStr(){return formatUserTime(new Date(), { includeSeconds: false, fallback: '--' });}
function addMsg(txt,role){
  const box=document.getElementById('chatMsgs');
  if(!box)return;
  const d=document.createElement('div');d.className='msg '+role;
  d.innerHTML=`<div class="bubble">${escapeHtml(txt)}</div><div class="msg-time">${nowStr()}</div>`;
  box.appendChild(d);box.scrollTop=box.scrollHeight;
}
function showTyping(){const box=document.getElementById('chatMsgs');if(!box)return;const t=document.createElement('div');t.className='typing-bbl';t.id='typing';t.innerHTML='<span></span><span></span><span></span>';box.appendChild(t);box.scrollTop=box.scrollHeight;}
function hideTyping(){const t=document.getElementById('typing');if(t)t.remove();}
function getChatEndpoint(){
  const panel=document.getElementById('chatPanel');
  return panel?.dataset.chatUrl || '/api/chatbot/chat';
}
function setChatBusy(busy){
  chatSending=busy;
  const input=document.getElementById('chatInput');
  const button=document.getElementById('chatSendBtn');
  if(input)input.disabled=busy;
  if(button)button.disabled=busy;
}
function trimChatHistory(messages){
  return messages.slice(-12);
}
async function requestChatReply(messages){
  const res = await fetch(getChatEndpoint(), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages })
  });

  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(payload.error || 'The chatbot is unavailable right now.');
  }

  const reply = payload.reply;
  if (typeof reply !== 'string' || reply === '') {
    throw new Error('The chatbot returned an empty response.');
  }

  return payload;
}
async function send(){
  const inp=document.getElementById('chatInput');
  const suggestions=document.getElementById('chatSugg');
  if(!inp)return;
  const txt=inp.value.trim();if(!txt||chatSending)return;
  const pendingMessages=trimChatHistory([...chatHistory,{ role:'user', content:txt }]);
  addMsg(txt,'user');inp.value='';inp.style.height='auto';
  if(suggestions)suggestions.style.display='none';
  setChatBusy(true);
  showTyping();
  try{
    const payload=await requestChatReply(pendingMessages);
    const reply=payload.reply;
    hideTyping();
    addMsg(reply,'bot');
    chatHistory=trimChatHistory([...pendingMessages,{ role:'assistant', content:reply }]);
  }catch(e){
    hideTyping();
    addMsg(e.message || 'The chatbot is unavailable right now.','bot');
  }finally{
    setChatBusy(false);
    inp.focus();
  }
}
document.addEventListener('click',e=>{
  const panel=document.getElementById('chatPanel');
  if(!chatOpen||!panel)return;
  if(panel.contains(e.target)||e.target.closest('[data-chat-toggle]'))return;
  closeChat();
});
document.addEventListener('keydown',e=>{
  if(e.key!=='Escape'||!chatOpen)return;
  if(chatFullscreen){
    setChatFullscreen(false);
    return;
  }
  closeChat();
});

// ── Toast
function showToast(msg,cls=''){
  const t=document.getElementById('toast');
  if (!t) return;
  t.textContent=msg;t.className='toast '+(cls||'');t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'),3000);
}

function getPipelineName() {
  return document.body.dataset.pipelineName || 'django-pipeline';
}

function getBranchName() {
  return document.body.dataset.branchName || 'main';
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function pipelineStrongLabel() {
  return `<strong>${escapeHtml(getPipelineName())}</strong>`;
}

// Shared pipeline actions
async function apiTriggerBuild() {
  const res = await fetch('/api/build', { method: 'POST' });
  const data = await res.json();
  return { ok: res.ok, data };
}

async function apiAbortBuild(buildNumber) {
  const res = await fetch('/api/abort/' + buildNumber, { method: 'POST' });
  const data = await res.json();
  return { ok: res.ok, data };
}

function triggerBuildWithConfirmation(opts = {}) {
  const bodyHtml = opts.bodyHtml || `Trigger a new build for ${pipelineStrongLabel()} on <strong>${escapeHtml(getBranchName())}</strong>?`;
  const queuedMessage = opts.queuedMessage || '✅ Build queued';
  const triggerErrorMessage = opts.triggerErrorMessage || 'Failed to trigger build';
  const onQueued = typeof opts.onQueued === 'function' ? opts.onQueued : null;

  showConfirm(
    '▶ Start Build',
    bodyHtml,
    async () => {
      try {
        const { data } = await apiTriggerBuild();
        if (data.queued) {
          showToast(queuedMessage);
          if (onQueued) onQueued(data);
        } else {
          showToast('❌ ' + (data.error || triggerErrorMessage), 'abort-toast');
        }
      } catch (e) {
        showToast('❌ Network error', 'abort-toast');
      }
    }
  );
}

// ── PDF Export 
function exportPDF(){
  const {jsPDF}=window.jspdf;
  const doc=new jsPDF({orientation:'landscape',unit:'mm',format:'a4'});
  const dark=document.documentElement.getAttribute('data-theme')==='dark';
  const ts=formatUserDateTime(new Date(), { includeSeconds: false, fallback: '--' });
  doc.setFillColor(dark?11:240,dark?11:240,dark?18:248);doc.rect(0,0,297,210,'F');
  doc.setFillColor(124,111,255);doc.rect(0,0,297,22,'F');
  doc.setTextColor(255,255,255);doc.setFontSize(14);doc.setFont('helvetica','bold');
  doc.text('Jenkins Monitor — KPI Report',14,13);
  doc.setFontSize(8);doc.setFont('helvetica','normal');
  doc.text(`Generated: ${ts}  |  Pipeline: ${getPipelineName()}  |  Branch: ${getBranchName()}`,14,20);
  const total=document.getElementById('sv-total').textContent;
  const succ=document.getElementById('sv-success').textContent;
  const fail=document.getElementById('sv-failed').textContent;
  const abrt=document.getElementById('sv-aborted').textContent;
  const health=document.getElementById('health-val').textContent+'%';
  const rate=document.getElementById('rate-val').textContent+'%';
  const kpis=[
    {l:'Total Builds',v:total,s:'All time',c:[124,111,255]},
    {l:'Successful',v:succ,s:'Last 30 days',c:[0,219,160]},
    {l:'Failed',v:fail,s:'Last 30 days',c:[255,69,96]},
    {l:'Aborted',v:abrt,s:'Last 30 days',c:[255,140,66]},
    {l:'Health Score',v:health,s:'Index',c:[0,219,160]},
    {l:'Success Rate',v:rate,s:'Last 30 days',c:[58,184,248]},
  ];
  doc.setTextColor(dark?190:40,dark?190:40,dark?210:60);
  doc.setFontSize(9);doc.setFont('helvetica','bold');
  doc.text('KEY PERFORMANCE INDICATORS',14,32);
  kpis.forEach((k,i)=>{
    const x=14+i*47,y=36,w=44,h=30,[r,g,b]=k.c;
    doc.setFillColor(dark?18:255,dark?18:255,dark?28:255);doc.roundedRect(x,y,w,h,3,3,'F');
    doc.setFillColor(r,g,b);doc.roundedRect(x,y,w,3,1,1,'F');
    doc.setTextColor(dark?120:100,dark?120:100,dark?150:130);
    doc.setFontSize(6.5);doc.setFont('helvetica','bold');
    doc.text(k.l.toUpperCase(),x+3,y+9);
    doc.setTextColor(r,g,b);doc.setFontSize(13);doc.setFont('helvetica','bold');
    doc.text(k.v,x+3,y+20);
    doc.setTextColor(dark?120:100,dark?120:100,dark?150:130);
    doc.setFontSize(6.5);doc.setFont('helvetica','normal');
    doc.text(k.s,x+3,y+27);
  });
  doc.save(`jenkins-report-${Date.now()}.pdf`);
  showToast('PDF exported successfully');
}



// POLLING
let _polling = null;
function startPolling(ms) {
    if (_polling) clearInterval(_polling);
    _polling = setInterval(() => {
        checkStatus();
        checkAzureStatus();
        loadKPIs();
    }, ms);
}

// BROWSER NOTIFICATIONS
const _notifiedBuilds = new Set();

function requestNotificationPermission() {
    if ('Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission();
    }
}

function notifyBuildFinished(build) {
    if (_notifiedBuilds.has(build.number)) return;
    _notifiedBuilds.add(build.number);
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    const icons = { SUCCESS: '✅', FAILURE: '❌', ABORTED: '⊘' };
    const dur   = build.duration ? Math.round(build.duration / 1000) : 0;
    const m = Math.floor(dur / 60), s = dur % 60;
    new Notification(
        (icons[build.result] || '●') + ' Build #' + build.number + ' — ' + build.result,
        { body: 'Finished in ' + m + 'm ' + String(s).padStart(2,'0') + 's' }
    );
}

// CONFIRMATION MODAL
function showConfirm(title, body, onYes, onNo) {
    const old = document.getElementById('_confirmModal');
    if (old) old.remove();

    const overlay = document.createElement('div');
    overlay.id = '_confirmModal';
    overlay.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.6);display:flex;align-items:center;justify-content:center;backdrop-filter:blur(4px);';
    overlay.innerHTML = `
        <div style="background:var(--bg2);border:1px solid var(--border2);border-radius:18px;padding:28px 28px 22px;width:340px;box-shadow:0 24px 60px rgba(0,0,0,.7);">
            <div style="font-size:16px;font-weight:800;margin-bottom:8px;">${title}</div>
            <div style="font-size:13px;color:var(--text2);line-height:1.5;margin-bottom:22px;">${body}</div>
            <div style="display:flex;gap:10px;justify-content:flex-end;">
                <button id="_cNo"  style="padding:8px 18px;border-radius:9px;border:1px solid var(--border2);background:var(--bg3);color:var(--text2);font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;">Cancel</button>
                <button id="_cYes" style="padding:8px 18px;border-radius:9px;border:none;background:var(--accent);color:#fff;font-size:13px;font-weight:700;cursor:pointer;font-family:inherit;">Confirm</button>
            </div>
        </div>`;
    document.body.appendChild(overlay);
    document.getElementById('_cYes').onclick = () => { overlay.remove(); onYes(); };
    document.getElementById('_cNo').onclick  = () => { overlay.remove(); if (onNo) onNo(); };
    overlay.addEventListener('click', e => { if (e.target === overlay) { overlay.remove(); if (onNo) onNo(); }});
}



// Set correct theme icon on load
(function(){
  const sv = localStorage.getItem('jm-t') || 'dark';
  document.documentElement.setAttribute('data-theme', sv);
  document.querySelectorAll('.theme-btn').forEach(btn => {
    btn.textContent = sv === 'dark' ? '☀️' : '🌙';
  });
})();

function toggleTheme() {
  const root = document.documentElement;
  const current = root.getAttribute('data-theme') || 'dark';
  const next = current === 'dark' ? 'light' : 'dark';
  root.setAttribute('data-theme', next);
  localStorage.setItem('jm-t', next);

  document.querySelectorAll('.theme-btn').forEach(btn => {
    btn.textContent = next === 'dark' ? '☀️' : '🌙';
  });
}

// Refresh button with spin
function doRefresh(btn) {
  const b = btn || document.getElementById('refBtn');
  if (b) b.classList.add('spin');
  setTimeout(() => window.location.reload(), 700);
}

// Nav active state fallback
function setActive(el) {
  document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));
  el.classList.add('active');
}

// Connection status
async function checkStatus() {
  try {
    const res = await fetch('/api/status');
    if (!res.ok) throw new Error('status request failed');

    const data = await res.json();
    const dot  = document.getElementById('jenkinsStatusDot');
    const val  = document.getElementById('jenkinsStatusVal');

    if (!dot || !val) return;

    if (data.connected) {
      dot.classList.remove('pulse-dot-error');
      val.textContent = 'Connected';
      val.className   = 'ji-val ok';
    } else {
      dot.classList.add('pulse-dot-error');
      val.textContent = 'Disconnected';
      val.className   = 'ji-val error';
    }
  } catch (e) {
    const dot = document.getElementById('jenkinsStatusDot');
    const val = document.getElementById('jenkinsStatusVal');
    if (dot) dot.classList.add('pulse-dot-error');
    if (val) {
      val.textContent = 'Discionnected';
      val.className   = 'ji-val error';
    }
    console.error('Jenkins status error:', e);
  }
}

// azure connection status
async function checkAzureStatus() {
  try {
    const res = await fetch('/api/azure/status');
    if (!res.ok) throw new Error('azure status request failed');
    const data = await res.json().catch(() => ({ connected: false }));
    const dot = document.getElementById('azureStatusDot');
    const val = document.getElementById('azureStatusVal');

    if (!dot || !val) return;

    const connected = data && data.connected === true;
    if (connected) {
      dot.classList.remove('pulse-dot-error');
      val.textContent = 'Connected';
      val.className = 'ji-val ok';
    } else {
      dot.classList.add('pulse-dot-error');
      val.textContent = 'Disconnected';
      val.className = 'ji-val error';
    }
  } catch (e) {
    const dot = document.getElementById('azureStatusDot');
    const val = document.getElementById('azureStatusVal');

    if (dot) dot.classList.add('pulse-dot-error');
    if (val) {
      val.textContent = 'Unreachable';
      val.className = 'ji-val error';
    }

    console.error('Azure status error:', e);
  }
}

// Shared helpers
function fmtDur(ms) {
  if (!ms) return '0s';
  const s = Math.round(ms / 1000);
  const m = Math.floor(s / 60);
  return m > 0 ? m + 'm ' + String(s % 60).padStart(2, '0') + 's' : s + 's';
}

function resultCls(r) {
  return r === 'SUCCESS' ? 'pass' : r === 'FAILURE' ? 'fail' : 'abrt';
}

function resultLabel(r) {
  return r === 'SUCCESS' ? '✓ SUCCESS' : r === 'FAILURE' ? '✗ FAILURE' : '⊘ ' + (r || 'ABORTED');
}

function openConsole(num) {
  window.open('/console/' + num, '_blank');
}

function _renderBuildBars(builds) {
  const wrap   = document.getElementById('barsWrap');
  const sumRow = document.getElementById('buildSummaryRow');
  if (!wrap) return;

  const sorted = [...builds].reverse();
  const maxDur = Math.max(...sorted.map(b => b.duration || 1));
  const pass   = builds.filter(b => b.result === 'SUCCESS').length;
  const fail   = builds.filter(b => b.result === 'FAILURE').length;
  const abrt   = builds.filter(b => b.result === 'ABORTED').length;

  if (sumRow) {
    sumRow.innerHTML =
      '<div class="bstat pass"><div class="bstat-dot"></div>' + pass + ' Pass</div>' +
      '<div class="bstat fail"><div class="bstat-dot"></div>' + fail + ' Fail</div>' +
      '<div class="bstat abrt"><div class="bstat-dot"></div>' + abrt + ' Aborted</div>';
  }

  wrap.innerHTML = sorted.map(b => {
    const dur  = b.duration || 0;
    const mins = Math.floor(dur / 60000);
    const secs = Math.floor((dur % 60000) / 1000);
    const pct  = Math.max(5, Math.round((dur / maxDur) * 100));
    const cls  = b.result === 'SUCCESS' ? 'pass' : b.result === 'FAILURE' ? 'fail' : 'abrt';

    const richTooltip =
      `<div class="bar-tooltip-rich">
          <div class="btr-top">
              <div class="btr-num">#${b.number}</div>
              <div class="btr-result">${b.result || 'RUNNING'}</div>
          </div>
          <div class="btr-dur">${mins}m ${secs}s</div>
          
      </div>`;

    return '<div class="bar-col">'
      + richTooltip
      + '<div class="bar ' + cls + '" style="height:' + pct + '%"></div>'
      + '<div class="bar-lbl">#' + b.number + '</div>'
      + '</div>';
  }).join('');
}

// ── Latest Builds Chart (shared)
function renderLatestBuildsChart(builds) {
  _renderBuildBars(builds);
}

// ── Shared Stat Row
function updateStatRow(data) {
  const source = data.summary || data;
  const map = {
    'sv-total': source.total_builds,
    'sv-success': source.successful,
    'sv-failed': source.failed,
    'sv-aborted': source.aborted,
  };
  Object.entries(map).forEach(([id, value]) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = value ?? '--';
  });
}

function clearStatRow() {
  ['sv-total', 'sv-success', 'sv-failed', 'sv-aborted'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = '--';
  });
}


async function loadLatestBuild() {
  try {
    const res = await fetch('/api/latest_build');
    if (!res.ok) throw new Error();

    const data = await res.json();
    const el = document.getElementById('latestBuildTag');

    if (el && data.build_number) {
      el.textContent = '#' + data.build_number;
    }
  } catch (e) {
    console.error('Navbar build fetch failed', e);
  }
}

async function loadGitHubBadge() {
  try {
    const res = await fetch('/api/github');
    if (!res.ok) throw new Error();
    const data = await res.json();
    if (!data.connected) return;

    const commits = data.commits || [];
    const latestSha = commits[0] && commits[0].sha ? commits[0].sha : null;
    if (!latestSha) return;

    const seenSha = localStorage.getItem('gh-last-seen');
    let newCount = 0;
    if (seenSha) {
      for (const c of commits) {
        if (c.sha === seenSha) break;
        newCount += 1;
      }
    }

    const badge = document.getElementById('ghBadge');
    if (!badge) return;
    if (newCount > 0) {
      badge.textContent = String(newCount);
      badge.style.display = 'flex';
    } else {
      badge.style.display = 'none';
    }
  } catch (e) {
    console.error('GitHub badge fetch failed', e);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  checkStatus();
  checkAzureStatus();
  if (document.body.dataset.page !== 'pipeline-kpis') {
    loadLatestBuild();
  }
  loadGitHubBadge();
});




// CLEAR DASHBOARD
function clearDashboard() {
    clearStatRow();
    ['health','success-rate'].forEach(cls => {
        const card = document.querySelector('.kpi-card.' + cls); if (!card) return;
        const c = card.querySelector('.circle-progress'); if (c) c.style.strokeDashoffset = '150.796';
    });
    const hv = document.getElementById('health-val'); if (hv) hv.textContent = '0';
    const rv = document.getElementById('rate-val');   if (rv) rv.textContent = '0';
    ['health-badge','rate-badge'].forEach(id => {
        const el = document.getElementById(id);
        if (el) { el.className = 'kpi-badge red'; el.textContent = '⚠ No data'; }
    });
    const c = document.getElementById('activeBuildLines');
    if (c) c.innerHTML = '<div class="no-builds">No active builds — Jenkins is disconnected</div>';
    const b = document.getElementById('activeCountBadge'); if (b) b.textContent = '0 running';
    const w = document.getElementById('barsWrap');
    if (w) w.innerHTML = '<div class="no-builds" style="width:100%;text-align:center;">No build data available</div>';
    const s = document.getElementById('buildSummaryRow'); if (s) s.innerHTML = '';
}

function segCls(status){
    if(status === 'SUCCESS') return 'done';
    if(status === 'FAILED') return 'fail';
    if(status === 'ABORTED') return 'abrt';
    if(status === 'IN_PROGRESS') return 'run';
    return 'idle';
}
//confirm abort
function confirmAbort(buildNumber) {
  showConfirm(
    '⊘ Abort Build #' + buildNumber,
    'Are you sure you want to abort build <strong>#' + buildNumber + '</strong>?',
    async () => {
      try {
        const { data } = await apiAbortBuild(buildNumber);

        if (data.aborted) {
          showToast('Build #' + buildNumber + ' aborted');

          const row = document.getElementById('brow-' + buildNumber);
          if (row) {
            const resultSpan = row.querySelector('.br-result');
            if (resultSpan) {
              resultSpan.className = 'br-result abrt';
              resultSpan.textContent = '⊘ Aborted';
            }
            const durEl = document.getElementById('brdur-' + buildNumber);
            if (durEl) durEl.textContent = 'Build aborted';
            const abortBtn = row.querySelector('.br-abort');
            if (abortBtn) abortBtn.style.display = 'none';
          }

          if (_activeTimers[buildNumber]) {
            clearInterval(_activeTimers[buildNumber]);
            delete _activeTimers[buildNumber];
          }

          setTimeout(loadPipelineKPIs, 2000);
        } else {
          showToast('Failed to abort: ' + (data.error || 'unknown'), 'abort-toast');
        }
      } catch (e) {
        showToast('Network error during abort', 'abort-toast');
      }
    }
  );
}

// BAR CHART 
function renderBarChart(builds) {
    _renderBuildBars(builds);
}

//CIRCULAR PROGRESS
function updateCircle(cardCls, pct, valId, badgeId) {
    const card = document.querySelector('.kpi-card.' + cardCls);
    if (!card) return;
    const c = card.querySelector('.circle-progress');
    const v = document.getElementById(valId);
    const b = document.getElementById(badgeId);
    if (c) c.style.strokeDashoffset = 150.796 * (1 - pct / 100);
    if (v) v.textContent = Math.round(pct);
    if (b) {
        if (pct >= 80)      { b.className = 'kpi-badge green'; b.textContent = '↑ Excellent'; }
        else if (pct >= 50) { b.className = 'kpi-badge blue';  b.textContent = '~ Fair'; }
        else                { b.className = 'kpi-badge red';   b.textContent = '↓ Poor'; }
    }
}
