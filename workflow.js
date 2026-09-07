'use strict';
(() => {
  let latest = null, sending = false, previewWindow = null, previewId = null;
  function render() {
    if (!latest) return;
    document.querySelectorAll('[data-workflow]').forEach(panel => {
      const id = panel.dataset.workflow, todo = data.todos.find(t => t.id === id);
      if (!todo) return;
      const run = latest.runs[id];
      const disabled = latest.busy || compatibility.read_only || history.pending || hasDraft() || run?.foreign;
      const button = (action, label, allowed) => `<button type="button" class="button small" data-workflow-action="${action}" data-todo="${id}" ${disabled || !allowed ? 'disabled' : ''}>${label}</button>`;
      const expanded = panel.querySelector('details')?.open;
      const scroll = panel.querySelector('pre')?.scrollTop || 0;
      const html = `<div class="workflow-actions"><strong>Implementation</strong>${button('implement','Implement with Codex', !run && todo.status === 'open' && latest.configured)}${run && (run.phase === 'implementation_failed' || run.resume_action === 'retry') ? button('retry','Retry implementation',true) : ''}${button('test','Test branch', (['ready','tested','test_failed'].includes(run?.phase) || run?.resume_action === 'test'))}${button('merge','Merge & restart', (['tested','merge_failed','push_failed','restart_failed'].includes(run?.phase) || run?.resume_action === 'merge'))}${run?.preview_url ? `<a class="button small" href="${escapeHTML(run.preview_url)}" target="_blank" rel="noopener">Open preview ↗</a>` : ''}</div><p class="muted">${escapeHTML(run ? ({implementing:'Implementing…',ready:'Ready to preview',testing:'Preparing preview…',tested:'Ready for your review',merging:'Merging and publishing…',restarting:'Restarting artifact…',done:'Completed',interrupted:'Interrupted — review and retry'}[run.phase] || run.phase.replaceAll('_',' ')) : !latest.configured ? 'Configure project test, preview and restart commands to enable this workflow.' : latest.automatic ? 'Automatic implementation is on for new ideas captured on this system.' : 'Automatic implementation is off.')}</p>${run ? `<details><summary>Progress & branch details</summary><pre>${escapeHTML(`${run.branch}\n${run.commit || ''}\n${run.worktree}\n\n${run.message}`)}</pre></details>` : ''}`;
      if (panel.dataset.rendered === html) return;
      panel.dataset.rendered = html; panel.innerHTML = html;
      if (expanded && panel.querySelector('details')) panel.querySelector('details').open = true;
      if (panel.querySelector('pre')) panel.querySelector('pre').scrollTop = scroll;
    });
  }
  async function refresh() {
    if (!token || sending) return;
    try {
      const response = await fetch('/api/workflow');
      if (!response.ok) return;
      latest = await response.json();
      if (previewWindow && previewId) {
        const preview = latest.runs[previewId];
        if (preview?.preview_url && preview.phase === 'tested') { previewWindow.location.href = preview.preview_url; previewWindow = null; previewId = null; }
        else if (preview?.phase.endsWith('failed')) { previewWindow.close(); previewWindow = null; previewId = null; }
      }
      render();
    } catch (_) { /* retain visible last progress during restart */ }
  }
  document.addEventListener('unfertig:todos-rendered', render);
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-workflow-action]');
    if (!button) return;
    event.preventDefault(); event.stopPropagation();
    if (sending || button.disabled || hasDraft()) return;
    const id = button.dataset.todo, action = button.dataset.workflowAction, run = latest.runs[id];
    if (action === 'merge' && !confirm(`Merge ${run.branch} at ${run.commit.slice(0,12)}, push it and restart the configured artifact?`)) return;
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
      latest = result; await load(); render();
    } catch (error) { if (previewWindow) previewWindow.close(); previewWindow = null; previewId = null; alert(error.message); }
    finally { sending = false; render(); }
  });
  setInterval(refresh, 2000);
  setTimeout(refresh, 1200);
})();
