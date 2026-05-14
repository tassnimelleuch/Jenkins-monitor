
// LOAD KPIs
let _prevRunningNumbers = new Set();
let _avgDurationMs      = 60000;
const LAST_24_HOURS_MS  = 24 * 60 * 60 * 1000;
const OVERVIEW_HISTORY_INITIAL_SHOW = 5;

const _overviewSegTip = document.getElementById('overviewSegTip');
let _overviewHistoryBuilds = [];
let _overviewHistoryShowingAll = false;
let _overviewHistoryTimers = {};
let _testsDuration24hChart = null;
let _overviewLoadInFlight = false;
let _runningStagesPollInFlight = false;
let _overviewStatsSignature = null;
let _overviewActiveSignature = null;
let _overviewFinishedSignature = null;
let _overviewHistorySignature = null;
let _overviewTestsDurationSignature = null;
let _lastOverviewMetrics = null;

function _isWithinLast24Hours(build, now = Date.now()) {
    const ts = Number(build?.timestamp || 0);
    return ts > 0 && (now - ts) <= LAST_24_HOURS_MS;
}

function _signaturePart(value) {
    return value ?? '';
}

function _buildStagesSignature(stages, { includeDurations = true } = {}) {
    return (Array.isArray(stages) ? stages : [])
        .map(stage => [
            _signaturePart(stage?.name),
            _signaturePart(stage?.status),
            includeDurations ? _signaturePart(stage?.duration_ms) : '',
        ].join(':'))
        .join(',');
}

function _buildListSignature(builds, { includeStages = false, includeDuration = true, includeStageDurations = true } = {}) {
    return (Array.isArray(builds) ? builds : [])
        .map(build => [
            _signaturePart(build?.number),
            _signaturePart(build?.result),
            _signaturePart(build?.timestamp),
            includeDuration ? _signaturePart(build?.duration) : '',
            includeStages ? _buildStagesSignature(build?.stages, { includeDurations: includeStageDurations }) : '',
        ].join('|'))
        .join('||');
}

function _buildStageStripSignature(stages) {
    return _buildStagesSignature(stages, { includeDurations: false });
}

function _buildTestsDurationSignature(points) {
    const theme = document.documentElement.getAttribute('data-theme') || 'dark';
    return theme + '::' + (Array.isArray(points) ? points : [])
        .map(point => [
            _signaturePart(point?.number),
            _signaturePart(point?.timestamp),
            _signaturePart(point?.total_duration_ms),
            _signaturePart(point?.unit_tests_ms),
            _signaturePart(point?.pylint_ms),
            _signaturePart(point?.sonarcloud_ms),
        ].join('|'))
        .join('||');
}

function _cacheOverviewMetrics(payload) {
    const previous = _lastOverviewMetrics || {};
    const metrics = {
        total_builds: payload?.total_builds ?? previous.total_builds,
        successful: payload?.successful ?? previous.successful,
        failed: payload?.failed ?? previous.failed,
        aborted: payload?.aborted ?? previous.aborted,
        running: payload?.running ?? previous.running ?? 0,
        success_rate: payload?.success_rate ?? previous.success_rate ?? 0,
        health_score: payload?.health_score ?? previous.health_score ?? 0,
        avg_duration_ms: payload?.avg_duration_ms ?? previous.avg_duration_ms ?? _avgDurationMs,
    };

    if (!(metrics.avg_duration_ms > 0)) {
        metrics.avg_duration_ms = previous.avg_duration_ms ?? _avgDurationMs;
    }

    _lastOverviewMetrics = metrics;
    return metrics;
}

function resetOverviewRenderCache() {
    _overviewStatsSignature = null;
    _overviewActiveSignature = null;
    _overviewFinishedSignature = null;
    _overviewHistorySignature = null;
    _overviewTestsDurationSignature = null;
    _lastOverviewMetrics = null;
}

