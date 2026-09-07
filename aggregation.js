'use strict';
// Aggregate records never enter `data`: it is exclusively the local writable inbox.
let aggregateSources = [], aggregateBusy = false, aggregateSignature = '';
$('#initials').value = preference('initials');
$('#initials').addEventListener('change', () => { $('#initials').value = $('#initials').value.trim().toUpperCase(); remember('initials', $('#initials').value); });
const localRenderIdeas = renderIdeas, localRenderTodos = renderTodos, localUpdateChoices = updateChoices;
const aggregating = () => boardContext?.mode === 'aggregation';
const qualified = (project, value) => JSON.stringify([project, value]);
function sourceName(source) { return source.name || source.project_id; }
function projectOptions(selected='') {
  return '<option value="">Infer from text</option>' + (boardContext?.sources || []).map(config => {
    const source = aggregateSources.find(s => s.project_id === config.project_id) || config;
    return `<option value="${escapeHTML(config.project_id)}" ${selected === config.project_id ? 'selected' : ''}>${escapeHTML(sourceName(source))}</option>`;
  }).join('');
}
function ownerLink(source, kind, id) {
  // Only server-validated loopback origins; unavailable services are never launched.
  if (source.status !== 'reachable') return '<span class="route-warning">Instance unavailable</span>';
  if (source.transport === 'filesystem' || !source.url) return `<span class="meta">Filesystem record · ${escapeHTML(source.context?.data || '')} · ${escapeHTML(id)} · read details here; no service opened</span>`;
  return `<a class="button small" target="_blank" rel="noopener noreferrer" href="${escapeHTML(source.url)}/#${kind}-${encodeURIComponent(id)}">Open in ${escapeHTML(sourceName(source))} ↗</a>`;
}
async function refreshAggregate() {
  if (!aggregating() || aggregateBusy || busy || document.hidden) return;
  aggregateBusy = true;
  try {
    const response = await fetch('/api/aggregate'); const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not refresh sources.');
    const signature = JSON.stringify(result.sources.map(s => [s.project_id,s.name,s.revision,s.status,s.error,s.transport,s.fallback_reason]));
    aggregateSources = result.sources;
    $('#source-status').textContent = aggregateSources.map(s => `${sourceName(s)}: ${s.status}${s.transport ? ' · ' + s.transport : ''}${s.fallback_reason ? ' · ' + s.fallback_reason : ''}${s.checked_at ? ' · checked ' + new Date(s.checked_at).toLocaleTimeString() : ' · no cached records'}${s.error ? ' · ' + s.error : ''}`).join(' | ') || 'No sources configured. Add explicit sources in configuration and restart.';
    $('#source-status').hidden = false;
    if (signature !== aggregateSignature && !busy) { aggregateSignature = signature; updateChoices(); renderIdeas(); renderTodos(); }
    const todos = aggregateSources.flatMap(s => s.data?.todos || []), done = todos.filter(t => t.status === 'closed').length;
    $('#todo-count').textContent = todos.length; $('#done-count').textContent = done;
    $('#progress-fill').style.width = (todos.length ? done / todos.length * 100 : 0) + '%';
    $('#progress-note').textContent = `${done} of ${todos.length} source todos complete. See source status for freshness.`;
  } catch (error) { $('#source-status').hidden = false; $('#source-status').textContent = 'Source refresh failed; displayed records may be stale. ' + error.message; }
  finally { aggregateBusy = false; }
}
updateChoices = function() {
  if (!aggregating()) return localUpdateChoices();
  $('#new-todo').hidden = true;
  $('#project-filter-label').hidden = false; $('#idea-project-label').hidden = false;
  const project = $('#project-filter').value, selection = $('#idea-project').value;
  $('#project-filter').innerHTML = '<option value="">All projects</option>' + aggregateSources.map(s => `<option value="${escapeHTML(s.project_id)}" ${project === s.project_id ? 'selected' : ''}>${escapeHTML(sourceName(s))}</option>`).join('');
  $('#idea-project').innerHTML = projectOptions(selection);
  for (const [id, field, label] of [['group-filter','group','All groups'], ['tag-filter','tags','All tags']]) {
    const selected = $('#' + id).value;
    const choices = aggregateSources.filter(s => !project || s.project_id === project).flatMap(s => unique((s.data?.todos || []).flatMap(t => field === 'tags' ? t.tags : [t.group]).filter(Boolean)).sort().map(v => ({value:qualified(s.project_id,v), label:sourceName(s) + ' / ' + v})));
    $('#' + id).innerHTML = `<option value="">${label}</option>` + choices.map(c => `<option value="${escapeHTML(c.value)}" ${c.value === selected ? 'selected' : ''}>${escapeHTML(c.label)}</option>`).join('');
  }
};
renderIdeas = function() {
  if (!aggregating()) return localRenderIdeas();
  const pending = data.ideas.filter(i => i.routing?.status !== 'routed');
  $('#pending-count').textContent = pending.length; $('#process').disabled = !pending.length;
  $('#scratch-summary').textContent = `${pending.length} pending in this inbox · child ideas are shown for reference below`;
  $('#ideas').innerHTML = data.ideas.filter(i => $('#show-processed').checked || i.routing?.status !== 'routed').slice().reverse().map(idea => {
    const route = idea.routing || {}, source = aggregateSources.find(s => s.project_id === route.project_id);
    return `<li id="idea-${idea.id}" class="idea"><div class="idea-body"><p class="idea-text">${escapeHTML(idea.text)}</p><div class="meta">${escapeHTML(idea.id)} · ${escapeHTML(idea.author)} · ${escapeHTML(date(idea.date_entered))}</div><p class="${['unclear','blocked'].includes(route.status) ? 'route-warning' : ''}">${escapeHTML(route.status || 'Unprocessed')}${route.reason ? ': ' + escapeHTML(route.reason) : ''}</p>${route.todo_id ? `<p>Destination ${escapeHTML(route.todo_id)} ${source ? ownerLink(source,'todo',route.todo_id) : ''}</p>` : ''}<label>Project <select data-project-idea="${idea.id}" ${route.request || compatibility.read_only ? 'disabled' : ''}>${projectOptions(idea.selected_project || '')}</select></label></div><div class="idea-actions"><a class="text-button" href="/#idea-${idea.id}" target="_blank" rel="noopener">Open in this inbox ↗</a><button class="text-button" data-process="${idea.id}">Brief agent ↗</button></div></li>`;
  }).join('');
  for (const source of aggregateSources) {
    const originals = (source.data?.ideas || []).filter(i => $('#show-processed').checked || !(source.data?.todos || []).some(t => t.source_ideas.includes(i.id)));
    if (originals.length) $('#ideas').insertAdjacentHTML('beforeend', `<li><details class="module"><summary>${escapeHTML(sourceName(source))} · source ideas (read-only) · ${originals.length}</summary>${originals.map(i => `<article class="source-idea"><p>${escapeHTML(i.text)}</p><span>${escapeHTML(i.id)} · ${escapeHTML(i.author)} · ${escapeHTML(date(i.date_entered))}</span> ${ownerLink(source,'idea',i.id)}</article>`).join('')}</details></li>`);
  }
};
renderTodos = function() {
  if (!aggregating()) return localRenderTodos();
  const project = $('#project-filter').value, group = $('#group-filter').value, tag = $('#tag-filter').value, status = $('#status-filter').value, query = $('#search').value.toLocaleLowerCase().trim();
  let rows = aggregateSources.flatMap(source => (source.data?.todos || []).map(todo => ({source,todo})));
  rows = rows.filter(({source:s,todo:t}) => (!project || s.project_id === project) && (!group || qualified(s.project_id,t.group) === group) && (!tag || t.tags.some(v => qualified(s.project_id,v) === tag)) && (status === 'all' || (status === 'active' ? t.status !== 'closed' : t.status === status)) && (!query || [sourceName(s),t.id,t.name,t.description,t.author,t.group,...t.tags].join(' ').toLocaleLowerCase().includes(query)));
  const priorities = {urgent:0, high:1, normal:2, low:3};
  rows.sort((a,b) => { const x=a.todo,y=b.todo; switch ($('#sort').value) {case 'name': return x.name.localeCompare(y.name); case 'newest': return y.date_entered.localeCompare(x.date_entered); case 'oldest': return x.date_entered.localeCompare(y.date_entered); default: return priorities[x.priority]-priorities[y.priority] || x.date_entered.localeCompare(y.date_entered);} });
  const card = ({source:s,todo:t}) => `<details class="aggregate-todo" data-record-key="${escapeHTML(qualified(s.project_id,t.id))}"><summary><span class="project-column">${escapeHTML(sourceName(s))}</span><span><strong>${escapeHTML(t.name)}</strong><br>${escapeHTML(t.id)} · ${escapeHTML(t.status)} · ${escapeHTML(t.group || 'Ungrouped')} · ${escapeHTML(t.tags.join(', '))}</span><span class="badge">${escapeHTML(t.priority)}</span></summary><p>${escapeHTML(t.description)}</p><p>Requested by ${escapeHTML(t.author)} · structured by ${escapeHTML(t.created_by)}</p>${s.status !== 'reachable' ? '<p class="route-warning">Stale cached record</p>' : ''}${ownerLink(s,'todo',t.id)}</details>`;
  $('#result-count').textContent = `${rows.length} source todos`;
  if ($('#group-by').checked) {
    $('#todos').innerHTML = aggregateSources.filter(s => rows.some(r => r.source === s)).map(s => `<details class="module" open><summary>${escapeHTML(sourceName(s))}</summary>${unique(rows.filter(r => r.source === s).map(r => r.todo.group)).sort().map(g => `<details class="module" open><summary>${escapeHTML(g || 'Ungrouped')}</summary>${rows.filter(r => r.source === s && r.todo.group === g).map(card).join('')}</details>`).join('')}</details>`).join('');
  } else $('#todos').innerHTML = rows.map(card).join('');
  if (!rows.length) $('#todos').innerHTML = '<p>No source todos match. Check source status and filters.</p>';
};
$('#project-filter').addEventListener('change', () => { updateChoices(); renderTodos(); });
$('#ideas').addEventListener('change', async event => {
  const id = event.target.dataset.projectIdea; if (!id) return;
  const next = structuredClone(data), idea = next.ideas.find(i => i.id === id); idea.selected_project = event.target.value;
  if (await save(next)) renderIdeas();
  else event.target.value = data.ideas.find(i => i.id === id).selected_project || '';
});
function aggregationBrief(ideas) {
  return `${boardLocations()} Read PROCESS.md and its aggregation routing section. This is planning only; do not implement tasks.\n\nThis instance owns ideas only. Explicit selected_project wins. Otherwise infer one destination from the text among the configured projects below. If ambiguous or spanning projects, PUT /api/routes with idea_id, current revision, actor and no project_id to mark Unclear; leave pending and ask for correction. Never create an aggregator todo.\n\nSources (configuration is not write authorization):\n${JSON.stringify(aggregateSources.map(s => ({project_id:s.project_id,name:s.name,url:s.url,status:s.status,transport:s.transport,fallback_reason:s.fallback_reason,context:s.context})),null,2)}\n\nIdeas:\n${ideas.map(i => JSON.stringify(i,null,2)).join('\n\n')}\n\nFor each destination, use its current aggregator source context (filesystem) or GET /api/state (HTTP), and read its actual PROCESS.md and applicable repository instructions. Read TRANSPORTS.md for coordinated filesystem access; never hand-edit JSON or launch a child service. Filesystem discovery does not migrate older sources. HTTP rejection is a block, never a reason to bypass safeguards through filesystem. Both transports must retain the same request/receipt after a lost response. Confirm write authorization and Git owner/identity. Do not infer authority from workspace containment. Use the aggregator PUT /api/routes with X-Board-Token, idea_id, revision, actor (actual agent), project_id (inferred only if no selection), reason, initials, todo and preflight. preflight contains exact destination data, repository, process, project_id and process_sha256 (SHA-256 of the PROCESS.md bytes). The todo contains standard required fields; requester and source_refs are enforced by the server. Use processor initials (${JSON.stringify($('#initials').value.trim().toUpperCase())}); attribution stays separate.\n\nThe server saves the exact destination request in the inbox idea before delivery. Retry /api/routes for that idea after interruptions; it reuses the stored request regardless of new drafts. Never issue a replacement destination create or erase a claim. It creates exactly one todo with immutable board-qualified source_refs; no actionable idea copy is created. The inbox becomes routed only after destination save and local Git history succeed. Resolve pending history through its owning instance before retrying. Blocked/unclear remains pending. Once claimed, selection is locked: do not silently move existing work. Preserve journals, receipts and drafts; no snapshot rollback.\n\nNo routing step pushes, merges, rebases or resolves Git conflicts. Stop before any push on conflict, preserve accepted changes and notify the user. Push needs explicit authorization. T0023 publication policy remains separate. Report actual destination IDs and local commit outcomes.`;
}
setInterval(refreshAggregate, 4000);
setTimeout(refreshAggregate, 500);
