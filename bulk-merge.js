'use strict';
(() => {
  const button = document.querySelector('#workflow-merge-all');
  const feedback = document.querySelector('#workflow-merge-all-state');
  if (!button || !feedback) return;
  let running = false, owner = '', enabled = false, pending = null;
  const key = () => 'unfertig.merge-batch.v1.' + owner;
  function message(text) { feedback.textContent = text; feedback.hidden = !text; }
  function persist() {
    // Retain exact action IDs/revisions across tab reload and service restart.
    if (pending) sessionStorage.setItem(key(), JSON.stringify(pending));
    else sessionStorage.removeItem(key());
  }
  function draft(id) {
    return Boolean(document.querySelector(`.todo-editor[data-id="${id}"][data-dirty="true"]`) ||
      (typeof priorityDrafts !== 'undefined' && priorityDrafts.entries.has(id)));
  }
  function render() {
    button.hidden = !enabled;
    button.disabled = running || !token || compatibility.read_only || history.pending || busy;
    button.textContent = running ? 'Reviewing / queueing…' : pending ? 'Retry reviewed merge batch' : 'Merge all PRs & restart';
  }
  async function refresh() {
    if (running || !token) return;
    try {
      const response = await fetch('/api/workflow', {cache:'no-store'});
      if (!response.ok) throw Error('Workflow unavailable.');
      const state = await response.json();
      enabled = state.enabled === true;
      if (owner !== state.repository) {
        owner = state.repository;
        pending = JSON.parse(sessionStorage.getItem(key()) || 'null');
        if (pending) message('A reviewed merge batch has an uncertain outcome. Retry it unchanged to check saved receipts; inspect the integration pipeline for accepted entries.');
      }
    } catch (_) { enabled = false; }
    render();
  }
  button.addEventListener('click', async () => {
    if (running || button.disabled || !enabled) return;
    running = true; render();
    try {
      if (!pending) {
        const response = await fetch('/api/workflow/merge-review', {cache:'no-store'});
        const review = await response.json();
        if (!response.ok) throw Error(review.error || 'Cannot review merge batch.');
        if (review.repository !== owner) throw Error('Owning repository changed. Reload before reviewing.');
        const excluded = [...review.excluded];
        const entries = review.entries.filter(entry => {
          if (!draft(entry.id)) return true;
          excluded.push({id:entry.id, reason:'Unsaved todo or priority draft; save or reset before merging.'});
          return false;
        });
        const omissions = excluded.map(e => `${e.id}: ${e.reason}`).join('\n');
        if (!entries.length) {
          message('No eligible finished todos to queue.' + (omissions ? '\n'+omissions : ''));
          return;
        }
        const commits = entries.map(e => `${e.id} — ${e.name}\n` + e.repositories.map(r =>
          `  ${r.id}: ${r.repository}\n  ${r.branch} at ${r.commit}${r.changed ? '\n  '+r.pr_url : ' (no changes)'}`).join('\n')).join('\n\n');
        const notice = `Queue ${entries.length} finished todos for Merge & restart?\n\n${commits}\n\nEach action integrates and publishes its changed repositories and restarts configured artifacts through the serialized queue. Combined tests, migration checks and recovery still apply; entries can fail separately.${review.queue_blocked_by ? '\nQueue paused by '+review.queue_blocked_by+'. Existing recovery controls remain required.' : ''}${omissions ? '\n\nExcluded:\n'+omissions : ''}`;
        message(notice);
        if (!confirm(notice)) return;
        pending = {repository:owner, board:review.board, entries:entries.map(e => e.action)};
        // Fail before submitting if the browser cannot preserve uncertain work.
        persist();
      }
      if (pending.entries.some(e => draft(e.id))) throw Error('A reviewed todo has an unsaved draft. Save or reset it before retrying the exact batch.');
      const snapshotResponse = await fetch('/api/state', {cache:'no-store'});
      const snapshot = await snapshotResponse.json();
      if (!snapshotResponse.ok) throw Error(snapshot.error || 'Cannot read the board session.');
      const response = await fetch('/api/workflow/merge-batch', {method:'PUT',
        headers:{'Content-Type':'application/json','X-Board-Token':snapshot.token}, body:JSON.stringify(pending)});
      const result = await response.json();
      if (!response.ok) {
        // A server-side rejection may follow an earlier uncertain delivery.
        // Keep the original request until its per-entry receipts are reported.
        throw Error(result.error || 'Cannot check the reviewed merge batch.');
      }
      message(result.outcomes.length ? result.outcomes.map(e => `${e.id}: ${e.status} — ${e.message}`).join('\n') : 'No entries were queued.');
      if (!result.outcomes.some(e => e.status === 'unknown')) { pending = null; persist(); }
      await load();
    } catch (error) {
      message(error.message + (pending ? '\nOutcome may be partial. Retry the reviewed batch unchanged to check durable receipts. Accepted entries remain in the integration pipeline.' : ''));
    } finally { running = false; render(); }
  });
  document.addEventListener('unfertig:todos-rendered', () => { render(); refresh(); });
  setInterval(refresh, 4000);
  setTimeout(refresh, 1200);
})();