function fmtDate(ts) {
    if (!ts) return '';
    const date = new Date(ts);
    return date.toLocaleDateString([], { month: 'short', day: 'numeric' }) +
        ' ' +
        date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function currentUserCanManageBuilds() {
    return document.body.dataset.canManageBuilds === 'true';
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

function updateOverviewStageSegmentDurations(strip, stages) {
    const segments = strip.querySelectorAll('.seg');
    if (segments.length !== stages.length) return false;

    stages.forEach((stage, index) => {
        const segment = segments[index];
        if (!segment) return;
        segment.dataset.dur = fmtDur(stage.duration_ms) || '';
    });

    return true;
}

function buildOverviewHistoryRowHtml(build) {
    const isRunning = build.result === null;
    const stages = Array.isArray(build.stages) ? build.stages : [];
    const stageSignature = escapeHtml(_buildStageStripSignature(stages));
    const elapsedSeconds = isRunning ? Math.round((Date.now() - build.timestamp) / 1000) : 0;
    const avgSeconds = Math.max(1, Math.round(_avgDurationMs / 1000));
    const pct = isRunning ? Math.min(95, Math.round((elapsedSeconds / avgSeconds) * 100)) : 0;
    const minutes = Math.floor(elapsedSeconds / 60);
    const seconds = elapsedSeconds % 60;
    const durText = isRunning ? `${minutes}m ${String(seconds).padStart(2, '0')}s` : '';
    const abortButton = currentUserCanManageBuilds()
        ? `<button class="br-abort" onclick="event.stopPropagation();confirmAbort(${build.number})" title="Abort build #${build.number}">
             <svg viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
           </button>`
        : '';
    const resultCell = isRunning
        ? `<div>
             <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
               <span class="br-result run">${historyResultLabel(build.result)}</span>
               ${abortButton}
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
            <div class="stage-strip" data-stage-signature="${stageSignature}">${buildOverviewStageSegmentsHtml(build.number, stages)}</div>
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

function clearOverviewTestsDuration24hChart(message = 'No tests duration data available in the last 24 hours') {
    const canvas = document.getElementById('testsDuration24hChart');
    const badge = document.getElementById('testsDuration24hBadge');
    const summary = document.getElementById('testsDuration24hSummary');
    if (!canvas) return;

    if (_testsDuration24hChart) {
        _testsDuration24hChart.destroy();
        _testsDuration24hChart = null;
    }

    canvas.style.display = 'none';
    const container = canvas.parentElement;
    if (!container.querySelector('.chart-empty')) {
        const empty = document.createElement('div');
        empty.className = 'chart-empty';
        empty.textContent = message;
        container.appendChild(empty);
    }

    if (summary) summary.innerHTML = '';
    if (badge) badge.textContent = '24h Avg —';
}

function renderOverviewTestsDuration24hChart(testsDurationTrend) {
    const canvas = document.getElementById('testsDuration24hChart');
    if (!canvas || typeof Chart === 'undefined') return;

    const container = canvas.parentElement;
    const badge = document.getElementById('testsDuration24hBadge');
    const summary = document.getElementById('testsDuration24hSummary');
    const cutoffMs = Date.now() - LAST_24_HOURS_MS;
    const points = (Array.isArray(testsDurationTrend) ? testsDurationTrend : [])
        .filter(point =>
            typeof point.total_duration_ms === 'number' &&
            point.total_duration_ms > 0 &&
            (point.timestamp || 0) >= cutoffMs
        )
        .sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));

    if (!points.length) {
        clearOverviewTestsDuration24hChart();
        return;
    }

    canvas.style.display = 'block';
    const existingEmpty = container.querySelector('.chart-empty');
    if (existingEmpty) existingEmpty.remove();

    const avgDurationMs = Math.round(
        points.reduce((sum, point) => sum + point.total_duration_ms, 0) / points.length
    );
    const latestPoint = points[points.length - 1];
    const maxDurationMs = Math.max(...points.map(point => point.total_duration_ms));

    if (summary) {
        summary.innerHTML =
            `<span class="overview-tests-duration-pill"><strong>${fmtDur(latestPoint.total_duration_ms)}</strong> latest build</span>` +
            `<span class="overview-tests-duration-pill"><strong>${fmtDur(maxDurationMs)}</strong> peak duration</span>`;
    }

    if (badge) {
        badge.textContent = `24h Avg ${fmtDur(avgDurationMs)}`;
    }

    const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
    const gridColor = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)';
    const textColor = isDark ? '#9c9a92' : '#73726c';
    const titleColor = isDark ? '#c2c0b6' : '#3d3d3a';

    if (_testsDuration24hChart) {
        _testsDuration24hChart.destroy();
        _testsDuration24hChart = null;
    }

    _testsDuration24hChart = new Chart(canvas, {
        type: 'bar',
        data: {
            labels: points.map(point => `#${point.number}`),
            datasets: [{
                label: 'Tests duration',
                data: points.map(point => point.total_duration_ms),
                backgroundColor: 'rgba(58,184,248,0.72)',
                borderColor: '#3ab8f8',
                borderWidth: 1,
                borderRadius: 6,
                borderSkipped: false,
                hoverBackgroundColor: 'rgba(58,184,248,0.88)',
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        title(items) {
                            const point = points[items[0].dataIndex];
                            return `Build #${point.number}`;
                        },
                        label(ctx) {
                            return ` Total tests duration: ${fmtDur(ctx.raw)}`;
                        },
                        afterLabel(ctx) {
                            const point = points[ctx.dataIndex];
                            return [
                                ` Unit tests: ${fmtDur(point.unit_tests_ms || 0)}`,
                                ` Pylint: ${fmtDur(point.pylint_ms || 0)}`,
                                ` SonarCloud: ${fmtDur(point.sonarcloud_ms || 0)}`,
                            ];
                        }
                    },
                    backgroundColor: isDark ? '#2c2c2a' : '#fff',
                    titleColor,
                    bodyColor: textColor,
                    borderColor: isDark ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.08)',
                    borderWidth: 0.5,
                    padding: 10,
                    cornerRadius: 8
                }
            },
            scales: {
                x: {
                    grid: { display: false },
                    border: { display: false },
                    ticks: { color: textColor, font: { size: 10 }, maxTicksLimit: 8 }
                },
                y: {
                    min: 0,
                    grid: { color: gridColor, drawTicks: false },
                    border: { display: false },
                    ticks: {
                        color: textColor,
                        font: { size: 10 },
                        callback: value => fmtDur(Number(value)),
                        maxTicksLimit: 6,
                    }
                }
            },
            animation: { duration: 600, easing: 'easeOutQuart' }
        }
    });
}

