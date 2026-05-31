//prometheus data
function getCssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function getUserDisplayPreferences() {
  const body = document.body;
  const systemTimeZone = body?.dataset.systemTimeZone || 'UTC';
  return {
    timeFormat: '24h',
    dateFormat: 'yyyy-mm-dd',
    timeZone: systemTimeZone,
    showSeconds: false
  };
}

function getDisplayTimeZone() {
  const { timeZone } = getUserDisplayPreferences();
  return timeZone || undefined;
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

function buildUserDateKey(year, month, day) {
  return [
    String(year).padStart(4, '0'),
    String(month).padStart(2, '0'),
    String(day).padStart(2, '0')
  ].join('-');
}

function parseUserDateKey(key) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(key || ''));
  if (!match) return null;

  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (!(month >= 1 && month <= 12 && day >= 1 && day <= 31)) return null;

  return { year, month, day };
}

function getUserDateKey(dateValue) {
  const parts = getUserDateParts(dateValue);
  if (!parts) return null;
  return buildUserDateKey(parts.year, parts.month, parts.day);
}

function userDateKeyToUtcDate(key) {
  const parsed = parseUserDateKey(key);
  if (!parsed) return null;
  return new Date(Date.UTC(parsed.year, parsed.month - 1, parsed.day, 12, 0, 0));
}

function getUserDateKeySortValue(key) {
  const date = userDateKeyToUtcDate(key);
  return date ? date.getTime() : 0;
}

function getUserWeekStartKey(dateValueOrKey) {
  const key = parseUserDateKey(dateValueOrKey) ? dateValueOrKey : getUserDateKey(dateValueOrKey);
  const date = userDateKeyToUtcDate(key);
  if (!date) return null;

  const day = date.getUTCDay();
  const diff = day === 0 ? -6 : 1 - day;
  date.setUTCDate(date.getUTCDate() + diff);
  return buildUserDateKey(
    date.getUTCFullYear(),
    date.getUTCMonth() + 1,
    date.getUTCDate()
  );
}

function getUserWeekEndKey(startKey) {
  const date = userDateKeyToUtcDate(startKey);
  if (!date) return null;

  date.setUTCDate(date.getUTCDate() + 6);
  return buildUserDateKey(
    date.getUTCFullYear(),
    date.getUTCMonth() + 1,
    date.getUTCDate()
  );
}

function getUserMonthStartKey(dateValueOrKey) {
  const key = parseUserDateKey(dateValueOrKey) ? dateValueOrKey : getUserDateKey(dateValueOrKey);
  const parsed = parseUserDateKey(key);
  if (!parsed) return null;
  return buildUserDateKey(parsed.year, parsed.month, 1);
}

function getUserMonthEndKey(startKey) {
  const parsed = parseUserDateKey(startKey);
  if (!parsed) return null;

  const date = new Date(Date.UTC(parsed.year, parsed.month, 0, 12, 0, 0));
  return buildUserDateKey(
    date.getUTCFullYear(),
    date.getUTCMonth() + 1,
    date.getUTCDate()
  );
}

function formatUserDateKey(key, opts = {}) {
  const parsed = parseUserDateKey(key);
  if (!parsed) return opts.fallback ?? '--';

  const includeYear = opts.includeYear !== false;
  const monthStyle = opts.monthStyle || null;
  const year = String(parsed.year).padStart(4, '0');
  const month = String(parsed.month).padStart(2, '0');
  const day = String(parsed.day).padStart(2, '0');

  if (monthStyle === 'short' || monthStyle === 'long') {
    const date = userDateKeyToUtcDate(key);
    if (!date) return opts.fallback ?? '--';

    return new Intl.DateTimeFormat(undefined, {
      timeZone: 'UTC',
      month: monthStyle,
      day: 'numeric',
      year: includeYear ? 'numeric' : undefined
    }).format(date);
  }

  if (!includeYear) {
    return `${month}-${day}`;
  }

  return `${year}-${month}-${day}`;
}

function formatUserDateKeyRange(startKey, endKey, opts = {}) {
  const startLabel = formatUserDateKey(startKey, {
    includeYear: false,
    monthStyle: opts.monthStyle,
    fallback: opts.fallback
  });
  const endLabel = formatUserDateKey(endKey, {
    includeYear: true,
    monthStyle: opts.monthStyle,
    fallback: opts.fallback
  });
  return `${startLabel} - ${endLabel}`;
}

function formatUserMonthKeyLabel(startKey, style = 'long', opts = {}) {
  const date = userDateKeyToUtcDate(startKey);
  if (!date) return opts.fallback ?? '--';

  return new Intl.DateTimeFormat(undefined, {
    timeZone: 'UTC',
    month: style === 'short' ? 'short' : 'long',
    year: 'numeric'
  }).format(date);
}

