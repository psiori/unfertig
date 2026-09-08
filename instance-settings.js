'use strict';
(() => {
  const get = id => document.getElementById(id);
  const dialog = get('settings-dialog');
  if (!dialog) return;
  const form = get('settings-form'), fields = get('settings-fields');
  const message = get('settings-message'), comparison = get('settings-comparison');
  let current, latest, base = {}, revision, token, storageKey, saving = false, loading = false;
  let inputs = {}, dirty = false;
  const draft = () => Object.fromEntries(Object.entries(inputs).map(([key, input]) => [key, input.value]));
  const changes = () => Object.fromEntries(Object.entries(draft()).filter(([key, value]) => value !== String(base[key] ?? '')));
  function remember() {
    dirty = Object.keys(changes()).length > 0;
    try {
      if (storageKey) {
        if (dirty) sessionStorage.setItem(storageKey, JSON.stringify({revision, base, draft:draft()}));
        else sessionStorage.removeItem(storageKey);
      }
    } catch (_) { message.textContent = 'Browser draft storage is unavailable. Keep this page open until saved.'; }
    controls();
  }
  function controls() {
    for (const input of Object.values(inputs)) input.disabled = saving || loading || !current?.editable;
    get('settings-save').disabled = saving || loading || !dirty || !current?.editable || !comparison.hidden;
    for (const id of ['settings-refresh', 'settings-keep', 'settings-use']) get(id).disabled = saving || loading;
  }
  function populate(values) {
    for (const [key, input] of Object.entries(inputs)) input.value = values[key] ?? '';
  }
  function render(snapshot) {
    current = snapshot; inputs = {}; fields.replaceChildren();
    get('settings-layer').textContent = snapshot.layer === 'local'
      ? 'Saved for this machine in the host’s durable local preferences, overriding shared defaults. Normal supervisor restart retains your choices. Shared defaults stay operator-maintained.'
      : 'Saved for this instance. If its configuration is shared, these settings are shared too.';
    for (const [key, spec] of Object.entries(snapshot.fields)) {
      const label = document.createElement('label'); label.textContent = spec.label;
      const input = document.createElement('input'); input.type = spec.type; input.name = key;
      input.id = `setting-${key}`;
      for (const attr of ['min', 'max', 'maxLength']) if (spec[attr] !== undefined) input[attr] = spec[attr];
      if (spec.type === 'number') { input.required = true; input.step = '1'; }
      const help = document.createElement('span'); help.className = 'hint'; help.id = `${input.id}-help`;
      const effective = snapshot.values[key];
      help.textContent = `${spec.help} Effective now: ${effective === '' ? '(hidden)' : effective}.`;
      input.setAttribute('aria-describedby', help.id);
      input.addEventListener('input', () => { message.textContent = ''; remember(); });
      label.append(input, help); fields.append(label); inputs[key] = input;
    }
    base = {...(snapshot.saved_values || snapshot.values)}; revision = snapshot.revision;
    populate(base); comparison.hidden = true; dirty = false;
    controls();
  }
  async function read() {
    const response = await fetch('/api/settings', {cache:'no-store', signal:AbortSignal.timeout(10000)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Cannot read settings.');
    return result;
  }
  function showComparison(snapshot) {
    latest = snapshot;
    const list = get('settings-latest'); list.replaceChildren();
    for (const [key, spec] of Object.entries(snapshot.fields)) {
      const term = document.createElement('dt'), value = document.createElement('dd');
      term.textContent = spec.label; value.textContent = String((snapshot.saved_values || snapshot.values)[key] || '(empty)');
      list.append(term, value);
    }
    comparison.hidden = false;
    message.textContent = snapshot.editable ? 'Compare the latest saved values. Your draft is retained.' : snapshot.error;
    controls();
    get('settings-keep').disabled = !snapshot.editable;
  }
  async function refresh() {
    if (saving || loading) return;
    loading = true; controls();
    try {
      const snapshot = await read();
      if (dirty) showComparison(snapshot);
      else { render(snapshot); message.textContent = snapshot.error || 'Latest settings loaded.'; }
    } catch (error) { message.textContent = `${error.message} Your draft is retained.`; }
    finally { loading = false; controls(); if (latest && !comparison.hidden) get('settings-keep').disabled = !latest.editable; }
  }
  get('open-settings').addEventListener('click', async () => {
    dialog.showModal();
    if (saving || loading) return;
    loading = true; controls(); message.textContent = 'Loading settings…';
    try {
      const response = await fetch('/api/state', {cache:'no-store', signal:AbortSignal.timeout(10000)});
      const state = await response.json();
      if (!response.ok) throw new Error('Cannot read this instance.');
      const key = `unfertig-settings:${state.context?.project_id || state.context?.data || location.origin}`;
      if (storageKey && storageKey !== key) throw new Error('The instance changed. Reload this page before editing settings. Your original draft is retained.');
      token = state.token; storageKey = key;
      if (current) { loading = false; await refresh(); return; }
      const snapshot = await read(); render(snapshot);
      let retained;
      try { retained = JSON.parse(sessionStorage.getItem(storageKey)); } catch (_) { /* Memory draft still works. */ }
      if (retained?.draft && retained?.base) {
        base = retained.base; revision = retained.revision; populate(retained.draft); remember();
        showComparison(snapshot);
      } else message.textContent = snapshot.error || 'Change a setting, then save. Closing keeps an unsaved draft in this tab.';
    } catch (error) { message.textContent = error.message; }
    finally { loading = false; controls(); if (latest && !comparison.hidden) get('settings-keep').disabled = !latest.editable; }
  });
  function close() { dialog.close(); get('open-settings').focus(); }
  get('close-settings').addEventListener('click', close);
  // Native dialog Escape closes without discarding. Reopening restores the draft.
  dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
  dialog.addEventListener('close', () => get('open-settings').focus());
  get('settings-refresh').addEventListener('click', refresh);
  get('settings-use').addEventListener('click', () => {
    render(latest); remember(); message.textContent = latest.error || 'Using the latest saved values.';
  });
  get('settings-keep').addEventListener('click', () => {
    if (!latest?.editable) return;
    const edits = changes(); render(latest); populate({...base, ...edits}); remember();
    message.textContent = 'Draft kept. Unedited fields use the latest saved values. Save when ready.';
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (get('settings-save').disabled || !form.reportValidity()) return;
    const edits = changes();
    for (const key of Object.keys(edits)) if (current.fields[key].type === 'number') edits[key] = Number(edits[key]);
    saving = true; controls(); message.textContent = 'Saving settings…';
    try {
      const response = await fetch('/api/settings', {method:'PUT', signal:AbortSignal.timeout(10000),
        headers:{'Content-Type':'application/json', 'X-Board-Token':token}, body:JSON.stringify({revision, changes:edits})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Could not save settings.');
      render(result); remember(); message.textContent = 'Saved. Settings are effective now; no restart is required.';
    } catch (error) {
      message.textContent = `${error.message} Your draft is retained. Use Compare latest before retrying an uncertain save. Reopen after a service restart to renew the session.`;
    } finally { saving = false; controls(); }
  });
})();
