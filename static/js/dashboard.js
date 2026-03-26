// ─────────────────────────────────────────────
// POLLING
// ─────────────────────────────────────────────
let _polling = null;
function startPolling(ms) {
    if (_polling) clearInterval(_polling);
    _polling = setInterval(() => {
        checkStatus();
        loadKPIs();
    }, ms);
}

// ─────────────────────────────────────────────
// BROWSER NOTIFICATIONS
// ─────────────────────────────────────────────
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

// ─────────────────────────────────────────────
// CONFIRMATION MODAL
// ─────────────────────────────────────────────
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

// ─────────────────────────────────────────────
// 2. LOAD KPIs
// ─────────────────────────────────────────────
let _prevRunningNumbers = new Set();
let _avgDurationMs      = 60000;

async function loadKPIs() {
    try {
        const res = await fetch(document.body.dataset.kpisUrl);
        const d   = await res.json();
        if (!d.connected) { clearDashboard(); return; }

        if (d.avg_duration_ms) _avgDurationMs = d.avg_duration_ms;

        document.getElementById('sv-total').textContent   = d.total_builds ?? '--';
        document.getElementById('sv-success').textContent = d.successful   ?? '--';
        document.getElementById('sv-failed').textContent  = d.failed       ?? '--';
        document.getElementById('sv-aborted').textContent = d.aborted      ?? '--';

        updateCircle('health',       d.health_score ?? 0, 'health-val', 'health-badge');
        updateCircle('success-rate', d.success_rate ?? 0, 'rate-val',   'rate-badge');

        const trend      = d.build_trend || [];
        const nowRunning = new Set(trend.filter(b => b.result === null).map(b => b.number));
        trend.filter(b => b.result !== null && _prevRunningNumbers.has(b.number))
             .forEach(notifyBuildFinished);
        _prevRunningNumbers = nowRunning;

        updateActiveBuilds(d.running ?? 0, trend);

        const finished = trend.filter(b => b.result !== null);
        if (finished.length > 0) {
            renderBarChart(finished);
            renderTrendChart(finished);
        }
    } catch (e) {
        console.error('KPI fetch error:', e);
    }
}

// ─────────────────────────────────────────────
// 3. CIRCULAR PROGRESS
// ─────────────────────────────────────────────
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

// ─────────────────────────────────────────────
// 4. ACTIVE BUILDS
// ─────────────────────────────────────────────
let _activeTimers = {};

