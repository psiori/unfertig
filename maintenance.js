'use strict';
(() => {
  const panel = document.createElement('details');
  panel.className = 'instance-maintenance';
  panel.hidden = true;
  panel.innerHTML = '<summary>Instance maintenance</summary><p></p><ul></ul>';
  (document.querySelector('main') || document.body).prepend(panel);
  const summary = panel.querySelector('summary'), message = panel.querySelector('p'), list = panel.querySelector('ul');
  let busy = false, last = '';
  async function refresh() {
    if (busy) return;
    busy = true;
    try {
      const response = await fetch('/api/maintenance', {cache:'no-store', signal:AbortSignal.timeout(5000)});
      if (!response.ok) throw new Error('Maintenance status unavailable');
      const state = await response.json(), fingerprint = JSON.stringify(state);
      if (fingerprint === last) return;
      if (!Array.isArray(state.hooks) || !Array.isArray(state.blockers) || typeof state.pending !== 'boolean' || typeof state.phase !== 'string' || (state.update != null && (typeof state.update !== 'object' || Array.isArray(state.update)))) throw Error('Invalid maintenance status');
      const hooks = state.hooks.filter(h => h.status !== 'complete');
      const updatePhase = state.update?.phase;
      const updateActive = Boolean(updatePhase && !['complete','idle','installed','current'].includes(updatePhase));
      const current = state.currency?.state === 'current' && /^[0-9a-f]{40,64}$/.test(state.runtime_commit || '') && state.currency.target_commit === state.runtime_commit && Number.isFinite(Date.parse(state.currency.checked_at));
      const updateIssues = state.update?.error || state.update?.blockers?.length;
      panel.hidden = current && state.phase === 'idle' && !state.pending && !state.blockers.length && !state.error && !hooks.length && !updateActive && !updateIssues;
      const phase = state.pending ? state.phase : state.error || state.blockers.length || updateIssues || hooks.some(h => h.status === 'failed') ? 'needs attention' : updateActive ? updatePhase : state.phase !== 'idle' ? state.phase : !current ? (state.currency?.state === 'outdated' ? 'update available' : 'status unavailable') : 'current';
      summary.textContent = `Instance maintenance · ${phase}`;
      message.textContent = [state.pending ? 'Restart requested. New work is paused; queued work is retained.' : '',
        ...state.blockers, state.error, state.update?.error, ...(state.update?.blockers || []), state.currency?.message || (!current ? 'Published application revision is unknown.' : ''), updateActive ? state.update?.message : '', state.runtime_commit ? `Running revision ${state.runtime_commit.slice(0,12)}` : ''].filter(Boolean).join(' · ');
      list.replaceChildren();
      hooks.forEach(hook => {
        const row = document.createElement('li');
        row.textContent = `${hook.todo} · ${hook.hook} · ${hook.status}: ${hook.message} `;
        if (hook.status === 'failed' && !state.pending) {
          const retry = document.createElement('button'); retry.type = 'button'; retry.textContent = 'Retry hook';
          retry.addEventListener('click', async () => {
            retry.disabled = true;
            try {
              const board = await (await fetch('/api/state', {cache:'no-store'})).json();
              const result = await fetch('/api/maintenance', {method:'PUT', headers:{'Content-Type':'application/json', 'X-Board-Token':board.token},
                body:JSON.stringify({action:'retry_hook', todo:hook.todo, event:hook.id})});
              if (!result.ok) throw new Error((await result.json()).error);
              last = ''; refresh();
            } catch (error) { last = ''; panel.hidden = false; message.textContent = error.message; retry.disabled = false; }
          });
          row.append(retry);
        }
        list.append(row);
      });
      last = fingerprint;
    } catch (error) { panel.hidden = false; last = ''; summary.textContent = 'Instance maintenance · reconnecting'; message.textContent = error.message; }
    finally { busy = false; }
  }
  refresh(); setInterval(refresh, 3000);
})();
