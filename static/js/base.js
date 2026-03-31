
// ── Chatbot
let chatOpen=false;
function toggleChat(){
  chatOpen=!chatOpen;
  document.getElementById('chatPanel').classList.toggle('open',chatOpen);
  if(chatOpen)setTimeout(()=>document.getElementById('chatInput').focus(),320);
}
function resize(el){el.style.height='auto';el.style.height=Math.min(el.scrollHeight,78)+'px';}
function onKey(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();}}
function useSugg(el){document.getElementById('chatInput').value=el.textContent;document.getElementById('chatSugg').style.display='none';send();}
function nowStr(){return new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});}
function addMsg(txt,role){
  const box=document.getElementById('chatMsgs');
  const d=document.createElement('div');d.className='msg '+role;
  d.innerHTML=`<div class="bubble">${txt}</div><div class="msg-time">${nowStr()}</div>`;
  box.appendChild(d);box.scrollTop=box.scrollHeight;
}
function showTyping(){const box=document.getElementById('chatMsgs');const t=document.createElement('div');t.className='typing-bbl';t.id='typing';t.innerHTML='<span></span><span></span><span></span>';box.appendChild(t);box.scrollTop=box.scrollHeight;}
function hideTyping(){const t=document.getElementById('typing');if(t)t.remove();}
const BOT=[
  {p:/fail|error/i,      r:"The last failure was likely due to a test timeout or environment issue. Check the build console for details."},
  {p:/coverage|trend/i,  r:"Coverage has been trending upward. Check the Pipeline KPIs section for the full breakdown."},
  {p:/slow|duration/i,   r:"The slowest builds are usually the ones running full integration test suites."},
  {p:/deploy|status/i,   r:"Last deployment completed successfully. All health checks passed."},
  {p:/hello|hi|hey/i,    r:"Hey! What would you like to know about your pipeline?"},
  {p:/success|rate/i,    r:"Your current success rate is shown in the KPI circles on the dashboard."},
  {p:/health/i,          r:"Health score reflects the ratio of successful builds over the last 10 runs."},
];
function getBotReply(m){for(const b of BOT)if(b.p.test(m))return b.r;return "I don't have specific data on that yet. Try asking about failures, coverage, or deployments.";}
async function send(){
  const inp=document.getElementById('chatInput');
  const txt=inp.value.trim();if(!txt)return;
  addMsg(txt,'user');inp.value='';inp.style.height='auto';
  document.getElementById('chatSugg').style.display='none';
  showTyping();
  await new Promise(r=>setTimeout(r,800+Math.random()*600));
  hideTyping();addMsg(getBotReply(txt),'bot');
}

// ── Toast
function showToast(msg,cls=''){
  const t=document.getElementById('toast');
  if (!t) return;
  t.textContent=msg;t.className='toast '+(cls||'');t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'),3000);
}

// Shared pipeline actions
async function apiTriggerBuild() {
  const res = await fetch('/jenkins/api/build', { method: 'POST' });
  const data = await res.json();
  return { ok: res.ok, data };
}

async function apiAbortBuild(buildNumber) {
  const res = await fetch('/jenkins/api/abort/' + buildNumber, { method: 'POST' });
  const data = await res.json();
  return { ok: res.ok, data };
}

// ── PDF Export
function exportPDF(){
  const {jsPDF}=window.jspdf;
  const doc=new jsPDF({orientation:'landscape',unit:'mm',format:'a4'});
  const dark=document.documentElement.getAttribute('data-theme')==='dark';
  const ts=new Date().toLocaleString();
  doc.setFillColor(dark?11:240,dark?11:240,dark?18:248);doc.rect(0,0,297,210,'F');
  doc.setFillColor(124,111,255);doc.rect(0,0,297,22,'F');
  doc.setTextColor(255,255,255);doc.setFontSize(14);doc.setFont('helvetica','bold');
  doc.text('Jenkins Monitor — KPI Report',14,13);
  doc.setFontSize(8);doc.setFont('helvetica','normal');
  doc.text(`Generated: ${ts}  |  Pipeline: django-pipeline  |  Branch: main`,14,20);
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
    const res = await fetch('/jenkins/api/status');
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
      val.textContent = 'Unreachable';
      val.className   = 'ji-val error';
    }
    console.error('Jenkins status error:', e);
  }
}

// azure connection status
async function checkAzureStatus() {
  try {
    const res = await fetch('/jenkins/azure/api/status');
    const data = await res.json().catch(() => ({ connected: false }));
    const dot = document.getElementById('azureStatusDot');
    const val = document.getElementById('azureStatusVal');

    if (!dot || !val) return;

    if (data.connected) {
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
  window.open('/jenkins/console/' + num, '_blank');
}

// ── Latest Builds Chart (shared)
function renderLatestBuildsChart(builds) {
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

// ── Shared Stat Row
async function getOverviewKpis() {
  const url = document.body.dataset.kpisUrl;
  if (!url) return null;
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error('KPI fetch failed');
    return await res.json();
  } catch (e) {
    console.error('KPI fetch error:', e);
    return null;
  }
}

function updateStatRow(data) {
  const map = {
    'sv-total': data.total_builds,
    'sv-success': data.successful,
    'sv-failed': data.failed,
    'sv-aborted': data.aborted,
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

async function loadStatRow() {
  const data = await getOverviewKpis();
  if (!data) return;
  if (!data.connected) {
    clearStatRow();
    return;
  }
  updateStatRow(data);
}


async function loadLatestBuild() {
  try {
    const res = await fetch('/jenkins/api/latest_build');
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

document.addEventListener('DOMContentLoaded', () => {
  checkStatus();
  checkAzureStatus();
  loadLatestBuild();
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
