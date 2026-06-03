const INITIAL_SHOW = 5;
const POLL_MS = 2000;
const SLOW_POLL_MS = 10000;
const KPI_COMPLETION_BURST_MS = 12000;
const KPI_COMPLETION_BURST_INTERVAL_MS = 2000;

let _allBuilds = [];
let _showingAll = false;
let _avgDurationMs = 120000;
let _activeTimers = {};
let _pollHandle = null;
let _slowHandle = null;
let _stagesHandle = null;
let _durationGrouping = 'week';
let _durationSourceBuilds = [];
let _groupedDurationChart = null;
let _coverageGrouping = 'week';
let _coverageSourcePoints = [];
let _testsDurationGrouping = 'week';
let _testsDurationSourcePoints = [];
let _junitGrouping = 'week';
let _junitSourcePoints = [];
let _pipelineKpisLoadInFlight = false;
let _pipelineGroupedDurationRenderSignature = null;
let _pipelineStageFailureRenderSignature = null;
let _pipelineCoverageRenderSignature = null;
let _pipelineJunitRenderSignature = null;
let _pipelineTestsDurationTrendRenderSignature = null;
let _pipelineTestsDuration24hRenderSignature = null;
let _pipelineKpiBurstHandle = null;
let _pipelineKpiBurstStopAt = 0;
let _prevRunningBuildNumbers = new Set();
let _pipelineLiveStream = null;
let _pipelineLiveStreamReceived = false;

// Legacy build-history helpers are intentionally kept in this file for
// possible rollback, even though the current page no longer renders the
// Build History timeline.
// ── TOOLTIP
const _tip = document.getElementById('segTip');

function showSegTip(el, name, dur, stcls, sttext) {
  if (!_tip) return;
  document.getElementById('stName').textContent = name;
  document.getElementById('stDur').textContent = dur || '';
  const st = document.getElementById('stStatus');
  st.textContent = sttext;
  st.className = 'st-status ' + stcls;
  _tip.classList.add('show');

  const r = el.getBoundingClientRect();
  const tipW = _tip.offsetWidth || 160;
  let left = r.left + r.width / 2 - tipW / 2;
  let top = r.top - (_tip.offsetHeight || 80) - 10;

  if (left < 8) left = 8;
  if (left + tipW > window.innerWidth - 8) left = window.innerWidth - tipW - 8;
  if (top < 8) top = r.bottom + 8;

  _tip.style.left = left + 'px';
  _tip.style.top = top + 'px';
}

