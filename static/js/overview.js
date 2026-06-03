
// LOAD KPIs
let _prevRunningNumbers = new Set();
let _avgDurationMs      = 60000;
const LAST_24_HOURS_MS  = 24 * 60 * 60 * 1000;
const OVERVIEW_HISTORY_INITIAL_SHOW = 5;
const LIVE_RUNNING_WATCH_POLL_MS = 2000;
const LIVE_RUNNING_POLL_MS = 2000;
const LIVE_RUNNING_IDLE_CONFIRM_POLLS = 2;
const KPI_COMPLETION_BURST_MS = 8000;
const KPI_COMPLETION_BURST_INTERVAL_MS = 1000;
const OPTIMISTIC_ABORT_WINDOW_MS = 30000;

const _overviewSegTip = document.getElementById('overviewSegTip');
let _overviewHistoryBuilds = [];
let _overviewHistoryShowingAll = false;
let _overviewHistoryTimers = {};
let _testsDuration24hChart = null;
let _overviewLoadInFlight = false;
let _overviewKpiBurstHandle = null;
let _overviewKpiBurstStopAt = 0;
let _runningBuildsWatcherInFlight = false;
let _runningBuildsWatcherHandle = null;
let _runningStagesPollInFlight = false;
let _runningStagesHandle = null;
let _liveRunningEmptyPollStreak = 0;
let _overviewStatsSignature = null;
let _overviewActiveSignature = null;
let _overviewFinishedSignature = null;
let _overviewHistorySignature = null;
let _overviewTestsDurationSignature = null;
let _lastOverviewMetrics = null;
let _lastOverviewPayload = null;
let _liveRunningBuilds = [];
let _hasLiveRunningPollData = false;
let _optimisticallyAbortedBuilds = new Map();
let _overviewLiveStream = null;
let _overviewLiveStreamReceived = false;
let _overviewLiveStreamFallbackStarted = false;
let _overviewLiveStreamLoggedError = false;
let _overviewLiveStreamFallbackHandle = null;
let _lastOverviewSsePayloadAt = 0;
let _overviewFinishedBuildFallbackHandle = null;

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

function _buildTestsDurationSignature(builds, points) {
    const theme = document.documentElement.getAttribute('data-theme') || 'dark';
    const buildSignature = (Array.isArray(builds) ? builds : [])
        .filter(build => build?.result !== null && _isWithinLast24Hours(build))
        .map(build => [
            _signaturePart(build?.number),
            _signaturePart(build?.timestamp),
            _signaturePart(build?.result),
        ].join('|'))
        .join('||');
    const durationSignature = (Array.isArray(points) ? points : [])
        .filter(point => point?.result !== null)
        .map(point => [
            _signaturePart(point?.number),
            _signaturePart(point?.timestamp),
            _signaturePart(point?.total_duration_ms),
            _signaturePart(point?.unit_tests_ms),
            _signaturePart(point?.pylint_ms),
            _signaturePart(point?.sonarcloud_ms),
        ].join('|'))
        .join('||');
    return [theme, buildSignature, durationSignature].join('::');
}