function updateActiveBuilds(runningCount, builds) {
    const badge     = document.getElementById('activeCountBadge');
    const container = document.getElementById('activeBuildLines');
    if (badge) badge.textContent = runningCount + ' running';
    if (!container) return;

    const active = builds.filter(b => b.result === null);

    if (active.length === 0) {
        Object.values(_activeTimers).forEach(clearInterval);
        _activeTimers = {};
        container.innerHTML = '<div class="no-builds">No active builds right now</div>';
        return;
    }

    const activeNums = new Set(active.map(b => b.number));
    Object.keys(_activeTimers).forEach(num => {
        if (!activeNums.has(parseInt(num))) {
            clearInterval(_activeTimers[num]);
            delete _activeTimers[num];
        }
    });

    active.forEach(b => {
        if (document.getElementById('bl-' + b.number)) return;

        const avgSec    = Math.round(_avgDurationMs / 1000);
        const elapsedSec = Math.round((Date.now() - b.timestamp) / 1000);
        const pct        = Math.min(95, Math.round((elapsedSec / avgSec) * 100));
        const m          = Math.floor(elapsedSec / 60);
        const s          = elapsedSec % 60;

        const div = document.createElement('div');
        div.className = 'build-line';
        div.id        = 'bl-' + b.number;
        div.innerHTML = `
            <div class="bl-top">
                <div class="bl-id">#${b.number}</div>
                <div class="bl-meta">
                    <div class="bl-duration" id="bl-${b.number}-dur">${m}m ${String(s).padStart(2,'0')}s</div>
                    <button class="bl-abort"
                        onclick="confirmAbort(${b.number})"
                        title="Abort build #${b.number}">
                        <svg viewBox="0 0 24 24">
                            <line x1="18" y1="6" x2="6" y2="18"/>
                            <line x1="6" y1="6" x2="18" y2="18"/>
                        </svg>
                    </button>
                    <a class="bl-console-btn"
                       href="/jenkins/console/${b.number}"
                       target="_blank"
                       title="View Console">
                        <svg viewBox="0 0 24 24">
                            <polyline points="4 17 10 11 4 5"/>
                            <line x1="12" y1="19" x2="20" y2="19"/>
                        </svg>
                    </a>
                </div>
            </div>
            <div class="bl-progress-track">
                <div class="bl-progress-fill" id="bl-${b.number}-fill" style="width:${pct}%"></div>
            </div>
            <div class="bl-footer">
                <span class="bl-stage">Running...</span>
                <span class="bl-pct" id="bl-${b.number}-pct"></span>
            </div>`;

        container.insertBefore(div, container.firstChild);
    });

    container.querySelectorAll('.build-line').forEach(el => {
        const num = parseInt(el.id.replace('bl-', ''));
        if (!activeNums.has(num)) el.remove();
    });

    const noBuilds = container.querySelector('.no-builds');
    if (noBuilds && active.length > 0) noBuilds.remove();

    active.forEach(b => {
        if (_activeTimers[b.number]) return;
        _activeTimers[b.number] = setInterval(() => {
            const elSec  = Math.round((Date.now() - b.timestamp) / 1000);
            const avgSec = Math.round(_avgDurationMs / 1000);
            const pct    = Math.min(95, Math.round((elSec / avgSec) * 100));
            const m      = Math.floor(elSec / 60);
            const s      = elSec % 60;
            const durEl  = document.getElementById('bl-' + b.number + '-dur');
            const fillEl = document.getElementById('bl-' + b.number + '-fill');
            const pctEl  = document.getElementById('bl-' + b.number + '-pct');
            if (durEl)  durEl.textContent  = m + 'm ' + String(s).padStart(2,'0') + 's';
            if (fillEl) fillEl.style.width = pct + '%';
            if (pctEl)  pctEl.textContent  = '';
        }, 1000);
    });
}

// ─────────────────────────────────────────────
// 5. TRIGGER BUILD
// ─────────────────────────────────────────────
function triggerBuild() {
    showConfirm(
        '▶ Start Build',
        'Are you sure you want to trigger a new build for <strong>django-pipeline</strong>?',
        async () => {
            try {
                const res  = await fetch('/jenkins/api/build', { method: 'POST' });
                const data = await res.json();
                if (data.queued) {
                    showToast('✅ Build queued — watch Active Builds');
                    startPolling(5000);
                    setTimeout(() => startPolling(30000), 30000);
                } else {
                    showToast('❌ ' + (data.error || 'Failed to trigger build'), 'abort-toast');
                }
            } catch (e) {
                showToast('❌ Network error', 'abort-toast');
            }
        }
    );
}

// keep same button behavior from your old page
function toggleBuild() {
    triggerBuild();
}

// ─────────────────────────────────────────────
// 6. ABORT BUILD
// ─────────────────────────────────────────────
function confirmAbort(buildNumber) {
    showConfirm(
        '⊘ Abort Build #' + buildNumber,
        'Are you sure you want to abort build <strong>#' + buildNumber + '</strong>?',
        async () => {
            try {
                const res  = await fetch('/jenkins/api/abort/' + buildNumber, { method: 'POST' });
                const data = await res.json();
                if (data.aborted) {
                    showToast('Build #' + buildNumber + ' aborted');
                    const line = document.getElementById('bl-' + buildNumber);
                    if (line) {
                        const fill  = line.querySelector('.bl-progress-fill');
                        const stage = line.querySelector('.bl-stage');
                        if (fill)  { fill.style.background = 'var(--orange)'; fill.style.boxShadow = 'none'; }
                        if (stage)  stage.textContent = 'Aborting...';
                    }
                    if (_activeTimers[buildNumber]) {
                        clearInterval(_activeTimers[buildNumber]);
                        delete _activeTimers[buildNumber];
                    }
                    setTimeout(checkStatus, 2000);
                } else {
                    showToast('Failed to abort: ' + (data.error || 'unknown'), 'abort-toast');
                }
            } catch (e) {
                showToast('Network error during abort', 'abort-toast');
            }
        }
    );
}

