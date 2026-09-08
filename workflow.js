'use strict';
(() => {
  let latest = null, sending = false, previewWindow = null, previewId = null;
  let verifiedAt = null, refreshing = false;
  // A hung request or a suspended tab must not leave confirmed activity behind.
  const activityLifetime = 6000;
  function renderActivity() {
    const fresh = verifiedAt !== null && Date.now() - verifiedAt < activityLifetime;
    document.querySelectorAll('[data-workflow-icon]').forEach(icon => {
      const todo = data.todos.find(t => t.id === icon.dataset.workflowIcon);
      const run = latest?.runs?.[todo?.id];
      const running = Boolean(fresh && latest?.enabled === true && latest.busy === true &&
        run?.phase === 'implementing' && !run.foreign && !run.resume_action);
      icon.classList.toggle('implementation-running', running);
      const label = running ? `${todo.status} — Implementation running` : (todo?.status || '');
      icon.setAttribute('aria-label', label);
      icon.title = label;
    });
  }
  function canMerge(run) {
    return ['ready','tested','test_failed','merge_failed','push_failed','restart_failed'].includes(run?.phase) || run?.resume_action === 'merge';
  }
  function testStatus(run) {
    return run?.commit && run.tested_commit === run.commit
      ? 'This commit passed Test branch / Preview.'
      : '';
  }
  function nextStep(todo, run) {
    if (!run) return todo.status !== 'closed' ? ['implement','Implement',todo.status === 'open'] : null;
    if (run.resume_action) return [run.resume_action, {retry:'Retry implementation',test:'Preview',merge:'Merge & restart'}[run.resume_action],true];
    if (run.phase === 'implementation_failed') return ['retry','Retry implementation',true];
    if (['ready','test_failed'].includes(run.phase)) return ['test','Preview',true];
    if (['tested','merge_failed','push_failed','restart_failed'].includes(run.phase)) return ['merge','Merge & restart',true];
    if (['implementing','testing','merging','restarting'].includes(run.phase)) return ['',{implementing:'Implementing…',testing:'Preparing preview…',merging:'Merging…',restarting:'Restarting…'}[run.phase],false];
    return null;
  }
  function render() {
    renderActivity();
    if (!latest) return;
    document.querySelectorAll('[data-workflow-next]').forEach(slot => {
      const todo = data.todos.find(t => t.id === slot.dataset.workflowNext);
      const run = latest.runs[todo?.id];
      const next = todo && nextStep(todo, run);
      slot.hidden = latest.enabled !== true || !next;
      if (slot.hidden) { slot.innerHTML = ''; return; }
      const [action,label,allowed] = next;
      const disabled = sending || latest.busy || !allowed || !latest.configured || compatibility.read_only || history.pending || hasDraft() || run?.foreign;
      const html = `<button type="button" class="button small next-step" data-workflow-action="${action}" data-todo="${todo.id}" ${disabled ? 'disabled' : ''}>${label}${allowed ? ' ↗' : ''}</button>`;
      if (slot.innerHTML !== html) slot.innerHTML = html;
    });
    document.querySelectorAll('[data-workflow]').forEach(panel => {
      panel.hidden = latest.enabled !== true;
      if (panel.hidden) { panel.innerHTML = ''; delete panel.dataset.rendered; return; }
      const id = panel.dataset.workflow, todo = data.todos.find(t => t.id === id);
      if (!todo) return;
      const run = latest.runs[id];
      const disabled = sending || !latest.configured || latest.busy || compatibility.read_only || history.pending || hasDraft() || run?.foreign;
      const button = (action, label, allowed) => `<button type="button" class="button small" data-workflow-action="${action}" data-todo="${id}" ${disabled || !allowed ? 'disabled' : ''}>${label}</button>`;
      const expanded = panel.querySelector('details')?.open;
      const scroll = panel.querySelector('pre')?.scrollTop || 0;
      const html = `<div class="workflow-actions"><strong>Implementation</strong>${button('implement','Implement with Codex', !run && todo.status === 'open' && latest.configured)}${run && (run.phase === 'implementation_failed' || run.resume_action === 'retry') ? button('retry','Retry implementation',true) : ''}${button('test','Test branch', (['ready','tested','test_failed'].includes(run?.phase) || run?.resume_action === 'test'))}${button('merge','Merge & restart', canMerge(run))}${run?.preview_url ? `<a class="button small" href="${escapeHTML(run.preview_url)}" target="_blank" rel="noopener">Open preview ↗</a>` : ''}</div><p class="muted">${escapeHTML(run ? ({implementing:'Implementing…',ready:'Ready to preview or merge',testing:'Preparing preview…',tested:'Ready for your review',merging:'Merging and publishing…',restarting:'Restarting artifact…',done:'Completed',interrupted:'Interrupted — review and retry'}[run.phase] || run.phase.replaceAll('_',' ')) : !latest.configured ? 'Configure project test, preview and restart commands to enable this workflow.' : latest.automatic ? 'Automatic implementation is on for new ideas captured on this system.' : 'Automatic implementation is off.')}</p>${testStatus(run) ? `<p class="muted">${testStatus(run)}</p>` : ''}${run ? `<details><summary>Progress & branch details</summary><pre>${escapeHTML(`${run.branch}\n${run.commit || ''}\n${run.worktree}\n\n${run.message}`)}</pre></details>` : ''}`;
      if (panel.dataset.rendered === html) return;
      panel.dataset.rendered = html; panel.innerHTML = html;
      if (expanded && panel.querySelector('details')) panel.querySelector('details').open = true;
      if (panel.querySelector('pre')) panel.querySelector('pre').scrollTop = scroll;
    });
  }
  async function refresh() {
    renderActivity();
    if (!token || sending || refreshing) return;
    refreshing = true;
    const requestedAt = Date.now();
    try {
      const response = await fetch('/api/workflow', {cache:'no-store', signal:AbortSignal.timeout(activityLifetime)});
      if (!response.ok) throw new Error('Workflow activity unavailable');
      latest = await response.json();
      verifiedAt = requestedAt;
      if (previewWindow && previewId) {
        const preview = latest.runs[previewId];
        if (preview?.preview_url && preview.phase === 'tested') { previewWindow.location.href = preview.preview_url; previewWindow = null; previewId = null; }
        else if (preview?.phase.endsWith('failed')) { previewWindow.close(); previewWindow = null; previewId = null; }
      }
      render();
    } catch (_) {
      verifiedAt = null;
      renderActivity(); // Keep existing progress details, but withdraw confirmed activity.
    } finally { refreshing = false; }
  }
  document.addEventListener('unfertig:todos-rendered', render);
  document.addEventListener('visibilitychange', renderActivity);
  document.addEventListener('input', render);
  document.addEventListener('change', render);
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-workflow-action]');
    if (!button) return;
    event.preventDefault(); event.stopPropagation();
    if (!latest?.enabled || sending || button.disabled || hasDraft()) return;
    const id = button.dataset.todo, action = button.dataset.workflowAction, run = latest.runs[id];
    if (action === 'merge' && !confirm(`Merge ${run.branch} at ${run.commit}, push it and restart the configured artifact?${testStatus(run) ? `\n\n${testStatus(run)}` : ''}`)) return;
    sending = true; button.disabled = true;
    if (action === 'test' && latest.web_preview) { previewId = id; previewWindow = window.open('about:blank','_blank'); if (previewWindow) { previewWindow.opener = null; previewWindow.document.title = 'Preparing branch preview…'; } }
    try {
      // A fresh snapshot supplies the revision; do not submit a stale editor's data.
      const snapshotResponse = await fetch('/api/state');
      const snapshot = await snapshotResponse.json();
      if (!snapshotResponse.ok) throw new Error(snapshot.error || 'Cannot read the board.');
      const current = snapshot.data.todos.find(t => t.id === id);
      const saved = data.todos.find(t => t.id === id);
      if (current.name !== saved.name || current.description !== saved.description) throw new Error('Task changed. Reload and review its scope first.');
      const response = await fetch('/api/workflow/action', {method:'PUT',headers:{'Content-Type':'application/json','X-Board-Token':snapshot.token},body:JSON.stringify({id,action,revision:snapshot.revisions.todos[id],commit:run?.commit})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Could not start the stage.');
      latest = result; verifiedAt = null; await load(); render();
    } catch (error) { if (previewWindow) previewWindow.close(); previewWindow = null; previewId = null; alert(error.message); }
    finally { sending = false; render(); }
  });
  setInterval(refresh, 2000);
  setTimeout(refresh, 1200);
})();