function _cacheOverviewMetrics(payload) {
    const previous = _lastOverviewMetrics || {};
    const metrics = {
        last_build_number: payload?.last_build_number ?? previous.last_build_number,
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

function stopOverviewKpiBurstRefresh() {
    if (_overviewKpiBurstHandle) {
        clearInterval(_overviewKpiBurstHandle);
        _overviewKpiBurstHandle = null;
    }
    _overviewKpiBurstStopAt = 0;
}

function clearOverviewFinishedBuildFallback() {
    if (_overviewFinishedBuildFallbackHandle) {
        clearTimeout(_overviewFinishedBuildFallbackHandle);
        _overviewFinishedBuildFallbackHandle = null;
    }
}

function scheduleOverviewFinishedBuildFallback(expectedPayloadAfter, delayMs = 1200) {
    clearOverviewFinishedBuildFallback();
    _overviewFinishedBuildFallbackHandle = window.setTimeout(() => {
        _overviewFinishedBuildFallbackHandle = null;
        if (_lastOverviewSsePayloadAt >= expectedPayloadAfter) {
            return;
        }
        loadKPIs({ refresh: true, wait: true });
    }, delayMs);
}

function loadImmediateOverviewKPIs() {
    return loadKPIs();
}

function scheduleOverviewKpiBurstRefresh({
    durationMs = KPI_COMPLETION_BURST_MS,
    intervalMs = KPI_COMPLETION_BURST_INTERVAL_MS,
    immediate = true,
} = {}) {
    const stopAt = Date.now() + durationMs;
    _overviewKpiBurstStopAt = Math.max(_overviewKpiBurstStopAt, stopAt);

    if (immediate) {
        loadImmediateOverviewKPIs();
    }

    if (_overviewKpiBurstHandle) return;

    _overviewKpiBurstHandle = setInterval(() => {
        if (Date.now() >= _overviewKpiBurstStopAt) {
            stopOverviewKpiBurstRefresh();
            return;
        }
        loadKPIs();
    }, intervalMs);
}

function stopRunningBuildsWatcher() {
    if (_runningBuildsWatcherHandle) {
        clearInterval(_runningBuildsWatcherHandle);
        _runningBuildsWatcherHandle = null;
    }
    _runningBuildsWatcherInFlight = false;
}

function stopLiveRunningPoll({ restartWatcher = true } = {}) {
    if (_runningStagesHandle) {
        clearInterval(_runningStagesHandle);
        _runningStagesHandle = null;
    }
    _runningStagesPollInFlight = false;
    _liveRunningEmptyPollStreak = 0;
    if (restartWatcher) {
        startRunningBuildsWatcher({ eager: false });
    }
}

function startLiveRunningPoll({ eager = true } = {}) {
    void eager;
    stopRunningBuildsWatcher();
}

function startRunningBuildsWatcher({ eager = true } = {}) {
    void eager;
    stopRunningBuildsWatcher();
}

function hasOverviewLiveBuildWidgets() {
    return Boolean(
        document.getElementById('activeBuildLines') ||
        document.getElementById('overviewBuildTimeline')
    );
}

function pruneOptimisticAbortedBuilds(now = Date.now()) {
    _optimisticallyAbortedBuilds.forEach((entry, buildNumber) => {
        if (!entry || entry.expiresAt <= now) {
            _optimisticallyAbortedBuilds.delete(buildNumber);
        }
    });
}

function normalizeOverviewBuild(build) {
    if (!build) return null;
    return {
        ...build,
        duration: build.duration ?? build.duration_ms ?? ((build.duration_seconds ?? 0) * 1000),
        stages: Array.isArray(build.stages) ? build.stages : [],
    };
}

function findCurrentOverviewBuild(buildNumber) {
    const liveBuild = (_liveRunningBuilds || []).find(build => build.number === buildNumber);
    if (liveBuild) return normalizeOverviewBuild(liveBuild);

    const payloadBuild = (_lastOverviewPayload?.build_trend || []).find(build => build?.number === buildNumber);
    if (payloadBuild) return normalizeOverviewBuild(payloadBuild);

    return null;
}

function markBuildOptimisticallyAborted(buildNumber, buildSnapshot = null) {
    const normalizedSnapshot = normalizeOverviewBuild(buildSnapshot) || normalizeOverviewBuild(findCurrentOverviewBuild(buildNumber)) || {
        number: buildNumber,
        result: 'ABORTED',
        duration: 0,
        duration_ms: 0,
        duration_seconds: 0,
        timestamp: 0,
        stages: [],
    };

    _optimisticallyAbortedBuilds.set(
        buildNumber,
        {
            expiresAt: Date.now() + OPTIMISTIC_ABORT_WINDOW_MS,
            build: {
                ...normalizedSnapshot,
                result: 'ABORTED',
            },
        }
    );
}

function clearOptimisticBuildAbort(buildNumber) {
    _optimisticallyAbortedBuilds.delete(buildNumber);
}

function isBuildOptimisticallyAborted(buildNumber) {
    pruneOptimisticAbortedBuilds();
    return _optimisticallyAbortedBuilds.has(buildNumber);
}

function getOptimisticAbortedBuild(buildNumber) {
    pruneOptimisticAbortedBuilds();
    return _optimisticallyAbortedBuilds.get(buildNumber)?.build || null;
}

function normalizeLiveRunningBuild(build) {
    return {
        ...normalizeOverviewBuild(build),
        result: null,
    };
}

function mergeOverviewTrendWithLiveRunningBuilds(builds) {
    pruneOptimisticAbortedBuilds();

    const liveMap = new Map(
        (_liveRunningBuilds || []).map(build => [build.number, build])
    );
    const merged = [];
    const seenNumbers = new Set();

    (Array.isArray(builds) ? builds : []).forEach(build => {
        if (build?.number !== undefined && build?.number !== null) {
            seenNumbers.add(build.number);
        }

        const liveBuild = liveMap.get(build.number);
        if (liveBuild) {
            merged.push({
                ...build,
                ...liveBuild,
                stages: Array.isArray(liveBuild.stages) ? liveBuild.stages : [],
                result: null,
            });
            return;
        }

        const optimisticBuild = getOptimisticAbortedBuild(build.number);
        if (build.result === null && optimisticBuild) {
            merged.push({
                ...build,
                ...optimisticBuild,
                result: 'ABORTED',
                stages: Array.isArray(optimisticBuild.stages) ? optimisticBuild.stages : [],
            });
            return;
        }

        if (build.result !== null) {
            clearOptimisticBuildAbort(build.number);
        }

        merged.push(build);
    });

    (_liveRunningBuilds || []).forEach(build => {
        if (seenNumbers.has(build.number)) return;
        merged.push(build);
    });

    _optimisticallyAbortedBuilds.forEach((entry, buildNumber) => {
        if (!entry?.build || seenNumbers.has(buildNumber)) return;
        merged.push({
            ...entry.build,
            result: 'ABORTED',
        });
    });

    return merged.sort((a, b) =>
        (b.timestamp || 0) - (a.timestamp || 0) || (b.number || 0) - (a.number || 0)
    );
}

function handleBuildAbortSuccess(buildNumber) {
    const buildSnapshot = findCurrentOverviewBuild(buildNumber);
    markBuildOptimisticallyAborted(buildNumber, buildSnapshot);
    _liveRunningBuilds = (_liveRunningBuilds || []).filter(build => build.number !== buildNumber);
    _prevRunningNumbers.delete(buildNumber);

    if (_lastOverviewPayload) {
        renderOverviewPayload(_lastOverviewPayload, { eagerRunningStages: false });
    } else {
        updateActiveBuilds(_liveRunningBuilds.length, _liveRunningBuilds);
    }

    scheduleOverviewKpiBurstRefresh();
}

function refreshRunningBuildsNow() {
    return Promise.resolve();
}

function getOverviewLiveStreamUrl() {
    return document.body.dataset.liveStreamUrl || '';
}

function canUseOverviewLiveStream() {
    return typeof window.EventSource !== 'undefined' && Boolean(getOverviewLiveStreamUrl());
}

function overviewLiveStreamActive() {
    return Boolean(_overviewLiveStream);
}

function applyOverviewLiveRunningBuildsData(
    builds,
    {
        updateStageStrips = true,
        source = 'stream',
    } = {}
) {
    const previousRunningNumbers = new Set((_liveRunningBuilds || []).map(build => build.number));
    const normalizedBuilds = (Array.isArray(builds) ? builds : [])
        .map(normalizeLiveRunningBuild)
        .filter(build => !isBuildOptimisticallyAborted(build.number))
        .sort((a, b) =>
            (b.timestamp || 0) - (a.timestamp || 0) || (b.number || 0) - (a.number || 0)
        );

    _hasLiveRunningPollData = true;
    _liveRunningBuilds = normalizedBuilds;

    const currentRunningNumbers = new Set(_liveRunningBuilds.map(build => build.number));
    const buildJustFinished = Array.from(previousRunningNumbers)
        .some(number => !currentRunningNumbers.has(number));

    if (_liveRunningBuilds.length > 0) {
        _liveRunningEmptyPollStreak = 0;
    } else if (source === 'poll') {
        _liveRunningEmptyPollStreak += 1;
    } else {
        _liveRunningEmptyPollStreak = 0;
    }

    if (_lastOverviewPayload) {
        renderOverviewPayload(_lastOverviewPayload, { eagerRunningStages: false });
    } else {
        updateActiveBuilds(_liveRunningBuilds.length, _liveRunningBuilds);
    }

    if (updateStageStrips) {
        _liveRunningBuilds.forEach(build => {
            const strip = document.querySelector('#brow-' + build.number + ' .stage-strip');
            if (!strip || !Array.isArray(build.stages) || !build.stages.length) return;
            const nextSignature = _buildStageStripSignature(build.stages);

            if (strip.dataset.stageSignature !== nextSignature) {
                strip.innerHTML = buildOverviewStageSegmentsHtml(build.number, build.stages);
                strip.dataset.stageSignature = nextSignature;
            } else {
                updateOverviewStageSegmentDurations(strip, build.stages);
            }
        });
    }

    if (buildJustFinished) {
        if (source === 'stream') {
            scheduleOverviewFinishedBuildFallback(Date.now());
        } else {
            scheduleOverviewKpiBurstRefresh();
        }
    }

    if (
        source === 'poll'
        && _runningStagesHandle
        && _liveRunningBuilds.length === 0
        && _liveRunningEmptyPollStreak >= LIVE_RUNNING_IDLE_CONFIRM_POLLS
    ) {
        stopLiveRunningPoll();
    }
}

function closeOverviewLiveStream() {
    if (_overviewLiveStream) {
        _overviewLiveStream.close();
        _overviewLiveStream = null;
    }
    _overviewLiveStreamLoggedError = false;
}

function stopOverviewPollingFallback() {
    if (_overviewLiveStreamFallbackHandle) {
        clearInterval(_overviewLiveStreamFallbackHandle);
        _overviewLiveStreamFallbackHandle = null;
    }
    _overviewLiveStreamFallbackStarted = false;
}

function startOverviewPollingFallback({ eager = true } = {}) {
    if (_overviewLiveStreamFallbackHandle) return;

    _overviewLiveStreamFallbackStarted = true;
    _liveRunningBuilds = [];
    _hasLiveRunningPollData = false;
    _liveRunningEmptyPollStreak = 0;

    if (eager) {
        void loadKPIs();
    }

    _overviewLiveStreamFallbackHandle = window.setInterval(() => {
        void loadKPIs();
    }, LIVE_RUNNING_POLL_MS);
}

function markOverviewLiveStreamHealthy() {
    _overviewLiveStreamReceived = true;
    _overviewLiveStreamLoggedError = false;
    stopOverviewPollingFallback();
}

function connectOverviewLiveStream() {
    if (!canUseOverviewLiveStream() || overviewLiveStreamActive()) return false;

    _overviewLiveStream = new EventSource(getOverviewLiveStreamUrl());
    _overviewLiveStream.addEventListener('open', () => {
        markOverviewLiveStreamHealthy();
    });
    _overviewLiveStream.addEventListener('stream_ready', () => {
        markOverviewLiveStreamHealthy();
    });
    _overviewLiveStream.addEventListener('heartbeat', () => {
        markOverviewLiveStreamHealthy();
    });
    _overviewLiveStream.addEventListener('jenkins_status', event => {
        markOverviewLiveStreamHealthy();
        try {
            applyJenkinsStatusPayload(JSON.parse(event.data));
        } catch (error) {
            console.error('Overview Jenkins SSE parse error:', error);
        }
    });
    _overviewLiveStream.addEventListener('azure_status', event => {
        markOverviewLiveStreamHealthy();
        try {
            applyAzureStatusPayload(JSON.parse(event.data));
        } catch (error) {
            console.error('Overview Azure SSE parse error:', error);
        }
    });
    _overviewLiveStream.addEventListener('running_stages', event => {
        markOverviewLiveStreamHealthy();
        try {
            const payload = JSON.parse(event.data);
            applyOverviewLiveRunningBuildsData(payload?.builds, {
                updateStageStrips: true,
                source: 'stream',
            });
        } catch (error) {
            console.error('Overview running stages SSE parse error:', error);
        }
    });
    _overviewLiveStream.addEventListener('overview_payload', event => {
        markOverviewLiveStreamHealthy();
        try {
            const payload = JSON.parse(event.data);
            if (payload?.connected) {
                _lastOverviewSsePayloadAt = Date.now();
                clearOverviewFinishedBuildFallback();
                renderOverviewPayload(payload, { eagerRunningStages: false });
                stopOverviewKpiBurstRefresh();
            }
        } catch (error) {
            console.error('Overview payload SSE parse error:', error);
        }
    });
    _overviewLiveStream.addEventListener('build_started', () => {
        markOverviewLiveStreamHealthy();
        scheduleOverviewKpiBurstRefresh();
    });
    _overviewLiveStream.addEventListener('build_finished', () => {
        markOverviewLiveStreamHealthy();
        scheduleOverviewFinishedBuildFallback(Date.now());
    });
    _overviewLiveStream.addEventListener('snapshot_refreshed', () => {
        markOverviewLiveStreamHealthy();
    });
    _overviewLiveStream.onerror = () => {
        if (!_overviewLiveStreamLoggedError) {
            console.warn('Overview SSE stream disconnected. The browser will retry automatically.');
            _overviewLiveStreamLoggedError = true;
        }
        startOverviewPollingFallback({ eager: !_overviewLiveStreamReceived });
    };

    return true;
}

async function checkForRunningBuilds() {
    return Promise.resolve();
}

function fmtDate(ts) {
    return formatUserDateTime(ts, {
        includeYear: false,
        includeSeconds: false,
        fallback: ''
    });
}

function currentUserCanStartBuilds() {
    return document.body.dataset.canStartBuilds === 'true';
}

function currentUserCanAbortBuilds() {
    return document.body.dataset.canAbortBuilds === 'true';
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
    const abortButton = currentUserCanAbortBuilds()
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
    stopLiveRunningPoll({ restartWatcher: false });
    stopRunningBuildsWatcher();

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
        if (typeof _testsDuration24hChart.$cleanupHoverBridge === 'function') {
            _testsDuration24hChart.$cleanupHoverBridge();
        }
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

function normalizeOverviewTestsDurationValue(value) {
    const num = Number(value);
    return Number.isFinite(num) ? num : 0;
}

function classifyOverviewTestsDurationStage(stageName) {
    const normalized = String(stageName || '')
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, ' ');
    if (!normalized) return null;
    if (normalized.includes('pylint')) return 'pylint_ms';
    if (normalized.includes('sonar')) return 'sonarcloud_ms';
    if (normalized.includes('pytest')) return 'unit_tests_ms';
    if (normalized.includes('unit') && normalized.includes('test')) return 'unit_tests_ms';
    if (
        normalized.includes('test')
        && !['integration', 'e2e', 'smoke', 'acceptance', 'performance', 'load']
            .some(marker => normalized.includes(marker))
    ) {
        return 'unit_tests_ms';
    }
    return null;
}

function deriveOverviewTestsDurationFromBuild(build) {
    const totals = {
        unit_tests_ms: 0,
        pylint_ms: 0,
        sonarcloud_ms: 0,
    };

    (build?.stages || []).forEach(stage => {
        const bucket = classifyOverviewTestsDurationStage(stage?.name);
        if (!bucket) return;
        totals[bucket] += normalizeOverviewTestsDurationValue(stage?.duration_ms);
    });

    const totalDurationMs = totals.unit_tests_ms + totals.pylint_ms + totals.sonarcloud_ms;
    return {
        total_duration_ms: totalDurationMs,
        ...totals,
    };
}

function buildOverviewTestsDurationPoints(buildTrend, testsDurationTrend) {
    const durationByBuildNumber = new Map(
        (Array.isArray(testsDurationTrend) ? testsDurationTrend : [])
            .filter(point => point?.number != null)
            .map(point => [point.number, point])
    );

    return (Array.isArray(buildTrend) ? buildTrend : [])
        .filter(build => build?.number != null && build?.result !== null && _isWithinLast24Hours(build))
        .sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0) || (a.number || 0) - (b.number || 0))
        .map(build => {
            const storedPoint = durationByBuildNumber.get(build.number);
            const derivedPoint = deriveOverviewTestsDurationFromBuild(build);
            const totalDurationMs = storedPoint?.total_duration_ms != null
                ? normalizeOverviewTestsDurationValue(storedPoint.total_duration_ms)
                : derivedPoint.total_duration_ms;

            return {
                number: build.number,
                timestamp: build.timestamp || 0,
                total_duration_ms: totalDurationMs,
                unit_tests_ms: storedPoint?.unit_tests_ms != null
                    ? normalizeOverviewTestsDurationValue(storedPoint.unit_tests_ms)
                    : derivedPoint.unit_tests_ms,
                pylint_ms: storedPoint?.pylint_ms != null
                    ? normalizeOverviewTestsDurationValue(storedPoint.pylint_ms)
                    : derivedPoint.pylint_ms,
                sonarcloud_ms: storedPoint?.sonarcloud_ms != null
                    ? normalizeOverviewTestsDurationValue(storedPoint.sonarcloud_ms)
                    : derivedPoint.sonarcloud_ms,
            };
        });
}