function hideSegTip() {
  if (!_tip) return;
  _tip.classList.remove('show');
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

function pipelineSignaturePart(value) {
  return value ?? '';
}

function getPipelineThemeSignature() {
  return document.documentElement.getAttribute('data-theme') || 'dark';
}

function buildPipelinePointsSignature(items, fields) {
  return (Array.isArray(items) ? items : [])
    .map(item => fields.map(field => pipelineSignaturePart(item?.[field])).join('|'))
    .join('||');
}

function buildPipelineRenderSignature(prefix, { grouping = '', items = [], fields = [] } = {}) {
  return [
    prefix,
    getPipelineThemeSignature(),
    grouping,
    buildPipelinePointsSignature(items, fields),
  ].join('::');
}

function resetPipelineChartRenderCache() {
  _pipelineGroupedDurationRenderSignature = null;
  _pipelineStageFailureRenderSignature = null;
  _pipelineCoverageRenderSignature = null;
  _pipelineJunitRenderSignature = null;
  _pipelineTestsDurationTrendRenderSignature = null;
  _pipelineTestsDuration24hRenderSignature = null;
}

function stopPipelineKpiBurstRefresh() {
  if (_pipelineKpiBurstHandle) {
    clearInterval(_pipelineKpiBurstHandle);
    _pipelineKpiBurstHandle = null;
  }
  _pipelineKpiBurstStopAt = 0;
}

function getPipelineLiveStreamUrl() {
  return document.body.dataset.liveStreamUrl || '';
}

function canUsePipelineLiveStream() {
  return typeof window.EventSource !== 'undefined' && Boolean(getPipelineLiveStreamUrl());
}

function pipelineLiveStreamActive() {
  return Boolean(_pipelineLiveStream);
}

function closePipelineLiveStream() {
  if (_pipelineLiveStream) {
    _pipelineLiveStream.close();
    _pipelineLiveStream = null;
  }
}

function connectPipelineLiveStream() {
  if (!canUsePipelineLiveStream() || pipelineLiveStreamActive()) return false;

  _pipelineLiveStream = new EventSource(getPipelineLiveStreamUrl());
  _pipelineLiveStream.addEventListener('open', () => {
    _pipelineLiveStreamReceived = true;
  });
  _pipelineLiveStream.addEventListener('stream_ready', () => {
    _pipelineLiveStreamReceived = true;
  });
  _pipelineLiveStream.addEventListener('heartbeat', () => {
    _pipelineLiveStreamReceived = true;
  });
  _pipelineLiveStream.addEventListener('jenkins_status', event => {
    _pipelineLiveStreamReceived = true;
    try {
      applyJenkinsStatusPayload(JSON.parse(event.data));
    } catch (error) {
      console.error('Pipeline Jenkins SSE parse error:', error);
    }
  });
  _pipelineLiveStream.addEventListener('azure_status', event => {
    _pipelineLiveStreamReceived = true;
    try {
      applyAzureStatusPayload(JSON.parse(event.data));
    } catch (error) {
      console.error('Pipeline Azure SSE parse error:', error);
    }
  });
  _pipelineLiveStream.addEventListener('build_started', () => {
    _pipelineLiveStreamReceived = true;
  });
  _pipelineLiveStream.addEventListener('build_finished', () => {
    _pipelineLiveStreamReceived = true;
  });
  _pipelineLiveStream.addEventListener('snapshot_refreshed', () => {
    _pipelineLiveStreamReceived = true;
    loadPipelineKPIs();
  });
  _pipelineLiveStream.onerror = () => {
    closePipelineLiveStream();
    console.warn('Pipeline SSE stream closed. REST fallback polling is disabled.');
  };

  return true;
}

function schedulePipelineKpiBurstRefresh({
  durationMs = KPI_COMPLETION_BURST_MS,
  intervalMs = KPI_COMPLETION_BURST_INTERVAL_MS,
  immediate = true,
} = {}) {
  if (pipelineLiveStreamActive()) {
    stopPipelineKpiBurstRefresh();
    return;
  }

  const stopAt = Date.now() + durationMs;
  _pipelineKpiBurstStopAt = Math.max(_pipelineKpiBurstStopAt, stopAt);

  if (immediate) {
    window.setTimeout(() => loadPipelineKPIs(), 0);
  }

  if (_pipelineKpiBurstHandle) return;

  _pipelineKpiBurstHandle = setInterval(() => {
    if (Date.now() >= _pipelineKpiBurstStopAt) {
      stopPipelineKpiBurstRefresh();
      return;
    }
    loadPipelineKPIs();
  }, intervalMs);
}

function segCls(status) {
  if (!status || status === 'IN_PROGRESS') return 'run';
  if (status === 'SUCCESS') return 'ok';
  if (status === 'FAILED') return 'fail';
  return 'skip';
}

function stageStatusText(status) {
  if (!status || status === 'IN_PROGRESS') return '⟳ In progress';
  if (status === 'SUCCESS') return '✓ Passed';
  if (status === 'FAILED') return '✗ Failed';
  return status;
}

function dotCls(r) {
  return !r ? 'run' : r === 'SUCCESS' ? 'pass' : r === 'FAILURE' ? 'fail' : 'abrt';
}

function pipelineResultCls(r) {
  return !r ? 'run' : r === 'SUCCESS' ? 'pass' : r === 'FAILURE' ? 'fail' : 'abrt';
}

function pipelineResultLabel(r) {
  if (!r) return '● Running';
  if (r === 'SUCCESS') return '✓ Success';
  if (r === 'FAILURE') return '✗ Failure';
  return '⊘ ' + r;
}

function updateCircle(circleId, valueId, badgeId, pct) {
  const c = document.getElementById(circleId);
  const v = document.getElementById(valueId);
  const b = document.getElementById(badgeId);

  if (c) c.style.strokeDashoffset = 150.796 * (1 - pct / 100);
  if (v) v.textContent = Math.round(pct);

  if (b) {
    if (pct >= 80) {
      b.className = 'kpi-badge green';
      b.textContent = '↑ Excellent';
    } else if (pct >= 50) {
      b.className = 'kpi-badge blue';
      b.textContent = '~ Fair';
    } else {
      b.className = 'kpi-badge red';
      b.textContent = '↓ Poor';
    }
  }
}

function buildRowHtml(b) {
  const isRunning = b.result === null;
  const stages = b.stages || [];
  const elapsed = isRunning ? Math.round((Date.now() - b.timestamp) / 1000) : 0;
  const avgSec = Math.round(_avgDurationMs / 1000);
  const pct = isRunning ? Math.min(95, Math.round((elapsed / avgSec) * 100)) : 0;
  const m = Math.floor(elapsed / 60);
  const sv = elapsed % 60;
  const durText = isRunning ? m + 'm ' + String(sv).padStart(2, '0') + 's' : '';

  let segHtml;
  if (stages.length) {
    segHtml = stages.map(st => {
      const cls = segCls(st.status);
      const tipDur = fmtDur(st.duration_ms) || '';
      const tipSt = stageStatusText(st.status);
      const name = (st.name || 'Stage').replace(/"/g, '&quot;');

      return `<div class="seg ${cls}"
        data-name="${name}"
        data-dur="${tipDur}"
        data-stcls="${cls}"
        data-sttext="${tipSt}"
        onmouseenter="showSegTip(this,this.dataset.name,this.dataset.dur,this.dataset.stcls,this.dataset.sttext)"
        onmouseleave="hideSegTip()"
        onclick="event.stopPropagation();openConsole(${b.number})"></div>`;
    }).join('');
  } else {
    segHtml = `<span class="no-stage-txt">${isRunning ? '⟳ waiting for stages…' : 'No stage data'}</span>`;
  }

  const runBar = isRunning
    ? `<div class="run-bar"><div class="run-bar-fill" id="rb-${b.number}" style="width:${pct}%"></div></div>`
    : '';
  const abortButton = currentUserCanAbortBuilds()
    ? `<button class="br-abort" onclick="event.stopPropagation();confirmAbort(${b.number})" title="Abort build #${b.number}">
         <svg viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
       </button>`
    : '';

  const resultCell = isRunning
    ? `<div>
         <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
           <span class="br-result run">${pipelineResultLabel(b.result)}</span>
           ${abortButton}
         </div>
         <div style="font-size:9.5px;font-family:'JetBrains Mono',monospace;color:var(--text2);margin-top:4px;" id="brdur-${b.number}">${durText}</div>
         <div class="br-console">↗ console</div>
       </div>`
    : `<div>
         <span class="br-result ${pipelineResultCls(b.result)}">${pipelineResultLabel(b.result)}</span>
         <div style="font-size:9.5px;font-family:'JetBrains Mono',monospace;color:var(--text2);margin-top:4px;"></div>
         <div class="br-console">↗ console</div>
       </div>`;

  return `
    <div class="build-row ${isRunning ? 'is-running' : ''}" id="brow-${b.number}" onclick="openConsole(${b.number})">
      <div>
        <div class="br-num">#${b.number}</div>
        <div class="br-date">${fmtDate(b.timestamp)}</div>
      </div>
      <div class="br-dot ${dotCls(b.result)}"></div>
      <div class="stage-strip">${segHtml}</div>
      ${resultCell}
      ${runBar}
    </div>`;
}

function renderTimeline() {
  const container = document.getElementById('buildTimeline');
  const btn = document.getElementById('showMoreBtn');
  const badge = document.getElementById('runningBadge');
  if (!container) return;

  const running = _allBuilds.filter(b => b.result === null);
  const finished = _allBuilds.filter(b => b.result !== null);

  if (badge) {
    badge.style.display = running.length ? 'inline-flex' : 'none';
    if (running.length) badge.textContent = '● ' + running.length + ' running';
  }

  const finishedToShow = _showingAll ? finished : finished.slice(0, INITIAL_SHOW);
  const toRender = [...running, ...finishedToShow];

  container.innerHTML = toRender.length
    ? toRender.map(buildRowHtml).join('')
    : '<div class="tl-empty">No builds found.</div>';

  if (btn) {
    if (finished.length > INITIAL_SHOW) {
      btn.style.display = 'block';
      btn.textContent = _showingAll
        ? 'Show less ↑'
        : 'Show more ↓  (' + (finished.length - INITIAL_SHOW) + ' more)';
    } else {
      btn.style.display = 'none';
    }
  }

  startRunningTimers(running);
}

function startRunningTimers(running) {
  const runNums = new Set(running.map(b => b.number));

  Object.keys(_activeTimers).forEach(n => {
    if (!runNums.has(parseInt(n))) {
      clearInterval(_activeTimers[n]);
      delete _activeTimers[n];
    }
  });

  running.forEach(b => {
    if (_activeTimers[b.number]) return;

    _activeTimers[b.number] = setInterval(() => {
      const elSec = Math.round((Date.now() - b.timestamp) / 1000);
      const pct = Math.min(95, Math.round((elSec / Math.round(_avgDurationMs / 1000)) * 100));
      const m = Math.floor(elSec / 60);
      const s = elSec % 60;

      const durEl = document.getElementById('brdur-' + b.number);
      const rbEl = document.getElementById('rb-' + b.number);

      if (durEl) durEl.textContent = m + 'm ' + String(s).padStart(2, '0') + 's';
      if (rbEl) rbEl.style.width = pct + '%';
    }, 1000);
  });
}

function toggleShowMore() {
  _showingAll = !_showingAll;
  renderTimeline();
}

function triggerBuild() {
  if (!currentUserCanStartBuilds()) {
    showToast('You do not have permission to start builds.', 'abort-toast');
    return;
  }

  triggerBuildWithConfirmation({
    bodyHtml: `Trigger a new build for ${pipelineStrongLabel()} on <strong>${escapeHtml(getBranchName())}</strong>?`,
    queuedMessage: '✅ Build queued — watching for updates',
    triggerErrorMessage: 'Failed to trigger',
    onQueued() {
      loadPipelineKPIs();
    }
  });
}

function toggleBuild() {
  triggerBuild();
}

function getSelectedBranchPayload(data) {
  const pipeline = data.pipeline || {};
  const branches = data.branches || {};
  const selectedBranch = pipeline.selected_branch || getBranchName();
  return branches[selectedBranch] || {};
}

function formatPeriodDuration(ms) {
  return fmtDur(ms || 0);
}

function formatAverageCount(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '0';
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function buildDurationMs(build) {
  return build.duration_ms ?? build.duration ?? ((build.duration_seconds ?? 0) * 1000);
}

function buildGroupStartKey(timestamp, grouping) {
  return grouping === 'month'
    ? getUserMonthStartKey(timestamp)
    : getUserWeekStartKey(timestamp);
}

function buildGroupEndKey(startKey, grouping) {
  return grouping === 'month'
    ? getUserMonthEndKey(startKey)
    : getUserWeekEndKey(startKey);
}

function formatGroupLabel(startKey, grouping) {
  if (!startKey) return '';
  if (grouping === 'month') {
    return formatUserMonthKeyLabel(startKey, 'short', { fallback: '' });
  }
  return formatUserDateKey(startKey, {
    includeYear: false,
    monthStyle: 'short',
    fallback: ''
  });
}

function formatGroupDetailLabel(startKey, grouping) {
  if (!startKey) return '';
  if (grouping === 'month') {
    return formatUserMonthKeyLabel(startKey, 'long', { fallback: '' });
  }
  return formatUserDateKeyRange(
    startKey,
    buildGroupEndKey(startKey, grouping),
    { monthStyle: 'short', fallback: '' }
  );
}

function buildDurationGroups(builds, grouping) {
  const grouped = new Map();
  const finished = (builds || []).filter(build =>
    build.result !== null &&
    build.timestamp &&
    buildDurationMs(build) > 0
  );

  finished.forEach(build => {
    const key = buildGroupStartKey(build.timestamp, grouping);
    if (!key) return;
    if (!grouped.has(key)) {
      grouped.set(key, {
        key,
        startMs: getUserDateKeySortValue(key),
        buildCount: 0,
        totalDurationMs: 0,
      });
    }

    const group = grouped.get(key);
    group.buildCount += 1;
    group.totalDurationMs += buildDurationMs(build);
  });

  const groups = Array.from(grouped.values())
    .sort((a, b) => a.startMs - b.startMs)
    .map(group => {
      const avgDurationMs = Math.round(group.totalDurationMs / group.buildCount);

      return {
        ...group,
        avgDurationMs,
        label: formatGroupLabel(group.key, grouping),
        detailLabel: formatGroupDetailLabel(group.key, grouping),
      };
    });

  const totalDurationMs = finished.reduce((sum, build) => sum + buildDurationMs(build), 0);
  const overallAvgMs = finished.length ? Math.round(totalDurationMs / finished.length) : 0;

  return {
    groups,
    finishedBuildCount: finished.length,
    overallAvgMs,
  };
}

function clearGroupedDurationChart(message = 'No finished build data available') {
  const canvas = document.getElementById('pipelineGroupedDurationChart');
  const summary = document.getElementById('pipelineDurationSummary');
  const avgBadge = document.getElementById('latestBuildsAvg');
  if (!canvas) return;

  _pipelineGroupedDurationRenderSignature = null;

  if (_groupedDurationChart) {
    _groupedDurationChart.destroy();
    _groupedDurationChart = null;
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
  if (avgBadge) avgBadge.textContent = 'Avg —';
}

function clearStageFailureChart(message = 'No stage data available') {
  const container = document.getElementById('stageFailureChart');
  if (!container) return;

  _pipelineStageFailureRenderSignature = null;

  if (window._stageChart) {
    window._stageChart.destroy();
    window._stageChart = null;
  }

  container.innerHTML = `<div style="text-align:center;color:var(--text2);padding:20px;font-size:12px;">${message}</div>`;
}

function renderGroupedDurationChart(builds) {
  const canvas = document.getElementById('pipelineGroupedDurationChart');
  if (!canvas) return;
  const container = canvas.parentElement;
  const summary = document.getElementById('pipelineDurationSummary');
  const subtitle = document.getElementById('pipelineDurationSub');
  const avgBadge = document.getElementById('latestBuildsAvg');

  const finishedBuilds = (Array.isArray(builds) ? builds : []).filter(build => build.result !== null);
  const renderSignature = buildPipelineRenderSignature('grouped-duration', {
    grouping: _durationGrouping,
    items: finishedBuilds,
    fields: ['number', 'result', 'timestamp', 'duration', 'duration_ms', 'duration_seconds'],
  });

  const { groups, finishedBuildCount, overallAvgMs } = buildDurationGroups(builds, _durationGrouping);
  const periodLabel = _durationGrouping === 'month' ? 'month' : 'week';
  const periodLabelPlural = _durationGrouping === 'month' ? 'months' : 'weeks';

  if (!groups.length) {
    const emptySignature = `empty::${renderSignature}`;
    if (_pipelineGroupedDurationRenderSignature === emptySignature) return;
    if (subtitle) {
      subtitle.textContent = `Average duration grouped by ${periodLabel}`;
    }
    clearGroupedDurationChart(`No finished builds available for ${periodLabel} grouping`);
    _pipelineGroupedDurationRenderSignature = emptySignature;
    return;
  }

  if (_pipelineGroupedDurationRenderSignature === renderSignature) return;

  if (subtitle) {
    subtitle.textContent = `Average duration grouped by ${periodLabel}`;
  }

  const existingEmpty = container.querySelector('.chart-empty');
  if (existingEmpty) existingEmpty.remove();

  if (summary) {
    summary.innerHTML =
      `<span class="pipeline-duration-pill"><strong>${groups.length}</strong> ${periodLabelPlural}</span>` +
      `<span class="pipeline-duration-pill"><strong>${finishedBuildCount}</strong> finished builds</span>` +
      `<span class="pipeline-duration-pill"><strong>${formatPeriodDuration(groups[groups.length - 1].avgDurationMs)}</strong> latest ${periodLabel} avg</span>`;
  }

  if (avgBadge) {
    avgBadge.textContent = `Avg: ${formatPeriodDuration(overallAvgMs)}`;
  }

  canvas.style.display = 'block';
  if (_groupedDurationChart) {
    _groupedDurationChart.destroy();
    _groupedDurationChart = null;
  }

  const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
  const gridColor = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)';
  const textColor = isDark ? '#9c9a92' : '#73726c';
  const labelColor = isDark ? '#c2c0b6' : '#3d3d3a';

  _groupedDurationChart = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: groups.map(group => group.label),
      datasets: [{
        label: `Average duration per ${periodLabel}`,
        data: groups.map(group => group.avgDurationMs),
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
              const group = groups[items[0].dataIndex];
              return group.detailLabel;
            },
            label(ctx) {
              return ` Avg duration: ${formatPeriodDuration(ctx.raw)}`;
            },
            afterLabel(ctx) {
              const group = groups[ctx.dataIndex];
              return ` Builds: ${group.buildCount}`;
            }
          },
          backgroundColor: isDark ? '#2c2c2a' : '#fff',
          titleColor: labelColor,
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
            font: { size: 10 },
            maxRotation: 0,
            minRotation: 0,
            autoSkip: true,
            maxTicksLimit: 8,
          }
        },
        y: {
          grid: { color: gridColor, drawTicks: false },
          border: { display: false },
          ticks: {
            color: textColor,
            font: { size: 10 },
            callback: value => formatPeriodDuration(Number(value)),
            maxTicksLimit: 6,
          }
        }
      },
      animation: { duration: 600, easing: 'easeOutQuart' }
    }
  });

  _pipelineGroupedDurationRenderSignature = renderSignature;
}