// ─────────────────────────────────────────────
// 7. BAR CHART
// ─────────────────────────────────────────────
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
                <a class="btr-console" href="/jenkins/console/${b.number}" target="_blank">
                    <svg viewBox="0 0 24 24">
                        <polyline points="4 17 10 11 4 5"/>
                        <line x1="12" y1="19" x2="20" y2="19"/>
                    </svg>
                    View Console
                </a>
            </div>`;

        return '<div class="bar-col">'
            + richTooltip
            + '<div class="bar ' + cls + '" style="height:' + pct + '%"></div>'
            + '<div class="bar-lbl">#' + b.number + '</div>'
            + '</div>';
    }).join('');
}

// ─────────────────────────────────────────────
// 8. SVG TREND CHART
// ─────────────────────────────────────────────
function renderTrendChart(builds) {
    const sorted = [...builds].reverse();
    const n      = sorted.length;
    if (n === 0) return;
    const X_MIN = 36, X_MAX = 412, Y_TOP = 18, Y_BOT = 138;
    const xStep = n > 1 ? (X_MAX - X_MIN) / (n - 1) : 0;

    const points = sorted.map((b, i) => {
        const val = b.result === 'SUCCESS' ? 1 : 0;
        const x   = n > 1 ? X_MIN + i * xStep : (X_MIN + X_MAX) / 2;
        const y   = Y_BOT - val * (Y_BOT - Y_TOP);
        return { x, y, build: b, val };
    });

    function makePath(pts) {
        if (!pts.length) return '';
        if (pts.length === 1) return 'M' + pts[0].x + ',' + pts[0].y;
        let d = 'M' + pts[0].x + ',' + pts[0].y;
        for (let i = 1; i < pts.length; i++) {
            const prev = pts[i-1], curr = pts[i];
            const cpx  = (prev.x + curr.x) / 2;
            d += ' C' + cpx + ',' + prev.y + ' ' + cpx + ',' + curr.y + ' ' + curr.x + ',' + curr.y;
        }
        return d;
    }

    const linePath = makePath(points);
    const firstPt  = points[0], lastPt = points[points.length - 1];
    document.getElementById('trendSuccessLine').setAttribute('d', linePath);
    document.getElementById('trendSuccessArea').setAttribute('d',
        linePath + ' L' + lastPt.x + ',' + Y_BOT + ' L' + firstPt.x + ',' + Y_BOT + ' Z');
    const failPts = points.map(p => ({ x: p.x, y: Y_BOT - (Y_BOT - p.y) * 0.25 + 8 }));
    document.getElementById('trendFailLine').setAttribute('d', makePath(failPts));
    document.getElementById('trendFailArea').setAttribute('d',
        makePath(failPts) + ' L' + lastPt.x + ',' + Y_BOT + ' L' + firstPt.x + ',' + Y_BOT + ' Z');

    document.getElementById('trendDots').innerHTML = points.map((p, i) => {
        const isLast = i === points.length - 1;
        const color  = p.build.result === 'SUCCESS' ? '#00dba0'
                     : p.build.result === 'FAILURE' ? '#ff4560' : '#ff8c42';
        const consoleUrl = '/jenkins/console/' + p.build.number;
        const r = isLast ? 6 : 4;
        return `<circle cx="${p.x}" cy="${p.y}" r="${r}" fill="${color}"
                    ${isLast ? 'stroke="white" stroke-width="2"' : ''}
                    style="cursor:pointer;"
                    onclick="window.open('${consoleUrl}','_blank')"
                    data-build="${p.build.number}"
                    data-result="${p.build.result || 'RUNNING'}">
                    <title>#${p.build.number} · ${p.build.result || 'RUNNING'} — click to view console</title>
                </circle>`;
    }).join('');

    document.getElementById('trendXLabels').innerHTML = points.map(p =>
        `<text x="${p.x}" y="158" class="axis-label" text-anchor="middle"
              style="cursor:pointer;"
              onclick="window.open('/jenkins/console/${p.build.number}','_blank')">#${p.build.number}</text>`
    ).join('');

    const badge = document.getElementById('trendBadge');
    if (badge) {
        if (n < 2) {
            badge.textContent      = 'Not enough data';
            badge.style.background = 'rgba(170,170,183,.1)';
            badge.style.color      = 'var(--text2)';
            badge.style.border     = '1px solid rgba(170,170,183,.15)';
        } else {
            const recent   = points.slice(-5);
            const previous = points.slice(-10, -5);
            const recentRate   = Math.round(recent.filter(p => p.val).length / recent.length * 100);
            const prevRate     = previous.length > 0
                ? Math.round(previous.filter(p => p.val).length / previous.length * 100)
                : null;

            if (prevRate === null) {
                badge.textContent      = recentRate + '% success rate';
                badge.style.background = recentRate >= 80 ? 'rgba(0,219,160,.1)' : recentRate >= 50 ? 'rgba(58,184,248,.1)' : 'rgba(255,69,96,.1)';
                badge.style.color      = recentRate >= 80 ? 'var(--green)' : recentRate >= 50 ? 'var(--blue)' : 'var(--red)';
                badge.style.border     = '1px solid ' + (recentRate >= 80 ? 'rgba(0,219,160,.2)' : recentRate >= 50 ? 'rgba(58,184,248,.2)' : 'rgba(255,69,96,.2)');
            } else {
                const diff = recentRate - prevRate;
                badge.textContent      = (diff > 0 ? '↑ +' : diff < 0 ? '↓ ' : '→ ') + diff + '% ';
                badge.style.background = diff > 0 ? 'rgba(0,219,160,.1)' : diff < 0 ? 'rgba(255,69,96,.1)' : 'rgba(170,170,183,.1)';
                badge.style.color      = diff > 0 ? 'var(--green)' : diff < 0 ? 'var(--red)' : 'var(--text2)';
                badge.style.border     = '1px solid ' + (diff > 0 ? 'rgba(0,219,160,.2)' : diff < 0 ? 'rgba(255,69,96,.2)' : 'rgba(170,170,183,.15)');
            }
        }
    }
}

// ─────────────────────────────────────────────
// 9. CLEAR DASHBOARD
// ─────────────────────────────────────────────
function clearDashboard() {
    ['sv-total','sv-success','sv-failed','sv-aborted'].forEach(id => {
        const el = document.getElementById(id); if (el) el.textContent = '--';
    });
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

// Fast stage updater — only updates squares on running rows
async function pollRunningStages() {
  try {
    const data = await (await fetch('/jenkins/api/running_stages')).json();
    data.forEach(b => {
      const strip = document.querySelector('#brow-' + b.number + ' .stage-strip');
      if (!strip || !b.stages.length) return;
      strip.innerHTML = b.stages.map(st => {
        const cls  = segCls(st.status);
        const name = (st.name || 'Stage').replace(/"/g, '&quot;');
        return `<span class="seg ${cls}" title="${name}: ${st.status || 'UNKNOWN'}"></span>`;
      }).join('');
    });
  } catch (e) {}
}

document.addEventListener('DOMContentLoaded', () => {
    requestNotificationPermission();
    checkStatus();
    loadKPIs();
    startPolling(30000);
    setInterval(pollRunningStages, 2000);
});
