'use strict';
(() => {
  let latest = null;
  const sending = new Set(), previews = new Map(), requests = new Map();
  let verifiedAt = null, refreshing = false;
  // A hung request or a suspended tab must not leave confirmed activity behind.
  const activityLifetime = 6000;
  function hasTicketDraft(id) {
    return Boolean(document.querySelector?.(`.todo-editor[data-id="${id}"][data-dirty="true"]`) ||
      (typeof priorityDrafts !== 'undefined' && priorityDrafts.entries.has(id)));
  }
  function renderActivity() {
    const fresh = verifiedAt !== null && Date.now() - verifiedAt < activityLifetime;
    document.querySelectorAll('[data-workflow-icon]').forEach(icon => {
      const todo = data.todos.find(t => t.id === icon.dataset.workflowIcon);
      const run = latest?.runs?.[todo?.id];
      const running = Boolean(fresh && latest?.enabled === true && (run?.active ?? latest.busy) === true &&
        run?.phase === 'implementing' && !run.foreign && !run.resume_action);
      icon.classList.toggle('implementation-running', running);
      const label = running ? `${todo.status} — Implementation running` : (todo?.status || '');
      icon.setAttribute('aria-label', label);
      icon.title = label;
    });
  }
  function canMerge(run) {
    return ['ready','tested','test_failed','merge_failed','push_failed','restart_failed','resolution_blocked'].includes(run?.phase) || run?.resume_action === 'merge';
  }
  function testStatus(run) {
    return run?.commit && run.tested_commit === run.commit
      ? 'This commit passed Test branch / Preview.'
      : '';
  }
  function nextStep(todo, run) {
    if (!run) return todo.status !== 'closed' ? ['implement','Implement',todo.status === 'open'] : null;
    if (run.phase === 'queued' || run.phase === 'merge_queued') return ['', run.phase === 'merge_queued' ? 'Queued for integration' : 'Queued', false];
    if (['resolving_conflict','testing_resolution'].includes(run.phase)) return ['',run.phase === 'resolving_conflict' ? 'Agent resolving merge conflict' : 'Testing resolved candidate',false];
    if (run.phase === 'resolution_blocked') return ['merge','Retry resolution',true];
    if (run.phase === 'migration_required') return ['migrate','Migrate & deploy',Boolean(run.deployment_review?.review_id)];
    if (['restart_failed','restarting','migrating','recovering'].includes(run.phase) && run.published_commit) return ['recover','Recover deployment',true];
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
    renderPipeline();
    document.querySelectorAll('[data-workflow-next]').forEach(slot => {
      const todo = data.todos.find(t => t.id === slot.dataset.workflowNext);
      const run = latest.runs[todo?.id];
      const next = todo && nextStep(todo, run);
      slot.hidden = latest.enabled !== true || !next;
      if (slot.hidden) { slot.innerHTML = ''; return; }
      const [action,label,allowed] = next;
      const disabled = sending.has(todo.id) || run?.active || !allowed || !latest.configured || compatibility.read_only || history.pending || hasTicketDraft(todo.id) || run?.foreign;
      const html = `<button type="button" class="button small next-step" data-workflow-action="${action}" data-todo="${todo.id}" ${disabled ? 'disabled' : ''}>${label}${allowed ? ' ↗' : ''}</button>`;
      if (slot.innerHTML !== html) slot.innerHTML = html;
    });
    document.querySelectorAll('[data-workflow]').forEach(panel => {
      panel.hidden = latest.enabled !== true;
      if (panel.hidden) { panel.innerHTML = ''; delete panel.dataset.rendered; return; }
      const id = panel.dataset.workflow, todo = data.todos.find(t => t.id === id);
      if (!todo) return;
      const run = latest.runs[id];
      const disabled = sending.has(id) || !latest.configured || run?.active || compatibility.read_only || history.pending || hasTicketDraft(id) || run?.foreign;
      const button = (action, label, allowed) => `<button type="button" class="button small" data-workflow-action="${action}" data-todo="${id}" ${disabled || !allowed ? 'disabled' : ''}>${label}</button>`;
      const expanded = panel.querySelector('details')?.open;
      const scroll = panel.querySelector('pre')?.scrollTop || 0;
      const html = `<div class="workflow-actions"><strong>Implementation</strong>${button('implement','Implement with Codex', !run && todo.status === 'open' && latest.configured)}${run && (run.phase === 'implementation_failed' || run.resume_action === 'retry') ? button('retry','Retry implementation',true) : ''}${button('test','Test branch', (['ready','tested','test_failed'].includes(run?.phase) || run?.resume_action === 'test'))}${button('merge','Merge & restart', canMerge(run) || run?.phase === 'migration_required')}${run?.phase === 'migration_required' ? button('migrate','Migrate & deploy',Boolean(run.deployment_review?.review_id)) : ''}${['restart_failed','restarting','migrating','recovering'].includes(run?.phase) && run?.published_commit ? button('recover','Recover deployment',true) : ''}${['merge_failed','push_failed','restart_failed','migration_required','resolution_blocked'].includes(run?.phase) && !run.queue_skip ? button('skip','Skip & continue queue',true) : ''}${run?.pr_url ? `<a class="button small" href="${escapeHTML(run.pr_url)}" target="_blank" rel="noopener">GitHub PR ↗</a>` : ''}${run?.preview_url ? `<a class="button small" href="${escapeHTML(run.preview_url)}" target="_blank" rel="noopener">Open preview ↗</a>` : ''}</div><p class="muted">${escapeHTML(run ? ({implementing:'Implementing…',ready:'Ready to preview or merge',testing:'Preparing preview…',tested:'Ready for your review',resolving_conflict:'Agent resolving merge conflict',testing_resolution:'Testing resolved candidate',resolution_blocked:'Blocked — user input required',merging:'Merging and publishing…',restarting:'Restarting artifact…',migrating:'Migrating reviewed storage…',recovering:'Recovering published deployment…',done:'Completed',interrupted:'Interrupted — review and retry'}[run.phase] || run.phase.replaceAll('_',' ')) : !latest.configured ? 'Configure project test, preview and restart commands to enable this workflow.' : latest.automatic ? 'Automatic implementation is on for new ideas captured on this system.' : 'Automatic implementation is off.')}</p>${testStatus(run) ? `<p class="muted">${testStatus(run)}</p>` : ''}${run ? `<details><summary>Progress & branch details</summary><pre>${escapeHTML(`${run.branch}\n${run.commit || ''}\n${run.worktree}\nPublished: ${run.published_commit || 'pending'}\nDeployment: ${run.phase === 'done' ? 'verified' : 'unverified / pending'}\nAffected files: ${(run.conflicted_paths || []).join(', ')}\n\n${run.message}\n\nGit diagnostics: ${JSON.stringify(run.git_diagnostics || {}, null, 2)}\nResolution reports: ${(run.resolution_reports || []).join('\n')}`)}</pre></details>` : ''}`;
      if (panel.dataset.rendered === html) return;
      panel.dataset.rendered = html; panel.innerHTML = html;
      if (expanded && panel.querySelector('details')) panel.querySelector('details').open = true;
      if (panel.querySelector('pre')) panel.querySelector('pre').scrollTop = scroll;
    });
  }
  function renderPipeline() {
    for (const row of document.querySelectorAll('[data-integration-pipeline]')) {
      row.hidden = latest.enabled !== true;
      if (row.hidden) { row.innerHTML = ''; continue; }
      const runs = Object.entries(latest.runs).sort((a,b) => (a[1].queued_at || '').localeCompare(b[1].queued_at || ''));
      const stages = [
        ['Working', ['queued','implementing','testing']],
        ['Ready', ['ready','tested']],
        ['Integration queue', ['merge_queued']],
        ['Integrating', ['merging','resolving_conflict','testing_resolution']],
        ['Migration review', ['migration_required']],
        ['Published / deploying', ['migrating','recovering','restarting']],
        ['Done', ['done']],
        ['Needs attention', ['implementation_failed','test_failed','merge_failed','push_failed','restart_failed','resolution_blocked','interrupted']]
      ];
      row.innerHTML = `<p><strong>Integration pipeline</strong>${latest.queue_blocked_by ? ` · Waiting for ${escapeHTML(latest.queue_blocked_by)}` : ''} · ${latest.active_count || 0}/${latest.max_workers || 1} workers${latest.draining ? ' · Draining before integration & restart' : ''}</p><div class="pipeline-stages">` + stages.map(([label, phases]) => {
        const items = runs.filter(([,run]) => phases.includes(run.phase));
        return `<section class="pipeline-stage"><h3>${label} <span>${items.length}</span></h3>${items.map(([id,run]) => `<div class="pipeline-ticket"><a href="#todo-${escapeHTML(id)}" title="${escapeHTML(run.message)}">${escapeHTML(id)}</a>${run.pr_url ? ` <a href="${escapeHTML(run.pr_url)}" target="_blank" rel="noopener">PR ↗</a>` : ''}${run.foreign ? ' · other system' : ''}<p class="muted">${escapeHTML((run.message || '').slice(0,300))}${run.conflicted_paths?.length ? ` · ${escapeHTML(run.conflicted_paths.join(', '))}` : ''}${run.queue_skip ? ' · Skipped by explicit request' : ''}</p></div>`).join('') || '<span class="muted">—</span>'}</section>`;
      }).join('') + '</div>';
    }
  }
  async function refresh() {
    renderActivity();
    if (!token || refreshing) return;
    refreshing = true;
    const requestedAt = Date.now();
    try {
      const response = await fetch('/api/workflow', {cache:'no-store', signal:AbortSignal.timeout(activityLifetime)});
      if (!response.ok) throw new Error('Workflow activity unavailable');
      latest = await response.json();
      for (const [id, body] of requests) {
        if (latest.runs[id]?.action_requests?.[body.request_id]) requests.delete(id);
      }
      verifiedAt = requestedAt;
      for (const [id, previewWindow] of previews) {
        const preview = latest.runs[id];
        if (preview?.preview_url && preview.phase === 'tested') { previewWindow.location.href = preview.preview_url; previews.delete(id); }
        else if (preview?.phase.endsWith('failed')) { previewWindow.close(); previews.delete(id); }
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
    if (!latest?.enabled || sending.has(button.dataset.todo) || button.disabled || hasTicketDraft(button.dataset.todo)) return;
    const id = button.dataset.todo, action = button.dataset.workflowAction, run = latest.runs[id];
    if (action === 'skip' && !confirm(`Skip ${id} and continue the already-authorized integration queue? Its failure, branch and deployment evidence remain retained. This does not mark it deployed or approve any migration.`)) return;
    if (action === 'merge' && !confirm(`Merge ${run.branch} at ${run.commit}, push it and restart the configured artifact?${testStatus(run) ? `\n\n${testStatus(run)}` : ''}`)) return;
    if (action === 'migrate' && !confirm(`Migrate & deploy ${run.deployment_review.candidate_commit}?\n\n${run.deployment_review.message}\n\nThis publishes the reviewed candidate, stops writers, retains an exact local backup, migrates storage, commits both wrapper pins and verifies restart. Failure retains a reservation for explicit recovery.`)) return;
    if (action === 'recover' && !confirm(`Recover the published deployment ${run.published_commit}? The host verifies existing deployment or resumes its retained migration; it does not remerge the ticket.`)) return;
    sending.add(id); button.disabled = true;
    if (action === 'test' && latest.web_preview) { const previewWindow = window.open('about:blank','_blank'); if (previewWindow) { previewWindow.opener = null; previewWindow.document.title = 'Preparing branch preview…'; previews.set(id, previewWindow); } }
    try {
      // A fresh snapshot supplies the revision; do not submit a stale editor's data.
      const snapshotResponse = await fetch('/api/state');
      const snapshot = await snapshotResponse.json();
      if (!snapshotResponse.ok) throw new Error(snapshot.error || 'Cannot read the board.');
      const current = snapshot.data.todos.find(t => t.id === id);
      const saved = data.todos.find(t => t.id === id);
      if (['name','description','category','depends_on','source_ideas','source_refs'].some(key => JSON.stringify(current[key]) !== JSON.stringify(saved[key]))) throw new Error('Task changed. Reload and review its scope first.');
      if (!requests.has(id)) requests.set(id, {id,action,revision:snapshot.revisions.todos[id],commit:run?.commit,...(action === 'migrate' ? {review_id:run.deployment_review.review_id} : {}),request_id:globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`});
      const response = await fetch('/api/workflow/action', {method:'PUT',headers:{'Content-Type':'application/json','X-Board-Token':snapshot.token},body:JSON.stringify(requests.get(id))});
      const result = await response.json();
      if (!response.ok) { if (response.status < 500) requests.delete(id); throw new Error(result.error || 'Could not start the stage.'); }
      requests.delete(id);
      latest = result; verifiedAt = null; await load(); render();
    } catch (error) { previews.get(id)?.close(); previews.delete(id); alert(error.message); }
    finally { sending.delete(id); render(); }
  });
  setInterval(refresh, 2000);
  setTimeout(refresh, 1200);
})();