function setOverviewTestsDurationActiveIndex(chart, index) {
    if (!chart || index == null || index < 0) return;
    const xScale = chart.scales?.x;
    const yScale = chart.scales?.y;
    if (!xScale || !yScale) return;

    const activeElements = [{ datasetIndex: 0, index }];
    const anchor = {
        x: xScale.getPixelForValue(index),
        y: yScale.getPixelForValue(chart.data.datasets[0].data[index] || 0),
    };
    chart.setActiveElements(activeElements);
    chart.tooltip.setActiveElements(activeElements, anchor);
    chart.update('none');
}

function clearOverviewTestsDurationActiveIndex(chart) {
    if (!chart) return;
    chart.setActiveElements([]);
    chart.tooltip.setActiveElements([], { x: 0, y: 0 });
    chart.update('none');
}

function attachOverviewTestsDurationHoverBridge(chart) {
    const canvas = chart?.canvas;
    if (!canvas) return;

    const handleMouseMove = event => {
        const rect = canvas.getBoundingClientRect();
        const x = event.clientX - rect.left;
        const y = event.clientY - rect.top;
        const chartArea = chart.chartArea;
        const xScale = chart.scales?.x;
        if (!chartArea || !xScale) return;

        const withinColumnZone = (
            x >= chartArea.left
            && x <= chartArea.right
            && y >= chartArea.top
            && y <= chart.height
        );
        if (!withinColumnZone) {
            clearOverviewTestsDurationActiveIndex(chart);
            return;
        }

        let nearestIndex = 0;
        let smallestDistance = Number.POSITIVE_INFINITY;
        for (let index = 0; index < xScale.ticks.length; index += 1) {
            const distance = Math.abs(x - xScale.getPixelForTick(index));
            if (distance < smallestDistance) {
                smallestDistance = distance;
                nearestIndex = index;
            }
        }

        setOverviewTestsDurationActiveIndex(chart, nearestIndex);
    };

    const handleMouseLeave = () => clearOverviewTestsDurationActiveIndex(chart);
    canvas.addEventListener('mousemove', handleMouseMove);
    canvas.addEventListener('mouseleave', handleMouseLeave);
    chart.$cleanupHoverBridge = () => {
        canvas.removeEventListener('mousemove', handleMouseMove);
        canvas.removeEventListener('mouseleave', handleMouseLeave);
    };
}

