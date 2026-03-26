// ═══════════════════════════════════════
// static/js/sidebar_shared.js
// Shared by all dashboard pages
// ═══════════════════════════════════════

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
function doRefresh() {
  const b = document.getElementById('refBtn');
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
    const dot  = document.getElementById('statusDot');
    const val  = document.getElementById('statusVal');

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
    const dot = document.getElementById('statusDot');
    const val = document.getElementById('statusVal');
    if (dot) dot.classList.add('pulse-dot-error');
    if (val) {
      val.textContent = 'Unreachable';
      val.className   = 'ji-val error';
    }
    console.error('Jenkins status error:', e);
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
  loadLatestBuild();
});