function updateDurationGroupingButtons() {
  document.querySelectorAll('.pipeline-duration-btn').forEach(button => {
    button.classList.toggle('active', button.dataset.group === _durationGrouping);
  });
}

function setDurationGrouping(grouping) {
  if (grouping !== 'week' && grouping !== 'month') return;
  _durationGrouping = grouping;
  updateDurationGroupingButtons();
  renderGroupedDurationChart(_durationSourceBuilds);
}

function buildCoverageGroups(points, grouping) {
  const grouped = new Map();
  const validPoints = (points || []).filter(point =>
    typeof point.coverage === 'number' &&
    point.timestamp
  );

  validPoints.forEach(point => {
    const key = buildGroupStartKey(point.timestamp, grouping);
    if (!key) return;
    if (!grouped.has(key)) {
      grouped.set(key, {
        key,
        startMs: getUserDateKeySortValue(key),
        sampleCount: 0,
        totalCoverage: 0,
      });
    }

    const group = grouped.get(key);
    group.sampleCount += 1;
    group.totalCoverage += point.coverage;
  });

  const groups = Array.from(grouped.values())
    .sort((a, b) => a.startMs - b.startMs)
    .map(group => {
      return {
        ...group,
        avgCoverage: Number((group.totalCoverage / group.sampleCount).toFixed(1)),
        label: formatGroupLabel(group.key, grouping),
        detailLabel: formatGroupDetailLabel(group.key, grouping),
      };
    });

  const overallAvg = validPoints.length
    ? Number((validPoints.reduce((sum, point) => sum + point.coverage, 0) / validPoints.length).toFixed(1))
    : null;

  return {
    groups,
    sampleCount: validPoints.length,
    overallAvg,
  };
}

