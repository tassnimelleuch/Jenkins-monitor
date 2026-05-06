
// LOAD KPIs
let _prevRunningNumbers = new Set();
let _avgDurationMs      = 60000;
const LAST_24_HOURS_MS  = 24 * 60 * 60 * 1000;
const OVERVIEW_HISTORY_INITIAL_SHOW = 5;

const _overviewSegTip = document.getElementById('overviewSegTip');
let _overviewHistoryBuilds = [];
let _overviewHistoryShowingAll = false;
let _overviewHistoryTimers = {};

function _isWithinLast24Hours(build, now = Date.now()) {
    const ts = Number(build?.timestamp || 0);
    return ts > 0 && (now - ts) <= LAST_24_HOURS_MS;
}

function fmtDate(ts) {
    if (!ts) return '';
    const date = new Date(ts);
    return date.toLocaleDateString([], { month: 'short', day: 'numeric' }) +
        ' ' +
        date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function showOverviewSegTip(el, name, dur, stcls, sttext) {
    if (!_overviewSegTip) return;

    document.getElementById('overviewTipName').textContent = name;
    document.getElementById('overviewTipDur').textContent = dur || '';
    const statusEl = document.getElementById('overviewTipStatus');
    statusEl.textContent = sttext;
    statusEl.className = 'overview-st-status ' + stcls;
    _overviewSegTip.classList.add('show');

    const rect = el.getBoundingClientRect();
    const tipWidth = _overviewSegTip.offsetWidth || 160;
    let left = rect.left + rect.width / 2 - tipWidth / 2;
    let top = rect.top - (_overviewSegTip.offsetHeight || 80) - 10;

    if (left < 8) left = 8;
    if (left + tipWidth > window.innerWidth - 8) left = window.innerWidth - tipWidth - 8;
    if (top < 8) top = rect.bottom + 8;

    _overviewSegTip.style.left = left + 'px';
    _overviewSegTip.style.top = top + 'px';
}

function hideOverviewSegTip() {
    if (_overviewSegTip) _overviewSegTip.classList.remove('show');
}

function historySegCls(status) {
    if (!status || status === 'IN_PROGRESS') return 'run';
    if (status === 'SUCCESS') return 'ok';
    if (status === 'FAILED' || status === 'FAILURE') return 'fail';
    return 'skip';
}

function historyStageStatusText(status) {
    if (!status || status === 'IN_PROGRESS') return 'In progress';
    if (status === 'SUCCESS') return 'Passed';
    if (status === 'FAILED' || status === 'FAILURE') return 'Failed';
    return status;
}

function historyDotCls(result) {
    return !result ? 'run' : result === 'SUCCESS' ? 'pass' : result === 'FAILURE' ? 'fail' : 'abrt';
}

function historyResultCls(result) {
    return !result ? 'run' : result === 'SUCCESS' ? 'pass' : result === 'FAILURE' ? 'fail' : 'abrt';
}

function historyResultLabel(result) {
    if (!result) return '● Running';
    if (result === 'SUCCESS') return '✓ Success';
    if (result === 'FAILURE') return '✗ Failure';
    return '⊘ ' + result;
}

function buildOverviewStageSegmentsHtml(buildNumber, stages) {
    if (!Array.isArray(stages) || stages.length === 0) {
        return '<span class="no-stage-txt">No stage data</span>';
    }

    return stages.map(stage => {
        const cls = historySegCls(stage.status);
        const tipDur = fmtDur(stage.duration_ms) || '';
        const tipStatus = historyStageStatusText(stage.status);
        const safeName = escapeHtml(stage.name || 'Stage');

        return `<div class="seg ${cls}"
            data-name="${safeName}"
            data-dur="${tipDur}"
            data-stcls="${cls}"
            data-sttext="${tipStatus}"
            onmouseenter="showOverviewSegTip(this,this.dataset.name,this.dataset.dur,this.dataset.stcls,this.dataset.sttext)"
            onmouseleave="hideOverviewSegTip()"
            onclick="event.stopPropagation();openConsole(${buildNumber})"></div>`;
    }).join('');
}

function buildOverviewHistoryRowHtml(build) {
    const isRunning = build.result === null;
    const stages = Array.isArray(build.stages) ? build.stages : [];
    const elapsedSeconds = isRunning ? Math.round((Date.now() - build.timestamp) / 1000) : 0;
    const avgSeconds = Math.max(1, Math.round(_avgDurationMs / 1000));
    const pct = isRunning ? Math.min(95, Math.round((elapsedSeconds / avgSeconds) * 100)) : 0;
    const minutes = Math.floor(elapsedSeconds / 60);
    const seconds = elapsedSeconds % 60;
    const durText = isRunning ? `${minutes}m ${String(seconds).padStart(2, '0')}s` : '';
    const resultCell = isRunning
        ? `<div>
             <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
               <span class="br-result run">${historyResultLabel(build.result)}</span>
               <button class="br-abort" onclick="event.stopPropagation();confirmAbort(${build.number})" title="Abort build #${build.number}">
                 <svg viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
               </button>
             </div>
             <div style="font-size:9.5px;font-family:'JetBrains Mono',monospace;color:var(--text2);margin-top:4px;" id="brdur-${build.number}">${durText}</div>
             <div class="br-console">↗ console</div>
           </div>`
        : `<div>
             <span class="br-result ${historyResultCls(build.result)}">${historyResultLabel(build.result)}</span>
             <div style="font-size:9.5px;font-family:'JetBrains Mono',monospace;color:var(--text2);margin-top:4px;"></div>
             <div class="br-console">↗ console</div>
           </div>`;

    return `
        <div class="build-row ${isRunning ? 'is-running' : ''}" id="brow-${build.number}" onclick="openConsole(${build.number})">
            <div>
                <div class="br-num">#${build.number}</div>
                <div class="br-date">${fmtDate(build.timestamp)}</div>
            </div>
            <div class="br-dot ${historyDotCls(build.result)}"></div>
            <div class="stage-strip">${buildOverviewStageSegmentsHtml(build.number, stages)}</div>
            ${resultCell}
            ${isRunning ? `<div class="run-bar"><div class="run-bar-fill" id="rb-${build.number}" style="width:${pct}%"></div></div>` : ''}
        </div>`;
}

function startOverviewHistoryTimers(runningBuilds) {
    const runningNumbers = new Set(runningBuilds.map(build => build.number));

    Object.keys(_overviewHistoryTimers).forEach(number => {
        if (!runningNumbers.has(parseInt(number, 10))) {
            clearInterval(_overviewHistoryTimers[number]);
            delete _overviewHistoryTimers[number];
        }
    });

    runningBuilds.forEach(build => {
        if (_overviewHistoryTimers[build.number]) return;

        _overviewHistoryTimers[build.number] = setInterval(() => {
            const elapsedSeconds = Math.round((Date.now() - build.timestamp) / 1000);
            const avgSeconds = Math.max(1, Math.round(_avgDurationMs / 1000));
            const pct = Math.min(95, Math.round((elapsedSeconds / avgSeconds) * 100));
            const minutes = Math.floor(elapsedSeconds / 60);
            const seconds = elapsedSeconds % 60;

            const durationEl = document.getElementById('brdur-' + build.number);
            const progressEl = document.getElementById('rb-' + build.number);

            if (durationEl) durationEl.textContent = `${minutes}m ${String(seconds).padStart(2, '0')}s`;
            if (progressEl) progressEl.style.width = pct + '%';
        }, 1000);
    });
}

function clearOverviewHistory() {
    Object.values(_overviewHistoryTimers).forEach(clearInterval);
    _overviewHistoryTimers = {};
    _overviewHistoryBuilds = [];
    _overviewHistoryShowingAll = false;
    if (_runningStagesHandle) {
        clearInterval(_runningStagesHandle);
        _runningStagesHandle = null;
    }

    const container = document.getElementById('overviewBuildTimeline');
    if (container) {
        container.innerHTML = '<div class="overview-tl-empty">No builds in the last 24 hours.</div>';
    }

    const badge = document.getElementById('overviewRunningBadge');
    if (badge) {
        badge.style.display = 'none';
        badge.textContent = '';
    }

    const button = document.getElementById('overviewShowMoreBtn');
    if (button) button.style.display = 'none';
}

function renderOverviewHistory() {
    const container = document.getElementById('overviewBuildTimeline');
    const badge = document.getElementById('overviewRunningBadge');
    const button = document.getElementById('overviewShowMoreBtn');
    if (!container) return;

    const running = _overviewHistoryBuilds.filter(build => build.result === null);
    const finished = _overviewHistoryBuilds.filter(build => build.result !== null);
    const finishedToShow = _overviewHistoryShowingAll
        ? finished
        : finished.slice(0, OVERVIEW_HISTORY_INITIAL_SHOW);
    const buildsToRender = [...running, ...finishedToShow];

    container.innerHTML = buildsToRender.length
        ? buildsToRender.map(buildOverviewHistoryRowHtml).join('')
        : '<div class="overview-tl-empty">No builds in the last 24 hours.</div>';

    if (badge) {
        badge.style.display = running.length ? 'inline-flex' : 'none';
        badge.textContent = running.length ? `● ${running.length} running` : '';
    }

    if (button) {
        if (finished.length > OVERVIEW_HISTORY_INITIAL_SHOW) {
            button.style.display = 'block';
            button.textContent = _overviewHistoryShowingAll
                ? 'Show less ↑'
                : `Show more ↓  (${finished.length - OVERVIEW_HISTORY_INITIAL_SHOW} more)`;
        } else {
            button.style.display = 'none';
        }
    }

    startOverviewHistoryTimers(running);
}

function toggleOverviewHistoryShowMore() {
    _overviewHistoryShowingAll = !_overviewHistoryShowingAll;
    renderOverviewHistory();
}

function clearOverviewHistoryCharts() {
    const wrap = document.getElementById('barsWrap');
    if (wrap) {
        wrap.innerHTML = '<div class="no-builds" style="width:100%;text-align:center;">No finished builds in the last 24 hours</div>';
    }

    const sumRow = document.getElementById('buildSummaryRow');
    if (sumRow) sumRow.innerHTML = '';

    const avg = document.getElementById('latestBuildsAvg');
    if (avg) avg.textContent = 'Avg —';

    ['trendSuccessArea', 'trendFailArea', 'trendSuccessLine', 'trendFailLine'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.setAttribute('d', '');
    });

    ['trendDots', 'trendXLabels'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.innerHTML = '';
    });

    const badge = document.getElementById('trendBadge');
    if (badge) {
        badge.textContent = 'No builds in last 24h';
        badge.style.background = 'rgba(170,170,183,.1)';
        badge.style.color = 'var(--text2)';
        badge.style.border = '1px solid rgba(170,170,183,.15)';
    }
}

