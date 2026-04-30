
// LOAD KPIs
let _prevRunningNumbers = new Set();
let _avgDurationMs      = 60000;

async function loadKPIs() {
    try {
        const res = await fetch(document.body.dataset.kpisUrl);
        const d   = await res.json();
        if (!d.connected) { clearDashboard(); return; }

        if (d.avg_duration_ms) _avgDurationMs = d.avg_duration_ms;

        if (typeof updateStatRow === 'function') {
            updateStatRow(d);
        }

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


// ACTIVE BUILDS
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
                       href="/console/${b.number}"
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

//TRIGGER BUILD
function triggerBuild() {
    showConfirm(
        '▶ Start Build',
        'Are you sure you want to trigger a new build for <strong>django-pipeline</strong>?',
        async () => {
            try {
                const { data } = await apiTriggerBuild();
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

function toggleBuild() {
    triggerBuild();
}

// SVG TREND CHART
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
        const consoleUrl = '/console/' + p.build.number;
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
              onclick="window.open('/console/${p.build.number}','_blank')">#${p.build.number}</text>`
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
// Fast stage updater — only updates squares on running rows
async function pollRunningStages() {
  try {
    const data = await (await fetch('/api/running_stages')).json();
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

let _runningStagesHandle = null;

document.addEventListener('DOMContentLoaded', () => {
    requestNotificationPermission();
    checkStatus();
    loadKPIs();
    startPolling(30000);
});