function clearCoverageTrendChart(message = 'No coverage data available') {
  const canvas = document.getElementById('coverageTrendChart');
  const badge = document.getElementById('coverageAvgBadge');
  const summary = document.getElementById('coverageTrendSummary');
  if (!canvas) return;

  _pipelineCoverageRenderSignature = null;

  if (window._coverageChart) {
    window._coverageChart.destroy();
    window._coverageChart = null;
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
  if (badge) badge.textContent = 'Avg —%';
}

function updateCoverageGroupingButtons() {
  document.querySelectorAll('.pipeline-coverage-btn').forEach(button => {
    button.classList.toggle('active', button.dataset.coverageGroup === _coverageGrouping);
  });
}

function setCoverageGrouping(grouping) {
  if (grouping !== 'week' && grouping !== 'month') return;
  _coverageGrouping = grouping;
  updateCoverageGroupingButtons();
  renderCoverageTrend(_coverageSourcePoints);
}

function buildJunitGroups(points, grouping) {
  const grouped = new Map();
  const validPoints = (points || []).filter(point =>
    typeof point.total === 'number' &&
    point.timestamp
  );

  validPoints.forEach(point => {
    const key = buildGroupStartKey(point.timestamp, grouping);
    if (!key) return;
    if (!grouped.has(key)) {
      grouped.set(key, {
        key,
        startMs: getUserDateKeySortValue(key),
        sampleCount: 0,
        totalPassed: 0,
        totalFailed: 0,
        totalSkipped: 0,
        totalTests: 0,
      });
    }

    const group = grouped.get(key);
    group.sampleCount += 1;
    group.totalPassed += Number(point.passed || 0);
    group.totalFailed += Number(point.failed || 0);
    group.totalSkipped += Number(point.skipped || 0);
    group.totalTests += Number(point.total || 0);
  });

  const groups = Array.from(grouped.values())
    .sort((a, b) => a.startMs - b.startMs)
    .map(group => {
      return {
        ...group,
        avgPassed: Number((group.totalPassed / group.sampleCount).toFixed(1)),
        avgFailed: Number((group.totalFailed / group.sampleCount).toFixed(1)),
        avgSkipped: Number((group.totalSkipped / group.sampleCount).toFixed(1)),
        avgTotal: Number((group.totalTests / group.sampleCount).toFixed(1)),
        label: formatGroupLabel(group.key, grouping),
        detailLabel: formatGroupDetailLabel(group.key, grouping),
      };
    });

  const overallAvgTotal = validPoints.length
    ? Number((validPoints.reduce((sum, point) => sum + Number(point.total || 0), 0) / validPoints.length).toFixed(1))
    : null;

  return {
    groups,
    sampleCount: validPoints.length,
    overallAvgTotal,
  };
}

function clearJunitTrendChart(message = 'No JUnit data available') {
  const canvas = document.getElementById('junitTrendChart');
  const badge = document.getElementById('junitAvgBadge');
  const summary = document.getElementById('junitTrendSummary');
  if (!canvas) return;

  _pipelineJunitRenderSignature = null;

  if (window._junitChart) {
    window._junitChart.destroy();
    window._junitChart = null;
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
  if (badge) badge.textContent = 'Avg —';
}

function updateJunitGroupingButtons() {
  document.querySelectorAll('.pipeline-junit-btn').forEach(button => {
    button.classList.toggle('active', button.dataset.junitGroup === _junitGrouping);
  });
}

function setJunitGrouping(grouping) {
  if (grouping !== 'week' && grouping !== 'month') return;
  _junitGrouping = grouping;
  updateJunitGroupingButtons();
  renderJUnitTrend(_junitSourcePoints);
}

function buildTestsDurationGroups(points, grouping) {
  const grouped = new Map();
  const validPoints = (points || []).filter(point =>
    point?.result !== null &&
    typeof point.total_duration_ms === 'number' &&
    point.total_duration_ms > 0 &&
    point.timestamp
  );

  validPoints.forEach(point => {
    const key = buildGroupStartKey(point.timestamp, grouping);
    if (!key) return;
    if (!grouped.has(key)) {
      grouped.set(key, {
        key,
        startMs: getUserDateKeySortValue(key),
        sampleCount: 0,
        totalDurationMs: 0,
      });
    }

    const group = grouped.get(key);
    group.sampleCount += 1;
    group.totalDurationMs += point.total_duration_ms;
  });

  const groups = Array.from(grouped.values())
    .sort((a, b) => a.startMs - b.startMs)
    .map(group => {
      return {
        ...group,
        avgDurationMs: Math.round(group.totalDurationMs / group.sampleCount),
        label: formatGroupLabel(group.key, grouping),
        detailLabel: formatGroupDetailLabel(group.key, grouping),
      };
    });

  const overallAvgMs = validPoints.length
    ? Math.round(validPoints.reduce((sum, point) => sum + point.total_duration_ms, 0) / validPoints.length)
    : 0;

  return {
    groups,
    sampleCount: validPoints.length,
    overallAvgMs,
  };
}

function clearTestsDurationTrendChart(message = 'No tests duration data available') {
  const canvas = document.getElementById('testsDurationTrendChart');
  const badge = document.getElementById('testsDurationAvgBadge');
  const summary = document.getElementById('testsDurationSummary');
  if (!canvas) return;

  _pipelineTestsDurationTrendRenderSignature = null;

  if (window._testsDurationTrendChart) {
    window._testsDurationTrendChart.destroy();
    window._testsDurationTrendChart = null;
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
  if (badge) badge.textContent = 'Avg —';
}

function updateTestsDurationGroupingButtons() {
  document.querySelectorAll('.pipeline-tests-duration-btn').forEach(button => {
    button.classList.toggle('active', button.dataset.testsGroup === _testsDurationGrouping);
  });
}

function setTestsDurationGrouping(grouping) {
  if (grouping !== 'week' && grouping !== 'month') return;
  _testsDurationGrouping = grouping;
  updateTestsDurationGroupingButtons();
  renderTestsDurationTrend(_testsDurationSourcePoints);
}

function attachBuildTimestamps(points, builds) {
  const timestampByBuild = new Map(
    (Array.isArray(builds) ? builds : [])
      .filter(build => build?.number != null)
      .map(build => [
        build.number,
        build.timestamp ?? build.timestamp_ms ?? 0,
      ])
  );

  return (Array.isArray(points) ? points : []).map(point => {
    if (point?.timestamp) return point;
    const fallbackTimestamp = timestampByBuild.get(point?.number);
    return fallbackTimestamp ? { ...point, timestamp: fallbackTimestamp } : point;
  });
}

function renderCharts(branchData) {
  const summary = branchData.summary || {};
  const stages = branchData.stages || {};
  const quality = branchData.quality || {};
  const trends = branchData.trends || {};

  if (summary.avg_duration_seconds !== undefined) {
    const avgDur = summary.avg_duration_seconds || 0;
    const mins = Math.floor(avgDur / 60);
    const secs = avgDur % 60;
    const displayText = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
    const el = document.getElementById('avgDurationValue');
    if (el) el.textContent = displayText;
    const avgEl = document.getElementById('latestBuildsAvg');
    if (avgEl) avgEl.textContent = avgDur > 0 ? `Avg: ${displayText}` : 'Avg —';
  }

  if (quality.avg_test_coverage !== undefined) {
    const coverage = quality.avg_test_coverage;
    const el = document.getElementById('coverageValue');
    if (coverage === null || coverage === undefined) {
      if (el) el.textContent = '—';
    } else {
      if (el) el.textContent = coverage.toFixed(1);
    }
  }

  renderStageFailureChart(stages.failure_rate || {});

  if (Array.isArray(trends.coverage)) {
    renderCoverageTrend(trends.coverage);
  }

  if (Array.isArray(trends.junit)) {
    renderJUnitTrend(attachBuildTimestamps(trends.junit, branchData.builds || trends.builds || []));
  } else {
    clearJunitTrendChart();
  }

  if (Array.isArray(trends.tests_duration)) {
    renderTestsDurationTrend(trends.tests_duration);
    renderTestsDuration24hChart(trends.tests_duration);
  } else {
    clearTestsDurationTrendChart();
    clearTestsDuration24hChart();
  }
}

function renderStageFailureChart(failureRateByStage) {
  const container = document.getElementById('stageFailureChart');
  if (!container) return;

  const entries = Object.entries(failureRateByStage).sort((a, b) => b[1] - a[1]).slice(0, 3);
  const renderSignature = buildPipelineRenderSignature('stage-failure', {
    items: entries.map(([stage, rate]) => ({ stage, rate })),
    fields: ['stage', 'rate'],
  });

  if (!entries.length) {
    const emptySignature = `empty::${renderSignature}`;
    if (_pipelineStageFailureRenderSignature === emptySignature) return;
    clearStageFailureChart();
    _pipelineStageFailureRenderSignature = emptySignature;
    return;
  }

  if (_pipelineStageFailureRenderSignature === renderSignature) return;

  const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
  const gridColor = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)';
  const textColor = isDark ? '#9c9a92' : '#73726c';
  const labelColor = isDark ? '#c2c0b6' : '#3d3d3a';

  const labels = entries.map(e => e[0]);
  const values = entries.map(e => e[1]);

  const bgColors = values.map(() => '#c62828');
  const borderColors = values.map(() => '#c62828');

  container.innerHTML = `
    <div style="position:relative;width:100%;height:${Math.max(220, entries.length * 52 + 36)}px;">
      <canvas id="stageChartCanvas"></canvas>
    </div>`;

  if (window._stageChart) {
    window._stageChart.destroy();
    window._stageChart = null;
  }

  window._stageChart = new Chart(document.getElementById('stageChartCanvas'), {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: bgColors,
        borderColor: borderColors,
        borderWidth: 1,
        borderRadius: 5,
        borderSkipped: false
      }]
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: { label: ctx => ` ${ctx.raw.toFixed(1)}% failure rate` },
          backgroundColor: isDark ? '#2c2c2a' : '#fff',
          titleColor: labelColor,
          bodyColor: textColor,
          borderColor: isDark ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.08)',
          borderWidth: 0.5,
          padding: 10,
          cornerRadius: 8
        }
      },
      scales: {
        x: {
          min: 0,
          max: 100,
          grid: { color: gridColor, drawTicks: false },
          border: { display: false },
          ticks: {
            color: textColor,
            font: { size: 11 },
            callback: v => v + '%',
            stepSize: 25
          }
        },
        y: {
          grid: { display: false },
          border: { display: false },
          ticks: {
            color: labelColor,
            font: { size: 12, weight: '500' },
            padding: 6
          }
        }
      },
      animation: { duration: 600, easing: 'easeOutQuart' }
    }
  });

  _pipelineStageFailureRenderSignature = renderSignature;
}

