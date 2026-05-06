const INITIAL_SHOW = 5;
const POLL_MS = 5000;
const SLOW_POLL_MS = 30000;

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
  if (!ts) return '';
  const d = new Date(ts);
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) +
         ' ' +
         d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
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

  const resultCell = isRunning
    ? `<div>
         <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
           <span class="br-result run">${pipelineResultLabel(b.result)}</span>
           <button class="br-abort" onclick="event.stopPropagation();confirmAbort(${b.number})" title="Abort build #${b.number}">
             <svg viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
           </button>
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
  triggerBuildWithConfirmation({
    bodyHtml: `Trigger a new build for ${pipelineStrongLabel()} on <strong>${escapeHtml(getBranchName())}</strong>?`,
    queuedMessage: '✅ Build queued — watching for updates',
    triggerErrorMessage: 'Failed to trigger',
    onQueued() {
      if (!_pollHandle) _pollHandle = setInterval(loadPipelineKPIs, POLL_MS);
      setTimeout(loadPipelineKPIs, 2000);
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

function buildDurationMs(build) {
  return build.duration_ms ?? build.duration ?? ((build.duration_seconds ?? 0) * 1000);
}

function startOfWeek(date) {
  const d = new Date(date);
  d.setHours(0, 0, 0, 0);
  const day = d.getDay();
  const diff = day === 0 ? -6 : 1 - day;
  d.setDate(d.getDate() + diff);
  return d;
}

function endOfWeek(start) {
  const d = new Date(start);
  d.setDate(d.getDate() + 6);
  return d;
}

function formatShortDate(date) {
  return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

function formatMonthLabel(date) {
  return date.toLocaleDateString([], { month: 'short', year: 'numeric' });
}

function buildGroupKey(date) {
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, '0'),
    String(date.getDate()).padStart(2, '0'),
  ].join('-');
}

function buildDurationGroups(builds, grouping) {
  const grouped = new Map();
  const finished = (builds || []).filter(build =>
    build.result !== null &&
    build.timestamp &&
    buildDurationMs(build) > 0
  );

  finished.forEach(build => {
    const buildDate = new Date(build.timestamp);
    const start = grouping === 'month'
      ? new Date(buildDate.getFullYear(), buildDate.getMonth(), 1)
      : startOfWeek(buildDate);
    start.setHours(0, 0, 0, 0);

    const key = buildGroupKey(start);
    if (!grouped.has(key)) {
      grouped.set(key, {
        key,
        startMs: start.getTime(),
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
      const start = new Date(group.startMs);
      const avgDurationMs = Math.round(group.totalDurationMs / group.buildCount);
      const end = grouping === 'month'
        ? new Date(start.getFullYear(), start.getMonth() + 1, 0)
        : endOfWeek(start);

      return {
        ...group,
        avgDurationMs,
        label: grouping === 'month' ? formatMonthLabel(start) : formatShortDate(start),
        detailLabel: grouping === 'month'
          ? formatMonthLabel(start)
          : `${formatShortDate(start)} - ${end.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' })}`,
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

function renderGroupedDurationChart(builds) {
  const canvas = document.getElementById('pipelineGroupedDurationChart');
  if (!canvas) return;
  const container = canvas.parentElement;
  const summary = document.getElementById('pipelineDurationSummary');
  const subtitle = document.getElementById('pipelineDurationSub');
  const avgBadge = document.getElementById('latestBuildsAvg');

  const existingEmpty = container.querySelector('.chart-empty');
  if (existingEmpty) existingEmpty.remove();

  const { groups, finishedBuildCount, overallAvgMs } = buildDurationGroups(builds, _durationGrouping);
  const periodLabel = _durationGrouping === 'month' ? 'month' : 'week';
  const periodLabelPlural = _durationGrouping === 'month' ? 'months' : 'weeks';

  if (subtitle) {
    subtitle.textContent = `Average duration grouped by ${periodLabel}`;
  }

  if (!groups.length) {
    clearGroupedDurationChart(`No finished builds available for ${periodLabel} grouping`);
    return;
  }

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
    const pointDate = new Date(point.timestamp);
    const start = grouping === 'month'
      ? new Date(pointDate.getFullYear(), pointDate.getMonth(), 1)
      : startOfWeek(pointDate);
    start.setHours(0, 0, 0, 0);

    const key = buildGroupKey(start);
    if (!grouped.has(key)) {
      grouped.set(key, {
        key,
        startMs: start.getTime(),
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
      const start = new Date(group.startMs);
      const end = grouping === 'month'
        ? new Date(start.getFullYear(), start.getMonth() + 1, 0)
        : endOfWeek(start);

      return {
        ...group,
        avgCoverage: Number((group.totalCoverage / group.sampleCount).toFixed(1)),
        label: grouping === 'month' ? formatMonthLabel(start) : formatShortDate(start),
        detailLabel: grouping === 'month'
          ? formatMonthLabel(start)
          : `${formatShortDate(start)} - ${end.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' })}`,
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
    const badge = document.getElementById('coverageAvgBadge');
    if (coverage === null || coverage === undefined) {
      if (el) el.textContent = '—';
      if (badge) badge.textContent = 'Avg —%';
    } else {
      if (el) el.textContent = coverage.toFixed(1);
      if (badge) badge.textContent = `Avg ${coverage.toFixed(1)}%`;
    }
  }

  if (stages.failure_rate && Object.keys(stages.failure_rate).length > 0) {
    renderStageFailureChart(stages.failure_rate);
  }

  if (Array.isArray(trends.coverage)) {
    renderCoverageTrend(trends.coverage);
  }

  if (Array.isArray(trends.junit)) {
    renderJUnitTrend(trends.junit);
  }
}

function renderStageFailureChart(failureRateByStage) {
  const container = document.getElementById('stageFailureChart');
  if (!container) return;

  const entries = Object.entries(failureRateByStage).sort((a, b) => b[1] - a[1]).slice(0, 5);

  if (!entries.length) {
    container.innerHTML = '<div style="text-align:center;color:var(--text2);padding:20px;font-size:12px;">No stage data available</div>';
    return;
  }

  const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
  const gridColor = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)';
  const textColor = isDark ? '#9c9a92' : '#73726c';
  const labelColor = isDark ? '#c2c0b6' : '#3d3d3a';

  const labels = entries.map(e => e[0]);
  const values = entries.map(e => e[1]);

  const bgColors = values.map(v => (v > 50 ? '#ff4560' : v > 25 ? '#ff9f43' : '#00dba0'));
  const borderColors = values.map(v => (v > 50 ? '#ff4560' : v > 25 ? '#ff9f43' : '#00dba0'));

  container.innerHTML = `

    <div style="position:relative;width:100%;height:${Math.max(180, entries.length * 40 + 40)}px;">
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
          max: 50,
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
  const { groups, sampleCount, overallAvg } = buildCoverageGroups(_coverageSourcePoints, _coverageGrouping);

  if (subtitle) {
    subtitle.textContent = `Average coverage grouped by ${periodLabel}`;
  }

  if (!groups.length) {
    clearCoverageTrendChart(`No coverage data available for ${periodLabel} grouping`);
    return;
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
      `<span class="pipeline-duration-pill"><strong>${sampleCount}</strong> coverage points</span>` +
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
            afterLabel(ctx) {
              const group = groups[ctx.dataIndex];
              return ` Builds: ${group.sampleCount}`;
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
}

function renderJUnitTrend(junitTrend) {
  const canvas = document.getElementById('junitTrendChart');
  if (!canvas) return;
  const container = canvas.parentElement;

  const points = junitTrend
    .filter(p => typeof p.total === 'number')
    .map(p => ({
      label: `#${p.number}`,
      passed: p.passed || 0,
      failed: p.failed || 0,
      skipped: p.skipped || 0
    }));

  if (!points.length) {
    if (window._junitChart) {
      window._junitChart.destroy();
      window._junitChart = null;
    }
    canvas.style.display = 'none';
    if (!container.querySelector('.chart-empty')) {
      const empty = document.createElement('div');
      empty.className = 'chart-empty';
      empty.textContent = 'No JUnit data available';
      container.appendChild(empty);
    }
    const totalBadge = document.getElementById('junitTotalBadge');
    if (totalBadge) totalBadge.textContent = 'Total —';
    return;
  }

  canvas.style.display = 'block';
  const existingEmpty = container.querySelector('.chart-empty');
  if (existingEmpty) existingEmpty.remove();

  const total = points.reduce((sum, p) => sum + p.passed + p.failed + p.skipped, 0);
  const totalBadge = document.getElementById('junitTotalBadge');
  if (totalBadge) totalBadge.textContent = `Total ${total}`;

  const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
  const gridColor = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)';
  const textColor = isDark ? '#9c9a92' : '#73726c';

  if (window._junitChart) {
    window._junitChart.destroy();
    window._junitChart = null;
  }

  window._junitChart = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: points.map(p => p.label),
      datasets: [
        {
          label: 'Passed',
          data: points.map(p => p.passed),
          backgroundColor: 'rgba(0,219,160,0.75)',
          borderRadius: 4,
          borderSkipped: false
        },
        {
          label: 'Failed',
          data: points.map(p => p.failed),
          backgroundColor: 'rgba(255,69,96,0.8)',
          borderRadius: 4,
          borderSkipped: false
        },
        {
          label: 'Skipped',
          data: points.map(p => p.skipped),
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
            label: ctx => ` ${ctx.dataset.label}: ${ctx.raw}`
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
          ticks: { color: textColor, font: { size: 10 } }
        }
      },
      animation: { duration: 600, easing: 'easeOutQuart' }
    }
  });
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

