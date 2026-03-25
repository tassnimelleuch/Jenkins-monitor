// static/js/main.js

// Set correct theme icon on load and handle theme toggle
(function() {
  const saved = localStorage.getItem('jm-t') || 'dark';
  document.documentElement.setAttribute('data-theme', saved);
  
  // Update theme buttons when they exist
  const updateThemeButtons = () => {
    const current = document.documentElement.getAttribute('data-theme');
    document.querySelectorAll('.theme-btn, [data-theme-btn]').forEach(btn => {
      btn.textContent = current === 'dark' ? '☀️' : '🌙';
    });
  };
  
  updateThemeButtons();
  
  // Watch for DOM changes (for dynamic buttons)
  const observer = new MutationObserver(updateThemeButtons);
  observer.observe(document.body, { childList: true, subtree: true });
})();

// Theme toggle function
window.toggleTheme = function() {
  const r = document.documentElement;
  const dark = r.getAttribute('data-theme') === 'dark';
  const next = dark ? 'light' : 'dark';
  r.setAttribute('data-theme', next);
  localStorage.setItem('jm-t', next);
  
  document.querySelectorAll('.theme-btn, [data-theme-btn]').forEach(btn => {
    btn.textContent = next === 'dark' ? '☀️' : '🌙';
  });
};

// Toast notification
window.showToast = function(msg, cls) {
  let toast = document.getElementById('toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'toast';
    toast.className = 'toast';
    document.body.appendChild(toast);
  }
  toast.textContent = msg;
  toast.className = 'toast ' + (cls || '');
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 3500);
};

// Auto-dismiss flash messages
document.addEventListener('DOMContentLoaded', function() {
  document.querySelectorAll('.flash').forEach(el => {
    setTimeout(() => el.remove(), 4000);
  });
});