function renderCoverageTrend(coverageTrend) {
  const canvas = document.getElementById('coverageTrendChart');
  if (!canvas) return;
  const container = canvas.parentElement;
  const subtitle = document.getElementById('coverageTrendSub');
  const badge = document.getElementById('coverageAvgBadge');
  const summary = document.getElementById('coverageTrendSummary');
  const periodLabel = _coverageGrouping === 'month' ? 'month' : 'week';
  const periodLabelPlural = _coverageGrouping === 'month' ? 'months' : 'weeks';

  _coverageSourcePoints = Array.isArray(coverageTrend) ? coverageTrend : [];
  const { groups, overallAvg } = buildCoverageGroups(_coverageSourcePoints, _coverageGrouping);
  const renderSignature = buildPipelineRenderSignature('coverage', {
    grouping: _coverageGrouping,
    items: _coverageSourcePoints,
    fields: ['number', 'timestamp', 'coverage'],
  });

  if (!groups.length) {
    const emptySignature = `empty::${renderSignature}`;
    if (_pipelineCoverageRenderSignature === emptySignature) return;
    if (subtitle) {
      subtitle.textContent = `Average coverage grouped by ${periodLabel}`;
    }
    clearCoverageTrendChart(`No coverage data available for ${periodLabel} grouping`);
    _pipelineCoverageRenderSignature = emptySignature;
    return;
  }

  if (_pipelineCoverageRenderSignature === renderSignature) return;

  if (subtitle) {
    subtitle.textContent = `Average coverage grouped by ${periodLabel}`;
  }

  canvas.style.display = 'block';
  const existingEmpty = container.querySelector('.chart-empty');
  if (existingEmpty) existingEmpty.remove();

  const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
  const gridColor = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)';
  const textColor = isDark ? '#9c9a92' : '#73726c';
  const lineColor = '#3ab8f8';
  const fillColor = 'rgba(58,184,248,0.18)';

  if (window._coverageChart) {
    window._coverageChart.destroy();
    window._coverageChart = null;
  }

  if (summary) {
    summary.innerHTML =
      `<span class="pipeline-duration-pill"><strong>${groups.length}</strong> ${periodLabelPlural}</span>` +
      `<span class="pipeline-duration-pill"><strong>${groups[groups.length - 1].avgCoverage.toFixed(1)}%</strong> latest ${periodLabel} avg</span>`;
  }
  if (badge) {
    badge.textContent = overallAvg === null ? 'Avg —%' : `Avg ${overallAvg.toFixed(1)}%`;
  }

  window._coverageChart = new Chart(canvas, {
    type: 'line',
    data: {
      labels: groups.map(group => group.label),
      datasets: [{
        data: groups.map(group => group.avgCoverage),
        borderColor: lineColor,
        backgroundColor: fillColor,
        fill: true,
        tension: 0.35,
        pointRadius: 3,
        pointHoverRadius: 4,
        pointBackgroundColor: lineColor,
        pointBorderWidth: 0
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
              const group = groups[items[0].dataIndex];
              return group.detailLabel;
            },
            label: ctx => ` Avg coverage: ${ctx.raw.toFixed(1)}%`,
          },
          backgroundColor: isDark ? '#2c2c2a' : '#fff',
          titleColor: isDark ? '#c2c0b6' : '#3d3d3a',
          bodyColor: textColor,
          borderColor: isDark ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.08)',
          borderWidth: 0.5,
          padding: 10,
          cornerRadius: 8
        }
      },
      scales: {
        x: {
          grid: { color: gridColor, drawTicks: false },
          border: { display: false },
          ticks: { color: textColor, font: { size: 10 }, maxTicksLimit: 8 }
        },
        y: {
          min: 0,
          max: 100,
          grid: { color: gridColor, drawTicks: false },
          border: { display: false },
          ticks: {
            color: textColor,
            font: { size: 10 },
            callback: v => v + '%',
            stepSize: 25
          }
        }
      },
      animation: { duration: 600, easing: 'easeOutQuart' }
    }
  });

  _pipelineCoverageRenderSignature = renderSignature;
}