function formatUserDate(dateValue, opts = {}) {
  const parts = getUserDateParts(dateValue);
  if (!parts) return opts.fallback ?? '--';

  const includeYear = opts.includeYear !== false;
  const year = parts.year;
  const month = parts.month;
  const day = parts.day;

  if (!includeYear) {
    return `${month}-${day}`;
  }

  return `${year}-${month}-${day}`;
}

function formatUserTime(dateValue, opts = {}) {
  const date = normalizeDateValue(dateValue);
  if (!date) return opts.fallback ?? '--';

  const { showSeconds } = getUserDisplayPreferences();
  return new Intl.DateTimeFormat(undefined, {
    timeZone: getDisplayTimeZone(),
    hour: '2-digit',
    minute: '2-digit',
    second: (opts.includeSeconds ?? showSeconds) ? '2-digit' : undefined,
    hour12: false
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
  if(chatOpen){
    setTimeout(()=>document.getElementById('chatInput')?.focus(),320);
  }
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
function canExportPdf() {
  return document.body?.dataset.canExportPdf === 'true';
}

function getExportReportUrl() {
  return document.body?.dataset.exportReportUrl || '';
}

function getStoreExportReportUrl() {
  return document.body?.dataset.storeExportReportUrl || '';
}

function setExportButtonBusy(isBusy) {
  const btn = document.getElementById('exportPdfBtn');
  if (!btn) return;

  if (!btn.dataset.originalLabel) {
    btn.dataset.originalLabel = btn.textContent;
  }

  btn.disabled = Boolean(isBusy);
  btn.textContent = isBusy ? 'Preparing...' : btn.dataset.originalLabel;
}

function hasPdfNumericValue(value) {
  return value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
}

function formatPdfPercent(value, digits = 1) {
  if (!hasPdfNumericValue(value)) return '--';
  return `${Number(value).toFixed(digits)}%`;
}

function formatPdfDuration(ms) {
  if (!hasPdfNumericValue(ms)) return '--';
  const totalMs = Number(ms);
  if (totalMs <= 0) return '--';

  const totalSeconds = Math.round(totalMs / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

function formatPdfCurrency(value, currencyCode = 'USD') {
  if (!hasPdfNumericValue(value)) return '--';
  const amount = Number(value);

  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency: currencyCode || 'USD',
      maximumFractionDigits: 2
    }).format(amount);
  } catch (e) {
    return `${currencyCode || 'USD'} ${amount.toFixed(2)}`;
  }
}

function truncatePdfText(value, maxLength = 90) {
  const text = String(value ?? '').trim();
  if (!text) return '--';
  if (text.length <= maxLength) return text;
  return `${text.slice(0, Math.max(0, maxLength - 1)).trimEnd()}...`;
}

function formatDockerImageReference(imageName, tag) {
  const cleanImageName = String(imageName || '').trim();
  const cleanTag = String(tag || '').trim();

  if (cleanImageName && cleanTag) {
    return cleanImageName.endsWith(`:${cleanTag}`)
      ? cleanImageName
      : `${cleanImageName}:${cleanTag}`;
  }
  return cleanImageName || cleanTag || '--';
}

async function fetchExportReportSnapshot() {
  const url = getExportReportUrl();
  if (!url) {
    throw new Error('PDF export endpoint is not configured.');
  }

  const res = await fetch(url, { headers: { Accept: 'application/json' } });
  const contentType = res.headers.get('content-type') || '';
  if (!contentType.includes('application/json')) {
    if (res.status === 401 || res.status === 403) {
      throw new Error('Your session no longer has access to the PDF export endpoint.');
    }
    if (res.status === 408 || res.status === 504) {
      throw new Error('The PDF export request timed out while waiting for dashboard data.');
    }
    throw new Error(
      res.ok
        ? 'The PDF export endpoint returned an unexpected response.'
        : `PDF export request failed with HTTP ${res.status}.`
    );
  }

  const data = await res.json();

  if (!res.ok) {
    throw new Error(data?.error || 'Failed to load PDF export data.');
  }

  return data || {};
}

async function storeExportedPdfReport(pdfBlob, payload) {
  const url = getStoreExportReportUrl();
  if (!url) {
    throw new Error('PDF archive endpoint is not configured.');
  }

  const formData = new FormData();
  formData.append('file', pdfBlob, payload.file_name || 'jenkins-monitor-report.pdf');
  formData.append('file_name', payload.file_name || 'jenkins-monitor-report.pdf');
  formData.append('generated_at', payload.generated_at || '');

  const res = await fetch(url, {
    method: 'POST',
    body: formData
  });

  const contentType = res.headers.get('content-type') || '';
  if (!contentType.includes('application/json')) {
    if (res.status === 401 || res.status === 403) {
      throw new Error('Your session no longer has access to the PDF archive endpoint.');
    }
    if (res.status === 408 || res.status === 504) {
      throw new Error('The PDF archive request timed out.');
    }
    throw new Error(
      res.ok
        ? 'The PDF archive endpoint returned an unexpected response.'
        : `PDF archive request failed with HTTP ${res.status}.`
    );
  }

  const data = await res.json();
  if (!res.ok) {
    throw new Error(data?.error || 'Failed to archive the PDF report.');
  }

  return data?.report || null;
}

function updatePdfReportsPage(report) {
  if (document.body?.dataset.page !== 'pdf-reports' || !report) return;

  const list = document.getElementById('pdfReportsList');
  if (!list) return;

  const existing = Array.from(list.querySelectorAll('.pdfr-card')).find(
    item => item.dataset.fileName === (report.file_name || '')
  );
  if (existing) existing.remove();

  const empty = document.getElementById('pdfReportsEmpty');
  if (empty) empty.style.display = 'none';
  list.style.display = 'flex';

  const card = document.createElement('div');
  card.className = 'pdfr-card';
  card.dataset.fileName = report.file_name || '';
  card.innerHTML = `
    <div class="pdfr-main">
      <a class="pdfr-link" href="${escapeHtml(report.view_url || '#')}" target="_blank" rel="noopener">${escapeHtml(report.file_name || 'report.pdf')}</a>
      <div class="pdfr-meta">Exported ${escapeHtml(report.exported_at_label || '--')}</div>
      <div class="pdfr-filepath">${escapeHtml(report.absolute_path || '--')}</div>
    </div>
    <div class="pdfr-side">
      <div class="pdfr-size">${escapeHtml(String(report.size_kb ?? '--'))} KB</div>
      <a class="pdfr-open" href="${escapeHtml(report.view_url || '#')}" target="_blank" rel="noopener">Open</a>
      <a class="pdfr-download" href="${escapeHtml(report.download_url || '#')}">Download</a>
    </div>
  `;
  list.prepend(card);

  const count = list.querySelectorAll('.pdfr-card').length;
  const countBadge = document.getElementById('pdfReportsCount');
  const statCount = document.getElementById('pdfReportsStatCount');
  if (countBadge) countBadge.textContent = String(count);
  if (statCount) statCount.textContent = String(count);
}

function buildPdfReportSummary(payload) {
  const latestBuild = payload.latest_build || {};
  const finops = payload.finops || {};
  const github = payload.github || {};
  const sonar = payload.sonarqube || {};
  const kubernetes = payload.kubernetes || {};
  const docker = payload.docker || {};
  const mainCommit = github.main_commit || {};
  const dockerImageReference = formatDockerImageReference(docker.image_name, docker.tag);
  const exportedAt = formatUserDateTime(payload.generated_at, {
    includeSeconds: true,
    fallback: '--'
  });
  const metaLine = [
    `Exported: ${exportedAt}`,
    `Branch: ${payload.branch_name || getBranchName()}`,
    `Status: ${latestBuild.status || '--'}`,
    `Pipeline: ${payload.pipeline_name || getPipelineName()}`
  ].join(' | ');

  return {
    title: 'Pipeline Report',
    latestBuild,
    cards: [
      {
        title: 'Latest Build',
        value: latestBuild.number != null ? `#${latestBuild.number}` : '--',
        note: ''
      },
      {
        title: 'Latest Build Status',
        value: latestBuild.status || '--',
        note: ''
      },
      {
        title: 'Azure Cost',
        value: formatPdfCurrency(finops.day_cost, finops.currency_code || 'USD'),
        note: ''
      },
      {
        title: 'Open Pull Requests',
        value: hasPdfNumericValue(github.open_pr_count) ? String(github.open_pr_count) : '--',
        note: ''
      },
      {
        title: 'SonarQube Issues',
        value: hasPdfNumericValue(sonar.total_issues) ? String(sonar.total_issues) : '--',
        note: ''
      },
      {
        title: 'Average Build Duration',
        value: formatPdfDuration(payload.average_duration_ms),
        note: ''
      },
      {
        title: 'Average Test Coverage',
        value: formatPdfPercent(payload.average_test_coverage, 1),
        note: ''
      },
      {
        title: 'Pipeline Success Rate',
        value: formatPdfPercent(payload.success_rate, 1),
        note: ''
      },
      {
        title: 'Pods Running',
        value: (
          hasPdfNumericValue(kubernetes.pods_running) &&
          hasPdfNumericValue(kubernetes.pods_total)
        )
          ? `${kubernetes.pods_running} / ${kubernetes.pods_total}`
          : '--',
        note: ''
      },
      {
        title: 'Jenkins Health',
        value: formatPdfPercent(payload.jenkins_health_score, 0),
        note: ''
      },
      {
        title: 'Latest Docker Image',
        value: dockerImageReference,
        note: '',
        valueFontSize: 8.5,
        valueMaxLines: 4
      }
    ],
    commitLines: [
      `Commit: ${mainCommit.short_sha || '--'}${mainCommit.author_name ? ` by ${mainCommit.author_name}` : ''}`,
      `Date: ${mainCommit.date ? formatUserDateTime(mainCommit.date, { includeSeconds: false, fallback: '--' }) : '--'}`,
      `Message: ${truncatePdfText(mainCommit.headline || mainCommit.message || '--', 110)}`
    ],
    warningLines: Array.isArray(payload.warnings)
      ? payload.warnings.map(item => truncatePdfText(item, 140)).filter(Boolean)
      : [],
    footerText: `Generated file: ${payload.file_name || 'jenkins-monitor-report.pdf'}`,
    metaLine
  };
}

function saveBlobAsFile(blob, fileName) {
  if (!(blob instanceof Blob)) {
    throw new Error('No PDF data was generated.');
  }

  const downloadUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = downloadUrl;
  link.download = fileName || `jenkins-monitor-report-${Date.now()}.pdf`;
  link.style.display = 'none';
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 1500);
}

function sanitizePdfText(value) {
  let text = String(value ?? '');
  if (typeof text.normalize === 'function') {
    text = text.normalize('NFKD');
  }

  return text
    .replace(/[“”]/g, '"')
    .replace(/[‘’]/g, "'")
    .replace(/[–—]/g, '-')
    .replace(/…/g, '...')
    .replace(/[^\x20-\x7E\n]/g, ' ')
    .replace(/[ \t]+/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function escapePdfLiteral(value) {
  return sanitizePdfText(value)
    .replace(/\\/g, '\\\\')
    .replace(/\(/g, '\\(')
    .replace(/\)/g, '\\)');
}

function splitLongPdfWord(word, maxChars) {
  if (word.length <= maxChars) return [word];
  const parts = [];
  for (let index = 0; index < word.length; index += maxChars) {
    parts.push(word.slice(index, index + maxChars));
  }
  return parts;
}

function wrapPdfText(value, maxChars = 90) {
  const paragraphs = String(value ?? '')
    .split('\n')
    .map(part => sanitizePdfText(part));
  const lines = [];

  paragraphs.forEach(paragraph => {
    if (!paragraph) {
      if (lines.length && lines[lines.length - 1] !== '') {
        lines.push('');
      }
      return;
    }

    let currentLine = '';
    paragraph.split(/\s+/).forEach(word => {
      const parts = splitLongPdfWord(word, Math.max(12, maxChars));
      parts.forEach(part => {
        const candidate = currentLine ? `${currentLine} ${part}` : part;
        if (candidate.length <= maxChars) {
          currentLine = candidate;
        } else {
          if (currentLine) lines.push(currentLine);
          currentLine = part;
        }
      });
    });

    if (currentLine) lines.push(currentLine);
  });

  return lines.length ? lines : ['--'];
}

function pdfColorCommand(color, operator) {
  const safeColor = Array.isArray(color) && color.length === 3 ? color : [24, 36, 48];
  const normalized = safeColor.map(value => (Math.max(0, Math.min(255, Number(value) || 0)) / 255).toFixed(3));
  return `${normalized.join(' ')} ${operator}`;
}

function buildPdfBlobFromPages(pages, opts = {}) {
  const pageWidth = Number(opts.pageWidth) || 595.28;
  const pageHeight = Number(opts.pageHeight) || 841.89;
  const footerText = sanitizePdfText(opts.footerText || '');
  const footerY = Number(opts.footerY) || 24;
  const footerLeftX = Number(opts.footerLeftX) || 42;
  const footerRightX = Number(opts.footerRightX) || pageWidth - 90;
  const safePages = Array.isArray(pages) && pages.length ? pages : [[]];
  const pageCount = safePages.length;
  const objects = [];
  const pageIds = [];
  const contentIds = [];

  for (let index = 0; index < pageCount; index += 1) {
    pageIds.push(5 + (index * 2));
    contentIds.push(6 + (index * 2));
  }

  objects[0] = '<< /Type /Catalog /Pages 2 0 R >>';
  objects[1] = `<< /Type /Pages /Count ${pageCount} /Kids [${pageIds.map(id => `${id} 0 R`).join(' ')}] >>`;
  objects[2] = '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>';
  objects[3] = '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>';

  safePages.forEach((pageItems, pageIndex) => {
    const pageId = pageIds[pageIndex];
    const contentId = contentIds[pageIndex];
    const commands = [];

    (Array.isArray(pageItems) ? pageItems : []).forEach(item => {
      if (!item) return;

      if (item.type === 'line') {
        commands.push(pdfColorCommand(item.color || [205, 214, 223], 'RG'));
        commands.push(`${Number(item.width || 0.8).toFixed(2)} w`);
        commands.push(`${Number(item.x1 || 0).toFixed(2)} ${Number(item.y1 || 0).toFixed(2)} m`);
        commands.push(`${Number(item.x2 || 0).toFixed(2)} ${Number(item.y2 || 0).toFixed(2)} l`);
        commands.push('S');
        return;
      }

      const text = escapePdfLiteral(item.text || ' ');
      commands.push('BT');
      commands.push(`/${item.font || 'F1'} ${Number(item.fontSize || 11).toFixed(2)} Tf`);
      commands.push(pdfColorCommand(item.color || [24, 36, 48], 'rg'));
      commands.push(`1 0 0 1 ${Number(item.x || 0).toFixed(2)} ${Number(item.y || 0).toFixed(2)} Tm`);
      commands.push(`(${text || ' '}) Tj`);
      commands.push('ET');
    });

    if (footerText) {
      commands.push('BT');
      commands.push('/F1 8.00 Tf');
      commands.push(pdfColorCommand([96, 109, 121], 'rg'));
      commands.push(`1 0 0 1 ${footerLeftX.toFixed(2)} ${footerY.toFixed(2)} Tm`);
      commands.push(`(${escapePdfLiteral(footerText)}) Tj`);
      commands.push('ET');
    }

    commands.push('BT');
    commands.push('/F1 8.00 Tf');
    commands.push(pdfColorCommand([96, 109, 121], 'rg'));
    commands.push(`1 0 0 1 ${footerRightX.toFixed(2)} ${footerY.toFixed(2)} Tm`);
    commands.push(`(${escapePdfLiteral(`Page ${pageIndex + 1} of ${pageCount}`)}) Tj`);
    commands.push('ET');

    const stream = commands.join('\n');
    objects[pageId - 1] = [
      `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${pageWidth.toFixed(2)} ${pageHeight.toFixed(2)}]`,
      '/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >>',
      `/Contents ${contentId} 0 R >>`
    ].join(' ');
    objects[contentId - 1] = `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`;
  });

  let pdfContent = '%PDF-1.4\n';
  const offsets = [0];

  objects.forEach((objectBody, index) => {
    offsets[index + 1] = pdfContent.length;
    pdfContent += `${index + 1} 0 obj\n${objectBody}\nendobj\n`;
  });

  const xrefOffset = pdfContent.length;
  pdfContent += `xref\n0 ${objects.length + 1}\n`;
  pdfContent += '0000000000 65535 f \n';

  for (let index = 1; index <= objects.length; index += 1) {
    pdfContent += `${String(offsets[index]).padStart(10, '0')} 00000 n \n`;
  }

  pdfContent += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF`;
  return new Blob([pdfContent], { type: 'application/pdf' });
}

function buildFallbackPdfReportBlob(payload) {
  const summary = buildPdfReportSummary(payload);
  const pageWidth = 595.28;
  const pageHeight = 841.89;
  const margin = 42;
  const bottomMargin = 54;
  const usableWidth = pageWidth - (margin * 2);
  const pages = [];
  let currentPage = null;
  let y = 0;

  function approxMaxChars(fontSize, width = usableWidth) {
    return Math.max(24, Math.floor(width / Math.max(5, fontSize * 0.52)));
  }

  function startPage() {
    currentPage = [];
    pages.push(currentPage);
    y = pageHeight - margin;

    currentPage.push({
      type: 'text',
      x: margin,
      y,
      text: summary.title,
      font: 'F2',
      fontSize: 16,
      color: [40, 84, 107]
    });
    y -= 22;

    wrapPdfText(summary.metaLine, approxMaxChars(10)).slice(0, 2).forEach(line => {
      currentPage.push({
        type: 'text',
        x: margin,
        y,
        text: line,
        font: 'F1',
        fontSize: 10,
        color: [96, 109, 121]
      });
      y -= 14;
    });

    currentPage.push({
      type: 'line',
      x1: margin,
      y1: y + 4,
      x2: pageWidth - margin,
      y2: y + 4,
      width: 0.8,
      color: [205, 214, 223]
    });
    y -= 10;
  }

  function ensureSpace(heightNeeded) {
    if (!currentPage) {
      startPage();
      return;
    }
    if ((y - heightNeeded) >= bottomMargin) return;
    startPage();
  }

  function addTextBlock(text, opts = {}) {
    const fontSize = opts.fontSize || 11;
    const x = opts.x || margin;
    const width = opts.width || usableWidth;
    const lineHeight = Math.max(11, fontSize * 1.35);
    const gapAfter = opts.gapAfter ?? 4;
    const textLines = Array.isArray(text)
      ? text.flatMap(line => wrapPdfText(line, approxMaxChars(fontSize, width)))
      : wrapPdfText(text, approxMaxChars(fontSize, width));
    const printableLines = textLines.length ? textLines : ['--'];

    ensureSpace((printableLines.length * lineHeight) + gapAfter);
    printableLines.forEach(line => {
      currentPage.push({
        type: 'text',
        x,
        y,
        text: line || ' ',
        font: opts.bold ? 'F2' : 'F1',
        fontSize,
        color: opts.color || [24, 36, 48]
      });
      y -= lineHeight;
    });
    y -= gapAfter;
  }

  function addSectionTitle(title) {
    addTextBlock(title, {
      fontSize: 13,
      bold: true,
      color: [40, 84, 107],
      gapAfter: 2
    });
  }

  function addSpacer(height = 4) {
    ensureSpace(height);
    y -= height;
  }

  startPage();
  addSectionTitle('Summary');
  summary.cards.forEach(card => {
    addTextBlock(`${card.title}: ${card.value || '--'}`, {
      fontSize: 11,
      gapAfter: 1
    });
  });

  addSpacer(4);
  addSectionTitle('Last Commit on main');
  addTextBlock(summary.commitLines, {
    fontSize: 10.5,
    gapAfter: 3
  });

  return buildPdfBlobFromPages(pages, {
    pageWidth,
    pageHeight,
    footerText: summary.footerText,
    footerLeftX: margin,
    footerRightX: pageWidth - 84,
    footerY: 24
  });
}

function buildJsPdfReportBlob(payload) {
  const jsPdfCtor = window.jspdf?.jsPDF;
  if (typeof jsPdfCtor !== 'function') {
    return null;
  }

  const summary = buildPdfReportSummary(payload);
  const doc = new jsPdfCtor({ orientation: 'portrait', unit: 'mm', format: 'a4' });
  const pageWidth = doc.internal.pageSize.getWidth();
  const pageHeight = doc.internal.pageSize.getHeight();
  const margin = 10;
  const contentWidth = pageWidth - (margin * 2);
  const cardGap = 3;
  const cardWidth = (contentWidth - cardGap) / 2;
  const rowGap = 2;
  let y = margin;

  function addHeader() {
    const headerHeight = 16;
    doc.setTextColor(40, 84, 107);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(13);
    doc.text(summary.title, margin, 8);
    doc.setTextColor(96, 109, 121);
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(7.5);

    const metaLines = doc.splitTextToSize(summary.metaLine, contentWidth).slice(0, 2);
    doc.text(metaLines, margin, 12.5);

    doc.setTextColor(24, 36, 48);
    y = headerHeight + 3;
  }

  function ensureSpace(heightNeeded) {
    if (y + heightNeeded <= pageHeight - 16) return;
    doc.addPage();
    addHeader();
  }

  function getMetricBlockHeight(card, width) {
    const valueFontSize = card.valueFontSize || 10;
    const valueMaxLines = card.valueMaxLines || 2;
    const noteMaxLines = card.noteMaxLines || 1;
    const titleLines = doc.splitTextToSize(String(card.title || ''), width).slice(0, 2);
    const valueLines = doc.splitTextToSize(String(card.value ?? '--'), width).slice(0, valueMaxLines);
    const noteLines = card.note
      ? doc.splitTextToSize(String(card.note), width).slice(0, noteMaxLines)
      : [];

    const titleHeight = titleLines.length * 2.9;
    const valueHeight = valueLines.length * (valueFontSize >= 10 ? 3.9 : 3.4);
    const noteHeight = noteLines.length ? (noteLines.length * 2.8) + 0.8 : 0;
    return Math.max(11, 2.5 + titleHeight + valueHeight + noteHeight);
  }

  function drawMetricCard(x, top, title, value, note = '', opts = {}) {
    const width = opts.width || cardWidth;
    const valueFontSize = opts.valueFontSize || 10;
    const valueMaxLines = opts.valueMaxLines || 2;
    const noteMaxLines = opts.noteMaxLines || 1;
    const titleLines = doc.splitTextToSize(String(title || ''), width).slice(0, 2);
    const valueLines = doc.splitTextToSize(String(value ?? '--'), width).slice(0, valueMaxLines);
    const noteLines = note
      ? doc.splitTextToSize(String(note), width).slice(0, noteMaxLines)
      : [];
    let cursorY = top + 3;

    doc.setTextColor(96, 109, 121);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(6.5);
    doc.text(titleLines, x, cursorY);
    cursorY += (titleLines.length * 2.9) + 0.8;

    doc.setTextColor(24, 36, 48);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(valueFontSize);
    doc.text(valueLines, x, cursorY);

    if (noteLines.length) {
      cursorY += (valueLines.length * (valueFontSize >= 10 ? 3.9 : 3.4)) + 0.7;
      doc.setTextColor(96, 109, 121);
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(6.2);
      doc.text(noteLines, x, cursorY);
    }
  }

  function drawSection(title, lines) {
    const printableLines = (Array.isArray(lines) ? lines : [])
      .map(line => String(line || '').trim())
      .filter(Boolean);

    if (!printableLines.length) return;

    const wrappedLines = [];
    printableLines.forEach(line => {
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(7.5);
      const parts = doc.splitTextToSize(line, contentWidth);
      parts.forEach(part => wrappedLines.push(part));
    });

    const sectionHeight = 4 + (wrappedLines.length * 3.6);
    ensureSpace(sectionHeight + rowGap);

    doc.setTextColor(40, 84, 107);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(8);
    doc.text(title, margin, y + 3);

    doc.setTextColor(24, 36, 48);
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(7.5);
    doc.text(wrappedLines, margin, y + 7);

    y += sectionHeight + rowGap;
  }

  addHeader();

  for (let index = 0; index < summary.cards.length; index += 2) {
    const leftCard = summary.cards[index];
    const rightCard = summary.cards[index + 1] || null;
    const leftWidth = rightCard ? cardWidth : contentWidth;
    const leftHeight = getMetricBlockHeight(leftCard, leftWidth);
    const rightHeight = rightCard ? getMetricBlockHeight(rightCard, cardWidth) : 0;
    const rowHeight = Math.max(leftHeight, rightHeight);

    ensureSpace(rowHeight + rowGap);

    drawMetricCard(margin, y, leftCard.title, leftCard.value, leftCard.note, {
      width: leftWidth,
      valueFontSize: leftCard.valueFontSize,
      valueMaxLines: leftCard.valueMaxLines,
      noteMaxLines: leftCard.noteMaxLines
    });

    if (rightCard) {
      drawMetricCard(margin + cardWidth + cardGap, y, rightCard.title, rightCard.value, rightCard.note, {
        width: cardWidth,
        valueFontSize: rightCard.valueFontSize,
        valueMaxLines: rightCard.valueMaxLines,
        noteMaxLines: rightCard.noteMaxLines
      });
    }

    y += rowHeight + rowGap;
  }

  drawSection('Last Commit on main', summary.commitLines);

  doc.setTextColor(96, 109, 121);
  doc.setFont('helvetica', 'normal');
  doc.setFontSize(7);
  doc.text(summary.footerText, margin, pageHeight - 8);
  return doc.output('blob');
}

function buildPdfReportBlob(payload) {
  try {
    const jsPdfBlob = buildJsPdfReportBlob(payload);
    if (jsPdfBlob) {
      return { blob: jsPdfBlob, usedFallback: false };
    }
  } catch (error) {
    console.warn('jsPDF export failed, using the built-in PDF fallback.', error);
  }

  return { blob: buildFallbackPdfReportBlob(payload), usedFallback: true };
}

async function exportPDF() {
  if (!canExportPdf()) {
    return;
  }

  setExportButtonBusy(true);

  try {
    const payload = await fetchExportReportSnapshot();
    const { blob: pdfBlob } = buildPdfReportBlob(payload);
    let storedReport = null;
    let archiveError = null;

    try {
      storedReport = await storeExportedPdfReport(pdfBlob, payload);
      updatePdfReportsPage(storedReport);
    } catch (error) {
      archiveError = error;
    }

    saveBlobAsFile(pdfBlob, payload.file_name || `jenkins-monitor-report-${Date.now()}.pdf`);

    if (storedReport) {
      showToast('PDF exported and archived successfully');
    } else if (archiveError) {
      showToast(`PDF exported locally, but archive failed: ${archiveError?.message || 'unknown error'}`, 'abort-toast');
    } else {
      showToast('PDF exported successfully');
    }
  } catch (error) {
    showToast(`❌ ${error?.message || 'PDF export failed'}`, 'abort-toast');
  } finally {
    setExportButtonBusy(false);
  }
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

function applyJenkinsStatusPayload(data) {
  const dot = document.getElementById('jenkinsStatusDot');
  const val = document.getElementById('jenkinsStatusVal');
  if (!dot || !val) return;

  if (data && data.connected) {
    dot.classList.remove('pulse-dot-error');
    val.textContent = 'Connected';
    val.className = 'ji-val ok';
    return;
  }

  dot.classList.add('pulse-dot-error');
  val.textContent = 'Disconnected';
  val.className = 'ji-val error';
}

// Connection status
async function checkStatus() {
  try {
    const res = await fetch('/api/status');
    if (!res.ok) throw new Error('status request failed');
    applyJenkinsStatusPayload(await res.json());
  } catch (e) {
    applyJenkinsStatusPayload({ connected: false });
    console.error('Jenkins status error:', e);
  }
}

function applyAzureStatusPayload(data) {
  const dot = document.getElementById('azureStatusDot');
  const val = document.getElementById('azureStatusVal');
  if (!dot || !val) return;

  if (data && data.connected === true) {
    dot.classList.remove('pulse-dot-error');
    val.textContent = 'Connected';
    val.className = 'ji-val ok';
    return;
  }

  dot.classList.add('pulse-dot-error');
  val.textContent = data ? 'Disconnected' : 'Unreachable';
  val.className = 'ji-val error';
}

function pageUsesLiveStatusStream() {
  return (
    typeof window.EventSource !== 'undefined' &&
    Boolean(document.body.dataset.liveStreamUrl)
  );
}

// azure connection status
async function checkAzureStatus() {
  try {
    const res = await fetch('/api/azure/status');
    const data = await res.json().catch(() => null);
    if (!data) throw new Error('azure status response failed');
    applyAzureStatusPayload(data);
  } catch (e) {
    applyAzureStatusPayload(null);
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

function refreshBuildViewsAfterMutation({ liveDelayMs = 200, fullDelayMs = 1500 } = {}) {
  const page = document.body.dataset.page || '';
  const overviewUsesLiveStream = page === 'overview' && pageUsesLiveStatusStream();

  if (!overviewUsesLiveStream && typeof refreshRunningBuildsNow === 'function') {
    setTimeout(() => refreshRunningBuildsNow(), liveDelayMs);
    setTimeout(() => refreshRunningBuildsNow(), fullDelayMs);
  }
  if (!overviewUsesLiveStream && typeof loadKPIs === 'function') {
    setTimeout(() => loadKPIs(), fullDelayMs);
  }
  if (typeof loadPipelineKPIs === 'function') {
    setTimeout(() => loadPipelineKPIs(), fullDelayMs);
  }
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
  const badge = document.getElementById('ghBadge');
  if (!badge) return;
  try {
    const res = await fetch('/api/github/badge');
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

const ALERTS_BADGE_POLL_MS = 15_000;
let _alertsBadgePollHandle = null;

function setAlertsSidebarBadgeCount(count) {
  const badge = document.getElementById('alertsBadge');
  if (!badge) return;

  const normalizedCount = Math.max(0, Number(count) || 0);
  if (normalizedCount > 0) {
    badge.textContent = normalizedCount > 99 ? '99+' : String(normalizedCount);
    badge.style.display = 'flex';
  } else {
    badge.textContent = '';
    badge.style.display = 'none';
  }
}

function applyAlertsSidebarBadgePayload(payload) {
  const summaryCount = Number(payload?.summary?.alert_count);
  const listCount = Array.isArray(payload?.alerts) ? payload.alerts.length : 0;
  const nextCount = Number.isFinite(summaryCount) ? summaryCount : listCount;
  setAlertsSidebarBadgeCount(nextCount);
}

window.setAlertsSidebarBadgeCount = setAlertsSidebarBadgeCount;
window.applyAlertsSidebarBadgePayload = applyAlertsSidebarBadgePayload;

async function loadAlertsSidebarBadge() {
  const badge = document.getElementById('alertsBadge');
  const url = document.body.dataset.alertsBadgeUrl || '';
  if (!badge || !url) return;

  try {
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) throw new Error();
    const data = await res.json();
    applyAlertsSidebarBadgePayload(data);
  } catch (e) {
    console.error('Alerts badge fetch failed', e);
  }
}

function startAlertsSidebarBadgePolling() {
  const badge = document.getElementById('alertsBadge');
  const url = document.body.dataset.alertsBadgeUrl || '';
  if (!badge || !url || _alertsBadgePollHandle) return;

  _alertsBadgePollHandle = window.setInterval(() => {
    void loadAlertsSidebarBadge();
  }, ALERTS_BADGE_POLL_MS);
}

document.addEventListener('DOMContentLoaded', () => {
  const page = document.body.dataset.page || '';
  if (!pageUsesLiveStatusStream()) {
    checkStatus();
    checkAzureStatus();
  }
  if (!['pipeline-kpis', 'overview'].includes(page)) {
    loadLatestBuild();
  }
  if (page !== 'github') {
    loadGitHubBadge();
  }
  if (document.getElementById('alertsBadge') && page !== 'alerts') {
    void loadAlertsSidebarBadge();
    startAlertsSidebarBadgePolling();
  }
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

          if (typeof handleBuildAbortSuccess === 'function') {
            handleBuildAbortSuccess(buildNumber);
          }

          refreshBuildViewsAfterMutation();
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
