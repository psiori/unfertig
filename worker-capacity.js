'use strict';
(() => {
  const form = document.querySelector('#worker-capacity');
  if (!form) return;
  const input = form.querySelector('input'), dot = form.querySelector('.live-dot');
  const label = form.querySelector('[data-capacity-label]'), save = form.querySelector('button');
  const message = document.querySelector('#worker-capacity-message');
  let refreshing = false;
  let latest = null, revision = null, dirty = false, sending = false, verified = 0;
  const labels = {ready:'Ready', occupied:'In use', full:'Full', inactive:'Inactive', unavailable:'Unavailable', unknown:'Unknown'};
  function render() {
    const fresh = Date.now() - verified < 6000;
    const state = fresh && labels[latest?.capacity_state] ? latest.capacity_state : 'unknown';
    dot.dataset.state = state;
    label.textContent = `WORKERS · ${labels[state]} ${fresh ? latest.active_count : '?'}/`;
    form.title = `${labels[state]} — owner-local worker slots. Queued jobs do not occupy slots.`;
    dot.setAttribute('aria-label', labels[state]);
    if (!dirty && latest) input.value = latest.max_workers;
    input.disabled = sending || !fresh || !latest?.worker_settings?.editable;
    save.hidden = !dirty;
    save.disabled = sending || input.disabled;
    if (!dirty && latest?.worker_settings?.error) message.textContent = latest.worker_settings.error;
  }
  async function refresh() {
    if (refreshing || sending) return;
    refreshing = true;
    const requestedAt = Date.now();
    try {
      const response = await fetch('/api/workflow', {cache:'no-store', signal:AbortSignal.timeout(5000)});
      if (!response.ok) throw new Error('Worker status unavailable.');
      latest = await response.json(); verified = requestedAt;
      if (!dirty) revision = latest.worker_settings?.revision;
    } catch (_) { verified = 0; }
    refreshing = false; render();
  }
  input.addEventListener('input', () => { dirty = true; message.textContent = ''; render(); });
  input.addEventListener('keydown', event => {
    if (event.key === 'Escape') { dirty = false; revision = latest?.worker_settings?.revision; message.textContent = ''; render(); }
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (sending || input.disabled || !form.reportValidity()) return;
    sending = true; render();
    try {
      const snapshot = await fetch('/api/state');
      const state = await snapshot.json();
      if (!snapshot.ok) throw new Error(state.error || 'Cannot read the instance.');
      const response = await fetch('/api/workflow/settings', {method:'PUT', headers:{'Content-Type':'application/json','X-Board-Token':state.token}, body:JSON.stringify({max_workers:Number(input.value), revision})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Could not save worker limit.');
      latest = result; verified = Date.now(); revision = result.worker_settings.revision; dirty = false;
      message.textContent = 'Saved. Next dispatch uses this limit; active work finishes and queued jobs remain.';
    } catch (error) {
      message.textContent = `${error.message} Your draft is retained. Escape restores the latest saved value.`;
      // A new submission after an explicit Escape/review uses a fresh revision.
      sending = false; await refresh();
    } finally { sending = false; render(); }
  });
  setInterval(() => { render(); }, 1000);
  setInterval(refresh, 2000);
  refresh();
})();
