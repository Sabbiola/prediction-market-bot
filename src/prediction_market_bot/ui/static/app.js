/* ── PMBot Control Plane — app.js ─────────────────────── */
(function () {
  'use strict';

  /* ── SECTION / TAB NAVIGATION ─────────────────────────── */
  const sections = document.querySelectorAll('.section[data-section]');
  const navItems = document.querySelectorAll('.sidebar__nav-item[data-section]');
  const breadcrumb = document.getElementById('topbar-breadcrumb');
  const sidebar = document.getElementById('sidebar');
  const sidebarToggle = document.getElementById('sidebar-toggle');
  const mobileMenuBtn = document.getElementById('mobile-menu-btn');

  function activateSection(name) {
    sections.forEach(function (s) {
      s.classList.toggle('active', s.dataset.section === name);
    });
    navItems.forEach(function (btn) {
      var active = btn.dataset.section === name;
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-current', active ? 'true' : 'false');
    });
    if (breadcrumb) {
      var labels = {
        overview: 'Overview',
        scanner: 'Scanner Engine',
        research: 'Research Engine',
        prediction: 'Prediction Engine',
        risk: 'Risk Engine',
        'review-queue': 'Review Queue',
        execution: 'Execution Lane',
        'sandbox-tx': 'Sandbox TX',
        positions: 'Open Positions',
        settlement: 'Settlement',
        reports: 'Reports',
        system: 'System Health',
      };
      breadcrumb.textContent = labels[name] || name;
    }
  }

  function sectionFromUrl() {
    var params = new URLSearchParams(window.location.search);
    var s = params.get('s') || params.get('active_tab');
    if (s) return s;
    var body = document.body;
    return body.dataset.section || 'overview';
  }

  navItems.forEach(function (btn) {
    btn.addEventListener('click', function () {
      activateSection(btn.dataset.section);
      if (sidebar) sidebar.classList.remove('mobile-open');
    });
  });

  activateSection(sectionFromUrl());

  /* ── SIDEBAR TOGGLE ────────────────────────────────────── */
  if (sidebarToggle && sidebar) {
    sidebarToggle.addEventListener('click', function () {
      sidebar.classList.toggle('collapsed');
      localStorage.setItem('pmbot-sidebar', sidebar.classList.contains('collapsed') ? 'collapsed' : 'expanded');
    });
    if (localStorage.getItem('pmbot-sidebar') === 'collapsed') {
      sidebar.classList.add('collapsed');
    }
  }

  if (mobileMenuBtn && sidebar) {
    mobileMenuBtn.addEventListener('click', function () {
      sidebar.classList.toggle('mobile-open');
    });
  }

  /* ── TOAST NOTIFICATIONS ───────────────────────────────── */
  var actionLog = document.getElementById('action-log');

  function showToast(message, type) {
    if (!actionLog) return;
    var el = document.createElement('div');
    el.className = 'action-log__item' + (type === 'error' ? ' action-log__item--error' : ' action-log__item--success');
    el.textContent = message;
    actionLog.appendChild(el);
    setTimeout(function () {
      el.style.transition = 'opacity .3s';
      el.style.opacity = '0';
      setTimeout(function () { el.remove(); }, 300);
    }, 4000);
  }

  /* ── API HELPERS ───────────────────────────────────────── */
  function apiPost(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(function (res) {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.json();
    });
  }

  /* ── ACTION BUTTONS ────────────────────────────────────── */
  function bindClick(id, handler) {
    var el = document.getElementById(id);
    if (el) el.addEventListener('click', handler);
  }

  bindClick('action-run-once', function () {
    var runId = (document.getElementById('run-id-input') || {}).value || '';
    showToast('Starting run-once…', 'info');
    apiPost('/api/actions/run-once', { run_id: runId || undefined })
      .then(function (data) { showToast('Run started: ' + (data.run_id || 'ok'), 'success'); })
      .catch(function (err) { showToast('Run-once failed: ' + err.message, 'error'); });
  });

  bindClick('action-pause', function () {
    var reason = (document.getElementById('pause-reason-input') || {}).value || 'Manual pause from UI';
    apiPost('/api/actions/pause', { reason: reason })
      .then(function () { showToast('Bot paused', 'success'); location.reload(); })
      .catch(function (err) { showToast('Pause failed: ' + err.message, 'error'); });
  });

  bindClick('action-resume', function () {
    apiPost('/api/actions/resume', {})
      .then(function () { showToast('Bot resumed', 'success'); location.reload(); })
      .catch(function (err) { showToast('Resume failed: ' + err.message, 'error'); });
  });

  /* ── REVIEW ACTIONS ────────────────────────────────────── */
  function reviewAction(action) {
    var queueId = (document.getElementById('review-queue-id') || {}).value;
    var runId = (document.getElementById('review-run-id') || {}).value;
    var rationale = (document.getElementById('review-rationale') || {}).value;
    var operatorId = (document.getElementById('review-operator-id') || {}).value;
    var note = (document.getElementById('review-note') || {}).value;
    var confirmed = (document.getElementById('review-confirm') || {}).checked;
    if (!confirmed) { showToast('Please confirm the decision first', 'error'); return; }
    if (!rationale) { showToast('Rationale is required', 'error'); return; }
    apiPost('/api/actions/review-' + action, {
      queue_id: queueId, run_id: runId, rationale: rationale,
      operator_id: operatorId, note: note,
    })
      .then(function (data) { showToast('Review ' + action + ': ' + (data.status || 'ok'), 'success'); location.reload(); })
      .catch(function (err) { showToast('Review failed: ' + err.message, 'error'); });
  }

  bindClick('action-review-approve', function () { reviewAction('approve'); });
  bindClick('action-review-reject', function () { reviewAction('reject'); });

  /* ── TX ACTIONS ────────────────────────────────────────── */
  bindClick('action-tx-reconcile', function () {
    var runId = (document.getElementById('tx-run-id') || {}).value;
    var limit = parseInt((document.getElementById('tx-limit') || {}).value || '100', 10);
    apiPost('/api/actions/tx-reconcile', { run_id: runId, limit: limit })
      .then(function (data) { showToast('Reconcile: ' + (data.reconciled || 0) + ' processed', 'success'); location.reload(); })
      .catch(function (err) { showToast('Reconcile failed: ' + err.message, 'error'); });
  });

  bindClick('action-tx-resubmit-safe', function () {
    var intentId = (document.getElementById('tx-intent-id') || {}).value;
    if (!intentId) { showToast('Intent ID required', 'error'); return; }
    apiPost('/api/actions/tx-resubmit-safe', { intent_id: intentId })
      .then(function (data) { showToast('Resubmit: ' + (data.status || 'ok'), 'success'); location.reload(); })
      .catch(function (err) { showToast('Resubmit failed: ' + err.message, 'error'); });
  });

  /* ── COUNT-UP ANIMATION ────────────────────────────────── */
  document.querySelectorAll('[data-count-to]').forEach(function (el) {
    var target = parseInt(el.dataset.countTo, 10);
    if (isNaN(target) || target === 0) return;
    var duration = 600;
    var start = performance.now();
    function step(now) {
      var progress = Math.min((now - start) / duration, 1);
      var eased = 1 - Math.pow(1 - progress, 3);
      el.textContent = Math.round(eased * target);
      if (progress < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  });

})();