function renderJUnitTrend(junitTrend) {
  const canvas = document.getElementById('junitTrendChart');
  if (!canvas) return;
  const container = canvas.parentElement;
  const subtitle = document.getElementById('junitTrendSub');
  const badge = document.getElementById('junitAvgBadge');
  const summary = document.getElementById('junitTrendSummary');
  const periodLabel = _junitGrouping === 'month' ? 'month' : 'week';
  const periodLabelPlural = _junitGrouping === 'month' ? 'months' : 'weeks';

  _junitSourcePoints = Array.isArray(junitTrend) ? junitTrend : [];
  const { groups, overallAvgTotal } = buildJunitGroups(_junitSourcePoints, _junitGrouping);
  const renderSignature = buildPipelineRenderSignature('junit', {
    grouping: _junitGrouping,
    items: _junitSourcePoints,
    fields: ['number', 'timestamp', 'total', 'passed', 'failed', 'skipped'],
  });

  if (!groups.length) {
    const emptySignature = `empty::${renderSignature}`;
    if (_pipelineJunitRenderSignature === emptySignature) return;
    if (subtitle) {
      subtitle.textContent = `Average passed, failed, and skipped grouped by ${periodLabel}`;
    }
    clearJunitTrendChart(`No unit test data available for ${periodLabel} grouping`);
    _pipelineJunitRenderSignature = emptySignature;
    return;
  }

  if (_pipelineJunitRenderSignature === renderSignature) return;

  if (subtitle) {
    subtitle.textContent = `Average passed, failed, and skipped grouped by ${periodLabel}`;
  }

  canvas.style.display = 'block';
  const existingEmpty = container.querySelector('.chart-empty');
  if (existingEmpty) existingEmpty.remove();

  const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
  const gridColor = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)';
  const textColor = isDark ? '#9c9a92' : '#73726c';

  if (window._junitChart) {
    window._junitChart.destroy();
    window._junitChart = null;
  }

  if (summary) {
    summary.innerHTML =
      `<span class="pipeline-duration-pill"><strong>${groups.length}</strong> ${periodLabelPlural}</span>` +
      `<span class="pipeline-duration-pill"><strong>${formatAverageCount(groups[groups.length - 1].avgTotal)}</strong> tests latest ${periodLabel} avg</span>`;
  }
  if (badge) {
    badge.textContent = overallAvgTotal === null ? 'Avg —' : `Avg ${formatAverageCount(overallAvgTotal)} tests`;
  }

  window._junitChart = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: groups.map(group => group.label),
      datasets: [
        {
          label: 'Passed',
          data: groups.map(group => group.avgPassed),
          backgroundColor: 'rgba(0,219,160,0.75)',
          borderRadius: 4,
          borderSkipped: false
        },
        {
          label: 'Failed',
          data: groups.map(group => group.avgFailed),
          backgroundColor: 'rgba(255,69,96,0.8)',
          borderRadius: 4,
          borderSkipped: false
        },
        {
          label: 'Skipped',
          data: groups.map(group => group.avgSkipped),
          backgroundColor: 'rgba(255,140,66,0.7)',
          borderRadius: 4,
          borderSkipped: false
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'bottom',
          labels: { color: textColor, boxWidth: 10, boxHeight: 10, padding: 12 }
        },
        tooltip: {
          callbacks: {
            title(items) {
              const group = groups[items[0].dataIndex];
              return group.detailLabel;
            },
            label: ctx => ` Avg ${ctx.dataset.label}: ${formatAverageCount(ctx.raw)}`,
          },
          backgroundColor: isDark ? '#2c2c2a' : '#fff',
          titleColor: isDark ? '#c2c0b6' : '#3d3d3a',
          bodyColor: textColor,
          borderColor: isDark ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.08)',
          borderWidth: 0.5,
          padding: 10,
          cornerRadius: 8
        }
      },
      scales: {
        x: {
          stacked: true,
          grid: { display: false },
          border: { display: false },
          ticks: { color: textColor, font: { size: 10 }, maxTicksLimit: 6 }
        },
        y: {
          stacked: true,
          grid: { color: gridColor, drawTicks: false },
          border: { display: false },
          ticks: {
            color: textColor,
            font: { size: 10 },
            callback: value => formatAverageCount(Number(value)),
          }
        }
      },
      animation: { duration: 600, easing: 'easeOutQuart' }
    }
  });

  _pipelineJunitRenderSignature = renderSignature;
}

function renderTestsDurationTrend(testsDurationTrend) {
  const canvas = document.getElementById('testsDurationTrendChart');
  if (!canvas) return;
  const container = canvas.parentElement;
  const subtitle = document.getElementById('testsDurationSub');
  const badge = document.getElementById('testsDurationAvgBadge');
  const summary = document.getElementById('testsDurationSummary');
  const periodLabel = _testsDurationGrouping === 'month' ? 'month' : 'week';
  const periodLabelPlural = _testsDurationGrouping === 'month' ? 'months' : 'weeks';

  _testsDurationSourcePoints = Array.isArray(testsDurationTrend) ? testsDurationTrend : [];
  const { groups, overallAvgMs } = buildTestsDurationGroups(
    _testsDurationSourcePoints,
    _testsDurationGrouping
  );
  const renderSignature = buildPipelineRenderSignature('tests-duration-trend', {
    grouping: _testsDurationGrouping,
    items: _testsDurationSourcePoints,
    fields: [
      'number',
      'result',
      'timestamp',
      'total_duration_ms',
      'unit_tests_ms',
      'pylint_ms',
      'sonarcloud_ms',
    ],
  });

  if (!groups.length) {
    const emptySignature = `empty::${renderSignature}`;
    if (_pipelineTestsDurationTrendRenderSignature === emptySignature) return;
    if (subtitle) {
      subtitle.textContent = `Average tests duration grouped by ${periodLabel}`;
    }
    clearTestsDurationTrendChart(`No tests duration data available for ${periodLabel} grouping`);
    _pipelineTestsDurationTrendRenderSignature = emptySignature;
    return;
  }

  if (_pipelineTestsDurationTrendRenderSignature === renderSignature) return;

  if (subtitle) {
    subtitle.textContent = `Average tests duration grouped by ${periodLabel}`;
  }

  canvas.style.display = 'block';
  const existingEmpty = container.querySelector('.chart-empty');
  if (existingEmpty) existingEmpty.remove();

  const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
  const gridColor = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)';
  const textColor = isDark ? '#9c9a92' : '#73726c';
  const lineColor = '#3ab8f8';
  const fillColor = 'rgba(58,184,248,0.18)';

  if (window._testsDurationTrendChart) {
    window._testsDurationTrendChart.destroy();
    window._testsDurationTrendChart = null;
  }

  if (summary) {
    summary.innerHTML =
      `<span class="pipeline-duration-pill"><strong>${groups.length}</strong> ${periodLabelPlural}</span>` +
      `<span class="pipeline-duration-pill"><strong>${formatPeriodDuration(groups[groups.length - 1].avgDurationMs)}</strong> latest ${periodLabel} avg</span>`;
  }
  if (badge) {
    badge.textContent = overallAvgMs > 0 ? `Avg ${formatPeriodDuration(overallAvgMs)}` : 'Avg —';
  }

  window._testsDurationTrendChart = new Chart(canvas, {
    type: 'line',
    data: {
      labels: groups.map(group => group.label),
      datasets: [{
        data: groups.map(group => group.avgDurationMs),
        borderColor: lineColor,
        backgroundColor: fillColor,
        fill: true,
        tension: 0.35,
        pointRadius: 3,
        pointHoverRadius: 4,
        pointBackgroundColor: lineColor,
        pointBorderWidth: 0
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
              const group = groups[items[0].dataIndex];
              return group.detailLabel;
            },
            label: ctx => ` Avg tests duration: ${formatPeriodDuration(ctx.raw)}`,
          },
          backgroundColor: isDark ? '#2c2c2a' : '#fff',
          titleColor: isDark ? '#c2c0b6' : '#3d3d3a',
          bodyColor: textColor,
          borderColor: isDark ? 'rgba(255,255,255,0.1)' : 'rgba(0,0,0,0.08)',
          borderWidth: 0.5,
          padding: 10,
          cornerRadius: 8
        }
      },
      scales: {
        x: {
          grid: { color: gridColor, drawTicks: false },
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
            callback: value => formatPeriodDuration(Number(value)),
            maxTicksLimit: 6,
          }
        }
      },
      animation: { duration: 600, easing: 'easeOutQuart' }
    }
  });

  _pipelineTestsDurationTrendRenderSignature = renderSignature;
}