function hasOverviewLatestBuildsChart() {
    return Boolean(document.getElementById('barsWrap'));
}

function hasOverviewTrendChart() {
    return Boolean(
        document.getElementById('trendSuccessLine') &&
        document.getElementById('trendFailLine') &&
        document.getElementById('trendDots') &&
        document.getElementById('trendXLabels')
    );
}

function hasOverviewHistoryTimeline() {
    return Boolean(document.getElementById('overviewBuildTimeline'));
}

async function loadKPIs() {
    if (_overviewLoadInFlight) return;
    _overviewLoadInFlight = true;

    try {
        const res = await fetch(document.body.dataset.kpisUrl);
        const d   = await res.json();
        // Only clear on connection failure; preserve visible data during polling
        if (!d.connected) {
            resetOverviewRenderCache();
            _prevRunningNumbers = new Set();
            Object.values(_activeTimers).forEach(clearInterval);
            _activeTimers = {};
            clearDashboard();
            clearOverviewHistory();
            clearOverviewTestsDuration24hChart('No tests duration data available');
            return;
        }

        const trend = (d.build_trend || []).map(build => ({
            ...build,
            duration: build.duration ?? build.duration_ms ?? ((build.duration_seconds ?? 0) * 1000),
            stages: Array.isArray(build.stages) ? build.stages : [],
        }));
        const metrics = _cacheOverviewMetrics(d);

        if (metrics.avg_duration_ms > 0) _avgDurationMs = metrics.avg_duration_ms;

        const statsSignature = JSON.stringify({
            total_builds: metrics.total_builds,
            successful: metrics.successful,
            failed: metrics.failed,
            aborted: metrics.aborted,
            running: metrics.running,
            success_rate: metrics.success_rate,
            health_score: metrics.health_score,
        });
        if (_overviewStatsSignature !== statsSignature) {
            if (typeof updateStatRow === 'function') {
                updateStatRow(metrics);
            }

            updateCircle('health',       metrics.health_score, 'health-val', 'health-badge');
            updateCircle('success-rate', metrics.success_rate, 'rate-val',   'rate-badge');
            _overviewStatsSignature = statsSignature;
        }

        const nowRunning = new Set(trend.filter(b => b.result === null).map(b => b.number));
        trend.filter(b => b.result !== null && _prevRunningNumbers.has(b.number))
             .forEach(notifyBuildFinished);
        _prevRunningNumbers = nowRunning;

        const hasRunning = trend.some(build => build.result === null);
        if (hasRunning && !_runningStagesHandle) {
            if (hasOverviewHistoryTimeline()) {
                _runningStagesHandle = setInterval(pollRunningStages, 2000);
                pollRunningStages();
            }
        } else if (!hasRunning && _runningStagesHandle) {
            clearInterval(_runningStagesHandle);
            _runningStagesHandle = null;
        }

        const activeBuilds = trend.filter(build => build.result === null);
        const activeSignature = _buildListSignature(activeBuilds, { includeDuration: false });
        if (_overviewActiveSignature !== activeSignature || metrics.running !== activeBuilds.length) {
            updateActiveBuilds(activeBuilds.length, trend);
            _overviewActiveSignature = activeSignature;
        }

        const now = Date.now();
        const historyLast24h = trend
            .filter(build => _isWithinLast24Hours(build, now))
            .sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0) || (b.number || 0) - (a.number || 0));
        const finishedLast24h = trend.filter(
            b => b.result !== null && _isWithinLast24Hours(b, now)
        );
        const finishedSignature = _buildListSignature(finishedLast24h);
        if (finishedLast24h.length > 0) {
            if (_overviewFinishedSignature !== finishedSignature) {
                if (hasOverviewLatestBuildsChart()) renderBarChart(finishedLast24h);
                if (hasOverviewTrendChart()) renderTrendChart(finishedLast24h);
                _overviewFinishedSignature = finishedSignature;
            }
        } else {
            if (_overviewFinishedSignature !== '__empty__') {
                clearOverviewHistoryCharts();
                _overviewFinishedSignature = '__empty__';
            }
        }

        const testsDurationSignature = _buildTestsDurationSignature(d.tests_duration);
        if (Array.isArray(d.tests_duration)) {
            if (_overviewTestsDurationSignature !== testsDurationSignature) {
                renderOverviewTestsDuration24hChart(d.tests_duration);
                _overviewTestsDurationSignature = testsDurationSignature;
            }
        } else {
            if (_overviewTestsDurationSignature !== '__empty__') {
                clearOverviewTestsDuration24hChart();
                _overviewTestsDurationSignature = '__empty__';
            }
        }

        if (hasOverviewHistoryTimeline()) {
            const historySignature = _buildListSignature(historyLast24h, {
                includeStages: true,
                includeDuration: false,
                includeStageDurations: false,
            });
            if (_overviewHistorySignature !== historySignature) {
                _overviewHistoryBuilds = historyLast24h;
                renderOverviewHistory();
                _overviewHistorySignature = historySignature;
            }
        } else {
            if (_overviewHistorySignature !== '__empty__') {
                clearOverviewHistory();
                _overviewHistorySignature = '__empty__';
            }
        }
    } catch (e) {
        console.error('KPI fetch error:', e);
    } finally {
        _overviewLoadInFlight = false;
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

        const abortButton = currentUserCanManageBuilds()
            ? `<button class="bl-abort"
                    onclick="confirmAbort(${b.number})"
                    title="Abort build #${b.number}">
                    <svg viewBox="0 0 24 24">
                        <line x1="18" y1="6" x2="6" y2="18"/>
                        <line x1="6" y1="6" x2="18" y2="18"/>
                    </svg>
                </button>`
            : '';

        const div = document.createElement('div');
        div.className = 'build-line';
        div.id        = 'bl-' + b.number;
        div.innerHTML = `
            <div class="bl-top">
                <div class="bl-id">#${b.number}</div>
                <div class="bl-meta">
                    <div class="bl-duration" id="bl-${b.number}-dur">${m}m ${String(s).padStart(2,'0')}s</div>
                    ${abortButton}
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
            setTimeout(() => startPolling(10000), 10000);
        }
    });
}

function toggleBuild() {
    triggerBuild();
}

// SVG TREND CHART
function renderTrendChart(builds) {
    if (!hasOverviewTrendChart()) return;
    const svg = document.getElementById('trendChartSvg');
    const chartArea = svg?.closest('.chart-area');
    const sorted = [...builds].reverse();
    const n      = sorted.length;
    if (n === 0) return;
    const width  = Math.max(
        Math.round(chartArea?.clientWidth || svg?.clientWidth || 430),
        430,
    );
    const height = Math.max(
        Math.round(chartArea?.clientHeight || svg?.clientHeight || 170),
        170,
    );
    const X_MIN = 36;
    const X_MAX = Math.max(X_MIN + 120, width - 36);
    const Y_TOP = 18;
    const Y_BOT = Math.max(Y_TOP + 48, height - 32);
    const X_LABEL_Y = Math.min(height - 10, Y_BOT + 20);
    const xStep = n > 1 ? (X_MAX - X_MIN) / (n - 1) : 0;

    if (svg) {
        svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
        const gridLines = svg.querySelectorAll('.grid-line');
        const gridYs = [Y_BOT, Y_TOP + ((Y_BOT - Y_TOP) / 2), Y_TOP];
        gridLines.forEach((line, index) => {
            const y = gridYs[index] ?? Y_BOT;
            line.setAttribute('x1', X_MIN);
            line.setAttribute('x2', X_MAX);
            line.setAttribute('y1', y);
            line.setAttribute('y2', y);
        });
    }

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
        `<text x="${p.x}" y="${X_LABEL_Y}" class="axis-label" text-anchor="middle"
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
  if (_runningStagesPollInFlight) return;
  _runningStagesPollInFlight = true;

  try {
    const data = await (await fetch('/api/running_stages')).json();
    data.forEach(b => {
      const strip = document.querySelector('#brow-' + b.number + ' .stage-strip');
      if (!strip || !Array.isArray(b.stages) || !b.stages.length) return;
      const nextSignature = _buildStageStripSignature(b.stages);

      if (strip.dataset.stageSignature !== nextSignature) {
        const nextHtml = buildOverviewStageSegmentsHtml(b.number, b.stages);
        strip.innerHTML = nextHtml;
        strip.dataset.stageSignature = nextSignature;
      } else {
        updateOverviewStageSegmentDurations(strip, b.stages);
      }
    });
  } catch (e) {
  } finally {
    _runningStagesPollInFlight = false;
  }
}

let _runningStagesHandle = null;

document.addEventListener('DOMContentLoaded', () => {
    requestNotificationPermission();
    checkStatus();
    loadKPIs();
    startPolling(2000);
});
