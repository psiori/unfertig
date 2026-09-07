'use strict';
// Input only changes a local flag. One heartbeat per 20 seconds batches activity.
(function () {
  const client = crypto.randomUUID();
  let active = true, sending = false, starting = false, lastRun = '';
  const button = document.querySelector('#process-run');
  const state = document.querySelector('#processing-state');
  const details = document.querySelector('#processing-details');
  function changed() { active = true; }
  for (const name of ['input', 'change', 'pointerdown', 'keydown']) document.addEventListener(name, changed, {passive:true});
  function render(result) {
    const running = result.status === 'running';
    button.disabled = starting || running || !result.enabled || !result.available || compatibility.read_only || history.pending || !data || hasDraft();
    button.textContent = running ? 'Processing ideas…' : 'Process ideas with Codex';
    button.title = `Working directory: ${result.working_directory}`;
    state.textContent = !result.available ? 'Codex setup required' : running ? 'Codex is translating saved ideas.' :
      result.status === 'failed' || result.status === 'needs_attention' ? 'Processing needs attention — see details.' :
      result.status === 'completed' ? 'Processing finished.' : result.automatic ? `Automatic after ${Math.round(result.idle_seconds / 60)} minutes idle or after closing all tabs · ideas captured on this system only` : 'Manual processing';
    details.textContent = `Working directory: ${result.working_directory}\nSelection: ${result.directory_source}\n${result.system_identified ? '' : 'System identity unavailable; automatic processing is disabled.\n'}${result.message || ''}`;
    if (lastRun && result.status !== 'running' && lastRun === result.run_id && !hasDraft()) load();
    lastRun = running ? result.run_id : '';
  }
  async function heartbeat(closed=false) {
    if (!token || (sending && !closed)) return;
    const wasActive = active;
    active = false;
    if (!closed) sending = true;
    try {
      const response = await fetch('/api/processing/presence', {method:'PUT', keepalive:closed,
        headers:{'Content-Type':'application/json', 'X-Board-Token':token},
        body:JSON.stringify({client_id:client, active:wasActive, closed, draft:hasDraft()})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Could not update processing status.');
      if (!closed) render(result);
    } catch (error) { active ||= wasActive; if (!closed) state.textContent = error.message; }
    finally { if (!closed) sending = false; }
  }
  button.addEventListener('click', async () => {
    if (starting || hasDraft()) return;
    starting = true; button.disabled = true;
    try {
      const response = await fetch('/api/processing/start', {method:'PUT',
        headers:{'Content-Type':'application/json','X-Board-Token':token}, body:'{}'});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Could not start processing.');
      starting = false; render(result);
    } catch (error) { state.textContent = error.message; }
    finally { starting = false; }
  });
  window.addEventListener('pagehide', () => heartbeat(true));
  window.addEventListener('pageshow', () => { active = true; heartbeat(); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { active = true; heartbeat(); } });
  setInterval(() => heartbeat(), 20000);
  setTimeout(() => heartbeat(), 1000);
})();