function clearTestsDuration24hChart(message = 'No tests duration data available in the last 24 hours') {
  const canvas = document.getElementById('testsDuration24hChart');
  const badge = document.getElementById('testsDuration24hBadge');
  const summary = document.getElementById('testsDuration24hSummary');
  if (!canvas) return;

  _pipelineTestsDuration24hRenderSignature = null;

  if (window._testsDuration24hChart) {
    window._testsDuration24hChart.destroy();
    window._testsDuration24hChart = null;
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

function renderTestsDuration24hChart(testsDurationTrend) {
  const canvas = document.getElementById('testsDuration24hChart');
  if (!canvas) return;
  const container = canvas.parentElement;
  const badge = document.getElementById('testsDuration24hBadge');
  const summary = document.getElementById('testsDuration24hSummary');

  const cutoffMs = Date.now() - (24 * 60 * 60 * 1000);
  const points = (Array.isArray(testsDurationTrend) ? testsDurationTrend : [])
    .filter(point =>
      point?.result !== null &&
      typeof point.total_duration_ms === 'number' &&
      point.total_duration_ms > 0 &&
      (point.timestamp || 0) >= cutoffMs
    )
    .sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));
  const renderSignature = buildPipelineRenderSignature('tests-duration-24h', {
    items: points,
    fields: [
      'number',
      'result',
      'timestamp',
      'total_duration_ms',
      'unit_tests_ms',
      'pylint_ms',
      'sonarcloud_ms',
    ],
  });

  if (!points.length) {
    const emptySignature = `empty::${renderSignature}`;
    if (_pipelineTestsDuration24hRenderSignature === emptySignature) return;
    clearTestsDuration24hChart();
    _pipelineTestsDuration24hRenderSignature = emptySignature;
    return;
  }

  if (_pipelineTestsDuration24hRenderSignature === renderSignature) return;

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
      `<span class="pipeline-duration-pill"><strong>${points.length}</strong> builds in 24h</span>` +
      `<span class="pipeline-duration-pill"><strong>${formatPeriodDuration(latestPoint.total_duration_ms)}</strong> latest build</span>` +
      `<span class="pipeline-duration-pill"><strong>${formatPeriodDuration(maxDurationMs)}</strong> peak duration</span>`;
  }
  if (badge) {
    badge.textContent = `24h Avg ${formatPeriodDuration(avgDurationMs)}`;
  }

  const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
  const gridColor = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)';
  const textColor = isDark ? '#9c9a92' : '#73726c';

  if (window._testsDuration24hChart) {
    window._testsDuration24hChart.destroy();
    window._testsDuration24hChart = null;
  }

  window._testsDuration24hChart = new Chart(canvas, {
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
              return ` Total tests duration: ${formatPeriodDuration(ctx.raw)}`;
            },
            afterLabel(ctx) {
              const point = points[ctx.dataIndex];
              return [
                ` Unit tests: ${formatPeriodDuration(point.unit_tests_ms || 0)}`,
                ` Pylint: ${formatPeriodDuration(point.pylint_ms || 0)}`,
                ` SonarCloud: ${formatPeriodDuration(point.sonarcloud_ms || 0)}`,
              ];
            }
          },
          backgroundColor: isDark ? '#2c2c2a' : '#fff',
          titleColor: isDark ? '#c2c0b6' : '#3d3d3a',
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
            callback: value => formatPeriodDuration(Number(value)),
            maxTicksLimit: 6,
          }
        }
      },
      animation: { duration: 600, easing: 'easeOutQuart' }
    }
  });

  _pipelineTestsDuration24hRenderSignature = renderSignature;
}