function renderOverviewTestsDuration24hChart(buildTrend, testsDurationTrend) {
    const canvas = document.getElementById('testsDuration24hChart');
    if (!canvas || typeof Chart === 'undefined') return;

    const container = canvas.parentElement;
    const badge = document.getElementById('testsDuration24hBadge');
    const summary = document.getElementById('testsDuration24hSummary');
    const points = buildOverviewTestsDurationPoints(buildTrend, testsDurationTrend);

    if (!points.length) {
        clearOverviewTestsDuration24hChart();
        return;
    }

    canvas.style.display = 'block';
    const existingEmpty = container.querySelector('.chart-empty');
    if (existingEmpty) existingEmpty.remove();

    const avgDurationMs = Math.round(
        points.reduce((sum, point) => sum + point.total_duration_ms, 0) / Math.max(points.length, 1)
    );
    const latestPoint = points[points.length - 1];
    const maxDurationMs = Math.max(...points.map(point => point.total_duration_ms));

    if (badge) {
        badge.textContent = `24h Avg ${fmtDur(avgDurationMs)}`;
    }
    if (summary) {
        const buildsWithTests = points.filter(point => point.total_duration_ms > 0).length;
        summary.innerHTML =
            `<span class="overview-tests-duration-pill"><strong>${points.length}</strong> finished builds</span>` +
            `<span class="overview-tests-duration-pill"><strong>${buildsWithTests}</strong> reached test stages</span>` +
            `<span class="overview-tests-duration-pill"><strong>${fmtDur(latestPoint.total_duration_ms)}</strong> latest build</span>`;
    }

    const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
    const gridColor = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)';
    const textColor = isDark ? '#9c9a92' : '#73726c';
    const titleColor = isDark ? '#c2c0b6' : '#3d3d3a';

    if (_testsDuration24hChart) {
        if (typeof _testsDuration24hChart.$cleanupHoverBridge === 'function') {
            _testsDuration24hChart.$cleanupHoverBridge();
        }
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
            interaction: {
                mode: 'index',
                intersect: false,
                axis: 'x',
            },
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
                    ticks: {
                        color: textColor,
                        font: { size: 9 },
                        autoSkip: false,
                        maxRotation: 0,
                        minRotation: 0,
                    }
                },
                y: {
                    min: 0,
                    suggestedMax: Math.max(maxDurationMs * 1.15, avgDurationMs * 1.3, 60_000),
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
    attachOverviewTestsDurationHoverBridge(_testsDuration24hChart);
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

function updateOverview24hInfo(finishedCount, runningCount) {
    const finishedEl = document.getElementById('overview24hFinishedCount');
    const runningEl = document.getElementById('overview24hRunningCount');
    if (!finishedEl && !runningEl) return;

    if (typeof finishedCount !== 'number' || !Number.isFinite(finishedCount)) {
        if (finishedEl) finishedEl.textContent = '--';
        if (runningEl) runningEl.textContent = '--';
        return;
    }

    if (finishedEl) finishedEl.textContent = String(finishedCount);
    if (runningEl) runningEl.textContent = String(runningCount);
}

function renderOverviewPayload(d, { eagerRunningStages = true } = {}) {
    if (!d?.connected) {
        resetOverviewRenderCache();
        _prevRunningNumbers = new Set();
        Object.values(_activeTimers).forEach(clearInterval);
        _activeTimers = {};
        stopOverviewKpiBurstRefresh();
        stopLiveRunningPoll({ restartWatcher: false });
        stopRunningBuildsWatcher();
        _lastOverviewPayload = null;
        _liveRunningBuilds = [];
        _hasLiveRunningPollData = false;
        _optimisticallyAbortedBuilds.clear();
        clearDashboard();
        clearOverviewHistory();
        clearOverviewTestsDuration24hChart('No tests duration data available');
        updateOverview24hInfo(null, null);
        return;
    }

    _lastOverviewPayload = d;

    const baseTrend = (d.build_trend || []).map(build => ({
        ...build,
        duration: build.duration ?? build.duration_ms ?? ((build.duration_seconds ?? 0) * 1000),
        stages: Array.isArray(build.stages) ? build.stages : [],
    }));
    const trend = mergeOverviewTrendWithLiveRunningBuilds(baseTrend);
    const metrics = _cacheOverviewMetrics(d);
    const latestBuildTag = document.getElementById('latestBuildTag');

    if (metrics.avg_duration_ms > 0) _avgDurationMs = metrics.avg_duration_ms;
    if (latestBuildTag && metrics.last_build_number) {
        latestBuildTag.textContent = '#' + metrics.last_build_number;
    }

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

    const snapshotRunningBuilds = baseTrend.filter(build => build.result === null);
    const snapshotShowsRunning = snapshotRunningBuilds.length > 0 || (metrics.running ?? 0) > 0;
    if (overviewLiveStreamActive()) {
        stopLiveRunningPoll({ restartWatcher: false });
        stopRunningBuildsWatcher();
    } else if (!hasOverviewLiveBuildWidgets()) {
        stopLiveRunningPoll({ restartWatcher: false });
        stopRunningBuildsWatcher();
    } else if (snapshotShowsRunning && !_runningStagesHandle) {
        startLiveRunningPoll({ eager: eagerRunningStages });
    } else if (!snapshotShowsRunning && !_runningStagesHandle) {
        startRunningBuildsWatcher({ eager: !_runningBuildsWatcherHandle });
    }

    const activeBuilds = _hasLiveRunningPollData
        ? [..._liveRunningBuilds]
        : trend.filter(build => build.result === null);
    const nowRunning = new Set(activeBuilds.map(b => b.number));
    trend.filter(b => b.result !== null && _prevRunningNumbers.has(b.number))
         .forEach(notifyBuildFinished);
    _prevRunningNumbers = nowRunning;

    const activeSignature = _buildListSignature(activeBuilds, { includeDuration: false });
    if (_overviewActiveSignature !== activeSignature || metrics.running !== activeBuilds.length) {
        updateActiveBuilds(activeBuilds.length, activeBuilds);
        _overviewActiveSignature = activeSignature;
    }

    const now = Date.now();
    const historyLast24h = trend
        .filter(build => _isWithinLast24Hours(build, now))
        .sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0) || (b.number || 0) - (a.number || 0));
    const runningLast24h = historyLast24h.filter(build => build.result === null).length;
    const finishedLast24hCount = historyLast24h.length - runningLast24h;
    updateOverview24hInfo(finishedLast24hCount, runningLast24h);
    const finishedLast24h = baseTrend.filter(
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

    const testsDurationSignature = _buildTestsDurationSignature(trend, d.tests_duration);
    if (Array.isArray(d.tests_duration)) {
        if (_overviewTestsDurationSignature !== testsDurationSignature) {
            renderOverviewTestsDuration24hChart(trend, d.tests_duration);
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
}

function getInitialOverviewPayload() {
    const el = document.getElementById('overviewInitialData');
    if (!el) return null;

    try {
        const payload = JSON.parse(el.textContent || '{}');
        return Object.keys(payload).length ? payload : null;
    } catch (e) {
        console.error('Initial overview payload parse error:', e);
        return null;
    }
}

async function loadKPIs({ refresh = false, wait = false } = {}) {
    if (_overviewLoadInFlight) return;
    _overviewLoadInFlight = true;

    try {
        const baseUrl = document.body.dataset.kpisUrl;
        const url = refresh
            ? `${baseUrl}?refresh=1${wait ? '&wait=1' : ''}`
            : baseUrl;
        const res = await fetch(url);
        const d   = await res.json();
        renderOverviewPayload(d);
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

        const abortButton = currentUserCanAbortBuilds()
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
    if (!currentUserCanStartBuilds()) {
        showToast('You do not have permission to start builds.', 'abort-toast');
        return;
    }

    triggerBuildWithConfirmation({
        bodyHtml: `Are you sure you want to trigger a new build for ${pipelineStrongLabel()} on <strong>${escapeHtml(getBranchName())}</strong>?`,
        queuedMessage: '✅ Build queued — watch Active Builds',
        triggerErrorMessage: 'Failed to trigger build',
        onQueued() {
            loadImmediateOverviewKPIs();
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
  return Promise.resolve();
}

document.addEventListener('DOMContentLoaded', () => {
    requestNotificationPermission();
    const hasLiveStream = connectOverviewLiveStream();
    const initialPayload = getInitialOverviewPayload();
    if (initialPayload) {
        renderOverviewPayload(initialPayload, { eagerRunningStages: false });
    } else {
        void loadKPIs();
    }
    if (!hasLiveStream) {
        startOverviewPollingFallback({ eager: false });
    }
});

window.addEventListener('beforeunload', () => {
    stopOverviewPollingFallback();
    closeOverviewLiveStream();
});