async function loadKPIs() {
    try {
        const res = await fetch(document.body.dataset.kpisUrl);
        const d   = await res.json();
        if (!d.connected) {
            clearDashboard();
            clearOverviewHistory();
            return;
        }

        if (d.avg_duration_ms) _avgDurationMs = d.avg_duration_ms;

        if (typeof updateStatRow === 'function') {
            updateStatRow(d);
        }

        updateCircle('health',       d.health_score ?? 0, 'health-val', 'health-badge');
        updateCircle('success-rate', d.success_rate ?? 0, 'rate-val',   'rate-badge');

        const trend = (d.build_trend || []).map(build => ({
            ...build,
            duration: build.duration ?? build.duration_ms ?? ((build.duration_seconds ?? 0) * 1000),
            stages: Array.isArray(build.stages) ? build.stages : [],
        }));
        const nowRunning = new Set(trend.filter(b => b.result === null).map(b => b.number));
        trend.filter(b => b.result !== null && _prevRunningNumbers.has(b.number))
             .forEach(notifyBuildFinished);
        _prevRunningNumbers = nowRunning;

        const hasRunning = trend.some(build => build.result === null);
        if (hasRunning && !_runningStagesHandle) {
            _runningStagesHandle = setInterval(pollRunningStages, 2000);
            pollRunningStages();
        } else if (!hasRunning && _runningStagesHandle) {
            clearInterval(_runningStagesHandle);
            _runningStagesHandle = null;
        }

        updateActiveBuilds(d.running ?? 0, trend);

        const now = Date.now();
        const historyLast24h = trend
            .filter(build => _isWithinLast24Hours(build, now))
            .sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0) || (b.number || 0) - (a.number || 0));
        const finishedLast24h = trend.filter(
            b => b.result !== null && _isWithinLast24Hours(b, now)
        );
        if (finishedLast24h.length > 0) {
            renderBarChart(finishedLast24h);
            renderTrendChart(finishedLast24h);
        } else {
            clearOverviewHistoryCharts();
        }

        _overviewHistoryBuilds = historyLast24h;
        renderOverviewHistory();
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
    triggerBuildWithConfirmation({
        bodyHtml: `Are you sure you want to trigger a new build for ${pipelineStrongLabel()} on <strong>${escapeHtml(getBranchName())}</strong>?`,
        queuedMessage: '✅ Build queued — watch Active Builds',
        triggerErrorMessage: 'Failed to trigger build',
        onQueued() {
            startPolling(5000);
            setTimeout(() => startPolling(30000), 30000);
        }
    });
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
      if (!strip || !Array.isArray(b.stages) || !b.stages.length) return;
      strip.innerHTML = buildOverviewStageSegmentsHtml(b.number, b.stages);
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
