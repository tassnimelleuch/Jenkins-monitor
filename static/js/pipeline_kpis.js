const INITIAL_SHOW = 5;
const POLL_MS = 5000;
const SLOW_POLL_MS = 30000;

let _allBuilds = [];
let _showingAll = false;
let _avgDurationMs = 120000;
let _activeTimers = {};
let _pollHandle = null;
let _slowHandle = null;

// ── TOOLTIP
const _tip = document.getElementById('segTip');

function showSegTip(el, name, dur, stcls, sttext) {
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

  badge.style.display = running.length ? 'inline-flex' : 'none';
  if (running.length) badge.textContent = '● ' + running.length + ' running';

  const finishedToShow = _showingAll ? finished : finished.slice(0, INITIAL_SHOW);
  const toRender = [...running, ...finishedToShow];

  container.innerHTML = toRender.length
    ? toRender.map(buildRowHtml).join('')
    : '<div class="tl-empty">No builds found.</div>';

  if (finished.length > INITIAL_SHOW) {
    btn.style.display = 'block';
    btn.textContent = _showingAll
      ? 'Show less ↑'
      : 'Show more ↓  (' + (finished.length - INITIAL_SHOW) + ' more)';
  } else {
    btn.style.display = 'none';
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
  showConfirm(
    '▶ Start Build',
    'Trigger a new build for <strong>django-pipeline</strong>?',
    async () => {
      try {
        const { data } = await apiTriggerBuild();

        if (data.queued) {
          showToast('✅ Build queued — watching for updates');
          if (!_pollHandle) _pollHandle = setInterval(loadPipelineKPIs, POLL_MS);
          setTimeout(loadPipelineKPIs, 2000);
        } else {
          showToast('❌ ' + (data.error || 'Failed to trigger'), 'abort-toast');
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

function renderCharts(data) {
  if (data.avg_duration_seconds !== undefined) {
    const avgDur = data.avg_duration_seconds || 0;
    const mins = Math.floor(avgDur / 60);
    const secs = avgDur % 60;
    const displayText = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
    const el = document.getElementById('avgDurationValue');
    if (el) el.textContent = displayText;
  }

  if (data.avg_test_coverage !== undefined) {
    const coverage = data.avg_test_coverage || 0;
    const el = document.getElementById('coverageValue');
    if (el) el.textContent = coverage.toFixed(1);
  }

  if (data.failure_rate_by_stage && Object.keys(data.failure_rate_by_stage).length > 0) {
    renderStageFailureChart(data.failure_rate_by_stage);
  }
}

function renderStageFailureChart(failureRateByStage) {
  const container = document.getElementById('stageFailureChart');
  if (!container) return;

  const entries = Object.entries(failureRateByStage).sort((a, b) => b[1] - a[1]).slice(0, 8);

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

  const bgColors = values.map(v => v > 50 ? 'rgba(226,75,74,0.75)' : v > 25 ? 'rgba(186,117,23,0.75)' : 'rgba(99,153,34,0.75)');
  const borderColors = values.map(v => v > 50 ? '#E24B4A' : v > 25 ? '#BA7517' : '#639922');

  container.innerHTML = `
    <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:12px;font-size:11px;color:var(--text2);">
      <span style="display:flex;align-items:center;gap:5px;"><span style="width:10px;height:10px;border-radius:2px;background:#E24B4A;display:inline-block;"></span>High (&gt;50%)</span>
      <span style="display:flex;align-items:center;gap:5px;"><span style="width:10px;height:10px;border-radius:2px;background:#BA7517;display:inline-block;"></span>Medium (25–50%)</span>
      <span style="display:flex;align-items:center;gap:5px;"><span style="width:10px;height:10px;border-radius:2px;background:#639922;display:inline-block;"></span>Low (&lt;25%)</span>
    </div>
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
}

async function pollRunningStages() {
  try {
    const data = await (await fetch('/jenkins/api/running_stages')).json();

    data.forEach(b => {
      const strip = document.querySelector('#brow-' + b.number + ' .stage-strip');
      if (!strip || !b.stages.length) return;

      strip.innerHTML = b.stages.map(st => {
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

    if (!data.connected || !data.builds || !data.builds.length) {
      document.getElementById('buildTimeline').innerHTML =
        '<div class="tl-empty">No build data — check Jenkins connection.</div>';
      return;
    }

    const durs = data.builds.filter(b => b.result && b.duration > 0).map(b => b.duration);
    if (durs.length) {
      _avgDurationMs = Math.round(durs.reduce((a, b) => a + b, 0) / durs.length);
    }

    const finished = data.builds.filter(b => b.result !== null);
    const success = finished.filter(b => b.result === 'SUCCESS').length;
    const rate = finished.length > 0 ? Math.round(success / finished.length * 100) : 0;

    updateCircle('healthCircle', 'health-val', 'health-badge', data.health_score || 0);
    updateCircle('rateCircle', 'rate-val', 'rate-badge', rate);

    _allBuilds = data.builds;
    if (typeof renderLatestBuildsChart === 'function' && typeof getOverviewKpis === 'function') {
      const kpis = await getOverviewKpis();
      if (kpis && kpis.connected) {
        const trendFinished = (kpis.build_trend || []).filter(b => b.result !== null);
        renderLatestBuildsChart(trendFinished);
      }
    }
    renderTimeline();
    renderCharts(data);

    const hasRunning = data.builds.some(b => b.result === null);
    if (hasRunning && !_pollHandle) {
      _pollHandle = setInterval(loadPipelineKPIs, POLL_MS);
    } else if (!hasRunning && _pollHandle) {
      clearInterval(_pollHandle);
      _pollHandle = null;
    }
  } catch (e) {
    console.error('Pipeline KPI error:', e);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  checkStatus();
  loadLatestBuild();

  const btn = document.getElementById('startStopBtn');
  if (btn) {
    btn.removeAttribute('onclick');
    btn.addEventListener('click', triggerBuild);
  }

  _slowHandle = setInterval(() => {
    if (!_pollHandle) loadPipelineKPIs();
    if (typeof loadStatRow === 'function') loadStatRow();
  }, SLOW_POLL_MS);

  setInterval(pollRunningStages, 2000);
  if (typeof loadStatRow === 'function') loadStatRow();
  loadPipelineKPIs();
});