async function loadPipelineKPIs() {
  try {
    const url = document.body.dataset.pipelineKpisUrl;
    const data = await (await fetch(url)).json();
    const branchData = getSelectedBranchPayload(data);
    const summary = branchData.summary || {};
    const builds = branchData.builds || [];
    /*
    Legacy timeline/latest-builds path kept for later rollback:
    const trendBuilds = (branchData.trends || {}).builds || [];
    */

    if (!data.connected || !builds.length) {
      if (typeof clearStatRow === 'function') clearStatRow();
      clearGroupedDurationChart('No build data available');
      /*
      Legacy empty state:
      document.getElementById('buildTimeline').innerHTML =
        '<div class="tl-empty">No build data — check Jenkins connection.</div>';
      */
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

    const hasRunning = builds.some(b => b.result === null);
    if (hasRunning && !_pollHandle) {
      _pollHandle = setInterval(loadPipelineKPIs, POLL_MS);
    } else if (!hasRunning && _pollHandle) {
      clearInterval(_pollHandle);
      _pollHandle = null;
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
  }
}

document.addEventListener('DOMContentLoaded', () => {
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

  _slowHandle = setInterval(() => {
    if (!_pollHandle) loadPipelineKPIs();
  }, SLOW_POLL_MS);

  loadPipelineKPIs();
});


// ── VM metrics polling ────────────────────────────────────────────────────────
const VM_METRICS_URL = document.body.dataset.vmMetricsUrl || '/api/vm-metrics';
let vmCpuChart = null;
let vmRamChart = null;
let vmNetChart = null;
let vmDiskChart = null;

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
  try {
    const res = await fetch(VM_METRICS_URL);
    const d = await res.json();
    if (!d.connected) return;

    if (d.cpu_history?.length) {
      const labels = d.cpu_history.map(([ts]) => ts);
      const values = d.cpu_history.map(([, v]) => parseFloat(v.toFixed(1)));
      const badge = document.getElementById('vmCpuBadge');
      const avgCpu = avgValue(values, 1);
      if (badge) badge.textContent = avgCpu ? `Avg ${avgCpu}%` : 'Avg —%';
      vmCpuChart = renderVmLineChart(
        'vmCpuChart',
        { labels, datasets: [{ label: 'CPU', values, color: '#5cb85c', fillArea: true }] },
        vmCpuChart,
        { unit: '%', min: 0, max: 100 }
      );
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

// Poll every 30 s
loadVmMetrics();
setInterval(loadVmMetrics, 30_000);