async function pollRunningStages() {
  try {
    const data = await (await fetch('/api/running_stages')).json();

    data.forEach(b => {
      const strip = document.querySelector('#brow-' + b.number + ' .stage-strip');
      const stages = Array.isArray(b.stages) ? b.stages : [];
      if (!strip || !stages.length) return;

      strip.innerHTML = stages.map(st => {
        const cls = segCls(st.status);
        const name = (st.name || 'Stage').replace(/"/g, '&quot;');
        const tipDur = fmtDur(st.duration_ms) || '';
        const tipSt = stageStatusText(st.status);

        return `<div class="seg ${cls}"
          data-name="${name}"
          data-dur="${tipDur}"
          data-stcls="${cls}"
          data-sttext="${tipSt}"
          onmouseenter="showSegTip(this,this.dataset.name,this.dataset.dur,this.dataset.stcls,this.dataset.sttext)"
          onmouseleave="hideSegTip()"
          onclick="event.stopPropagation();openConsole(${b.number})"></div>`;
      }).join('');
    });
  } catch (e) {}
}

async function loadPipelineKPIs({ refresh = false, wait = false } = {}) {
  if (_pipelineKpisLoadInFlight) return;
  _pipelineKpisLoadInFlight = true;

  try {
    const baseUrl = document.body.dataset.pipelineKpisUrl;
    const url = refresh
      ? `${baseUrl}?refresh=1${wait ? '&wait=1' : ''}`
      : baseUrl;
    const data = await (await fetch(url)).json();
    const branchData = getSelectedBranchPayload(data);
    const summary = branchData.summary || {};
    const builds = branchData.builds || [];

    if (!data.connected) {
      _prevRunningBuildNumbers = new Set();
      stopPipelineKpiBurstRefresh();
      resetPipelineChartRenderCache();
      if (typeof clearStatRow === 'function') clearStatRow();
      clearGroupedDurationChart('No build data available');
      clearStageFailureChart('No stage data available');
      clearTestsDurationTrendChart('No build data available');
      clearTestsDuration24hChart('No build data available');

      return;
    }

    if (!builds.length) {
      _prevRunningBuildNumbers = new Set();
      stopPipelineKpiBurstRefresh();
      return;
    }

    if (typeof updateStatRow === 'function') {
      updateStatRow(summary);
    }

    if (summary.avg_duration_ms) {
      _avgDurationMs = summary.avg_duration_ms;
    } else {
      const durs = builds
        .filter(b => b.result && b.duration_seconds > 0)
        .map(b => b.duration_ms || (b.duration_seconds * 1000));
      if (durs.length) {
        _avgDurationMs = Math.round(durs.reduce((a, b) => a + b, 0) / durs.length);
      }
    }

    const finished = builds.filter(b => b.result !== null);
    const success = finished.filter(b => b.result === 'SUCCESS').length;
    const rate = summary.success_rate ?? (finished.length > 0 ? Math.round(success / finished.length * 100) : 0);

    updateCircle('healthCircle', 'health-val', 'health-badge', summary.health_score || 0);
    updateCircle('rateCircle', 'rate-val', 'rate-badge', rate);

    const latestBuildTag = document.getElementById('latestBuildTag');
    if (latestBuildTag && summary.last_build_number) {
      latestBuildTag.textContent = '#' + summary.last_build_number;
    }

    _durationSourceBuilds = builds.map(b => ({
      ...b,
      duration: b.duration_ms ?? ((b.duration_seconds ?? 0) * 1000),
    }));
    renderGroupedDurationChart(_durationSourceBuilds);
    /*
    Legacy latest-builds + timeline rendering kept for later rollback:
    _allBuilds = builds.map(b => ({
      ...b,
      duration: b.duration_ms ?? ((b.duration_seconds ?? 0) * 1000),
    }));
    if (typeof renderLatestBuildsChart === 'function') {
      const trendFinished = trendBuilds.filter(b => b.result !== null).map(b => ({
        ...b,
        duration: b.duration_ms ?? ((b.duration_seconds ?? 0) * 1000),
      }));
      renderLatestBuildsChart(trendFinished);
    }
    renderTimeline();
    */
    renderCharts(branchData);

    const currentRunningBuildNumbers = new Set(
      builds
        .filter(b => b.result === null)
        .map(b => b.number)
    );
    const buildJustFinished = Array.from(_prevRunningBuildNumbers)
      .some(number => !currentRunningBuildNumbers.has(number));
    _prevRunningBuildNumbers = currentRunningBuildNumbers;

    const hasRunning = currentRunningBuildNumbers.size > 0;
    if (_pollHandle) {
      clearInterval(_pollHandle);
      _pollHandle = null;
    }

    if (hasRunning) {
      stopPipelineKpiBurstRefresh();
    } else if (buildJustFinished) {
      schedulePipelineKpiBurstRefresh();
    }
    /*
    Legacy running-stage polling kept for later rollback:
    if (hasRunning && !_stagesHandle) {
      _stagesHandle = setInterval(pollRunningStages, 2000);
    } else if (!hasRunning && _stagesHandle) {
      clearInterval(_stagesHandle);
      _stagesHandle = null;
    }
    */
  } catch (e) {
    console.error('Pipeline KPI error:', e);
  } finally {
    _pipelineKpisLoadInFlight = false;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const hasLiveStream = connectPipelineLiveStream();
  const btn = document.getElementById('startStopBtn');
  if (btn) {
    btn.removeAttribute('onclick');
    btn.addEventListener('click', triggerBuild);
  }

  document.querySelectorAll('.pipeline-duration-btn').forEach(button => {
    button.addEventListener('click', () => setDurationGrouping(button.dataset.group));
  });
  updateDurationGroupingButtons();

  document.querySelectorAll('.pipeline-coverage-btn').forEach(button => {
    button.addEventListener('click', () => setCoverageGrouping(button.dataset.coverageGroup));
  });
  updateCoverageGroupingButtons();

  document.querySelectorAll('.pipeline-junit-btn').forEach(button => {
    button.addEventListener('click', () => setJunitGrouping(button.dataset.junitGroup));
  });
  updateJunitGroupingButtons();

  document.querySelectorAll('.pipeline-tests-duration-btn').forEach(button => {
    button.addEventListener('click', () => setTestsDurationGrouping(button.dataset.testsGroup));
  });
  updateTestsDurationGroupingButtons();

  loadPipelineKPIs();
  if (!hasLiveStream) {
    console.warn('Pipeline live SSE stream is unavailable. REST fallback polling is disabled.');
  }
});

window.addEventListener('beforeunload', closePipelineLiveStream);


// ── VM metrics polling ────────────────────────────────────────────────────────
const VM_METRICS_URL = document.body.dataset.vmMetricsUrl || '';
let vmCpuChart = null;
let vmRamChart = null;
let vmNetChart = null;
let vmDiskChart = null;

function getLatestSeriesValue(points, digits = 1) {
  if (!Array.isArray(points) || !points.length) return null;
  for (let i = points.length - 1; i >= 0; i -= 1) {
    const num = Number(points[i]?.[1]);
    if (Number.isFinite(num)) return Number(num.toFixed(digits));
  }
  return null;
}

function buildVmCpuSeries(cpuCoreHistory, fallbackHistory) {
  const palette = ['#5cb85c', '#3ab8f8', '#ff9f43', '#ff4560', '#7c6fff', '#00dba0'];
  const entries = Object.entries(cpuCoreHistory || {})
    .filter(([, points]) => Array.isArray(points) && points.length)
    .sort((a, b) => Number(a[0]) - Number(b[0]));

  if (entries.length) {
    const labels = entries[0][1].map(([ts]) => ts);
    const datasets = entries.map(([cpu, points], index) => ({
      label: `CPU ${cpu}`,
      values: points.map(([, v]) => parseFloat(Number(v).toFixed(1))),
      color: palette[index % palette.length],
    }));
    const latestValues = entries
      .map(([cpu, points]) => ({ cpu, value: getLatestSeriesValue(points, 1) }))
      .filter(item => item.value !== null);

    return { labels, datasets, latestValues };
  }

  if (!Array.isArray(fallbackHistory) || !fallbackHistory.length) return null;

  return {
    labels: fallbackHistory.map(([ts]) => ts),
    datasets: [{
      label: 'CPU',
      values: fallbackHistory.map(([, v]) => parseFloat(Number(v).toFixed(1))),
      color: '#5cb85c',
      fillArea: true,
    }],
    latestValues: [],
  };
}

function formatVmCpuBadge(latestValues) {
  if (!Array.isArray(latestValues) || !latestValues.length) return 'Now —%';
  return latestValues
    .map(({ cpu, value }) => `CPU ${cpu} ${value}%`)
    .join(' · ');
}

function renderVmLineChart(canvasId, series, chartRef, opts = {}) {
  const ctx = document.getElementById(canvasId)?.getContext('2d');
  if (!ctx) return chartRef;
  if (chartRef) chartRef.destroy();

  const labels = (series?.labels || []).map(formatTimeLabel);
  const datasets = (series?.datasets || []).map(ds => ({
    label: ds.label,
    data: ds.values,
    borderColor: ds.color,
    backgroundColor: ds.fill || `${ds.color}22`,
    fill: !!ds.fillArea
  }));

  const styledDatasets = applyLineDefaults(datasets, { tension: 0.25 });

  return buildLineChart(ctx, labels, styledDatasets, {
    unit: opts.unit || '',
    min: opts.min ?? 0,
    max: opts.max ?? undefined,
    maxTicksLimit: 10
  });
}

async function loadVmMetrics() {
  if (!VM_METRICS_URL || !hasVmMetricCharts()) return;
  try {
    const res = await fetch(VM_METRICS_URL);
    const d = await res.json();
    if (!d.connected) return;

    if (d.cpu_history?.length) {
      const cpuSeries = buildVmCpuSeries(d.cpu_core_history, d.cpu_history);
      const badge = document.getElementById('vmCpuBadge');
      if (!cpuSeries) {
        if (badge) badge.textContent = 'Now —%';
      } else {
        if (badge) {
          badge.textContent = cpuSeries.latestValues.length
            ? formatVmCpuBadge(cpuSeries.latestValues)
            : 'Now —%';
        }
        vmCpuChart = renderVmLineChart(
          'vmCpuChart',
          { labels: cpuSeries.labels, datasets: cpuSeries.datasets },
          vmCpuChart,
          { unit: '%', min: 0, max: 100 }
        );
      }
    }

    if (d.ram_history?.length) {
      const labels = d.ram_history.map(([ts]) => ts);
      const values = d.ram_history.map(([, v]) => parseFloat(v.toFixed(1)));
      const badge = document.getElementById('vmRamBadge');
      const avgRam = avgValue(values, 1);
      if (badge) badge.textContent = avgRam ? `Avg ${avgRam}%` : 'Avg —%';
      vmRamChart = renderVmLineChart(
        'vmRamChart',
        { labels, datasets: [{ label: 'RAM', values, color: '#3ab8f8', fillArea: true }] },
        vmRamChart,
        { unit: '%', min: 0, max: 100 }
      );
    }

    if (d.net_rx_history?.length || d.net_tx_history?.length) {
      const labels = (d.net_rx_history?.length ? d.net_rx_history : d.net_tx_history).map(([ts]) => ts);
      const rxValues = (d.net_rx_history || []).map(([, v]) => parseFloat(v.toFixed(2)));
      const txValues = (d.net_tx_history || []).map(([, v]) => parseFloat(v.toFixed(2)));
      const badge = document.getElementById('vmNetBadge');
      const combined = rxValues.map((v, i) => v + (txValues[i] || 0));
      const avgNet = avgValue(combined, 2);
      if (badge) badge.textContent = avgNet ? `Avg ${avgNet} MB/s` : 'Avg — MB/s';
      vmNetChart = renderVmLineChart(
        'vmNetChart',
        {
          labels,
          datasets: [
            { label: 'RX', values: rxValues, color: '#5cb85c' },
            { label: 'TX', values: txValues, color: '#ff9f43' }
          ]
        },
        vmNetChart,
        { unit: ' MB/s', min: 0 }
      );
    }

    if (d.disk_used_pct_history?.length) {
      const labels = d.disk_used_pct_history.map(([ts]) => ts);
      const values = d.disk_used_pct_history.map(([, v]) => parseFloat(v.toFixed(1)));
      const badge = document.getElementById('vmDiskBadge');
      const avgDisk = avgValue(values, 1);
      if (badge) badge.textContent = avgDisk ? `Avg ${avgDisk}%` : 'Avg —%';
      vmDiskChart = renderVmLineChart(
        'vmDiskChart',
        { labels, datasets: [{ label: 'Disk Used', values, color: '#ff9f43', fillArea: true }] },
        vmDiskChart,
        { unit: '%', min: 0, max: 100 }
      );
    }
  } catch (e) {
    console.warn('VM metrics fetch failed', e);
  }
}

function hasVmMetricCharts() {
  return Boolean(
    document.getElementById('vmCpuChart') ||
    document.getElementById('vmRamChart') ||
    document.getElementById('vmNetChart') ||
    document.getElementById('vmDiskChart')
  );
}

if (VM_METRICS_URL && hasVmMetricCharts()) {
  loadVmMetrics();
  setInterval(loadVmMetrics, 30_000);
}
