'use strict';
// Input only changes a local flag. One heartbeat per 20 seconds batches activity.
(function () {
  const client = crypto.randomUUID();
  let active = true, sending = false, starting = false, lastRun = '', latest = null;
  const button = document.querySelector('#process-run');
  const state = document.querySelector('#processing-state');
  const details = document.querySelector('#processing-details');
  const mark = document.querySelector('.capture-mark');
  function changed() { active = true; queueMicrotask(() => { if (latest) render(latest); }); }
  document.addEventListener('unfertig:ideas-rendered', () => { if (latest) render(latest); });
  for (const name of ['input', 'change', 'pointerdown', 'keydown']) document.addEventListener(name, changed, {passive:true});
  function render(result) {
    latest = result;
    const running = result.status === 'running';
    mark.classList.toggle('is-processing', running);
    const pending = (data?.ideas || []).filter(idea => idea.routing?.status !== 'routed' && !(data?.todos || []).some(todo => todo.source_ideas.includes(idea.id))).length;
    button.disabled = starting || running || !result.enabled || !result.available || compatibility.read_only || history.pending || !pending || hasDraft();
    button.textContent = running ? 'Creating todos…' : 'Create todos with Codex';
    button.title = `Working directory: ${result.working_directory}`;
    const attention = result.status === 'failed' || result.status === 'needs_attention';
    state.textContent = !result.enabled ? 'Processing is disabled for this board.' : !result.available ? 'Codex setup required — see details.' : running ? 'Codex is turning saved ideas into a plan…' :
      attention ? 'Needs attention — open run details below.' : hasDraft() ? 'Save your draft before creating todos.' :
      !pending ? 'All caught up. Capture an idea to get started.' : `${pending} saved ${pending === 1 ? 'idea is' : 'ideas are'} ready to become todos.`;
    document.querySelector('#processing-summary').textContent = result.enabled && result.automatic && result.system_identified ?
      `Automatic after ${Math.round(result.idle_seconds / 60)} min idle · Run details` : 'Manual processing · Run details';
    document.querySelector('#processing-automation').textContent = result.enabled && result.automatic && result.system_identified ?
      `Automatic after ${Math.round(result.idle_seconds / 60)} minutes without activity, or ${result.closed_seconds} seconds after all tabs close. Only saved ideas captured on this system are processed automatically.` :
      !result.system_identified ? 'Automatic processing is unavailable because this system could not be identified.' : 'Automatic processing is off. Use the button to process saved ideas.';
    details.textContent = `Working directory: ${result.working_directory}\nSelection: ${result.directory_source}\n\n${result.message || 'No run in this session.'}`;
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
    if (starting || button.disabled || hasDraft()) return;
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
