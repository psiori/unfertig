'use strict';
// Shared state machine. The exact body is retained across failed requests/reloads.
class PriorityDrafts {
  constructor(entries = []) { this.entries = new Map(entries); }
  begin(key, snapshot, priority, actor) {
    const prior = this.entries.get(key);
    if (prior) return prior;
    if (snapshot.todo.priority === priority) return null;
    const entry = {key, priority, original: structuredClone(snapshot.todo), revision:snapshot.revision,
      preflight:snapshot.preflight, actor, request_id:crypto.randomUUID(), status:'ready', message:''};
    this.entries.set(key,entry); return entry;
  }
  async send(key, deliver) {
    const entry=this.entries.get(key); if (!entry || entry.status==='sending') return null;
    entry.status='sending'; entry.message='Saving…';
    try {
      const result=await deliver(entry);
      if (result.history.pending) { entry.status='history'; entry.message='Saved at source · Git history pending. Finish its commit, then Retry.'; }
      else if (result.todo.priority!==entry.priority) { entry.status='conflict'; entry.message='Request recovered, but this record has changed again. Review latest.'; }
      else { this.entries.delete(key); }
      return result;
    } catch(error) {
      entry.status=error.status && error.status<500 ? 'conflict' : 'uncertain';
      entry.message=error.message + (entry.status==='uncertain' ? ' Outcome uncertain; Retry keeps the same request.' : ' Draft retained; review the latest record before retrying.');
      return null;
    }
  }
}
let priorityDrafts = new PriorityDrafts(), priorityOwner = '';
function restorePriorityDrafts() {
  const owner=boardContext?.project_id; if (!owner || priorityOwner===owner) return;
  priorityOwner=owner;
  try { priorityDrafts=new PriorityDrafts(JSON.parse(sessionStorage.getItem('unfertig.priority.'+owner)||'[]')); }
  catch { priorityDrafts=new PriorityDrafts(); }
}
function persistPriorityDrafts() {
  try { sessionStorage.setItem('unfertig.priority.'+priorityOwner,JSON.stringify([...priorityDrafts.entries])); }
  catch { notice('Priority drafts remain in this tab; browser storage is unavailable. Keep it open until resolved.'); }
}
function priorityControl(todo,key=todo.id,blocked=false) {
  restorePriorityDrafts(); const draft=priorityDrafts.entries.get(key);
  return `<span class="row-priority"><select class="badge priority-${todo.priority}" data-priority="${escapeHTML(key)}" aria-label="Priority for ${escapeHTML(todo.id)}" ${blocked || draft ? 'disabled' : ''}>${options(['low','normal','high','urgent'],todo.priority)}</select>${draft ? `<span class="priority-outcome" role="status">${escapeHTML(draft.message || 'Draft retained')} · requested ${escapeHTML(draft.priority)}</span><button type="button" class="button small" data-priority-retry="${escapeHTML(key)}" ${draft.status==='sending' ? 'disabled' : ''}>${draft.status==='conflict' ? 'Review latest' : 'Retry'}</button>` : ''}</span>`;
}
async function priorityJSON(path,body) {
  const response=await fetch(path,{method:'PUT',headers:{'Content-Type':'application/json','X-Board-Token':token},body:JSON.stringify(body)});
  const result=await response.json();
  if (!response.ok) { const error=new Error(result.error || 'Priority save failed.');error.status=response.status;throw error; }
  return result;
}
function priorityDisplayed(key) {
  if (boardContext?.mode==='aggregation') return aggregateDisplayed(key);
  return {todo:data.todos.find(t=>t.id===key),revision:revisions.todos[key]};
}
async function priorityLatest(key) {
  if (boardContext?.mode==='aggregation') return aggregateFresh(key);
  const s=await requestState(); token=s.token;
  return {todo:s.data.todos.find(t=>t.id===key), revision:s.revisions.todos[key], history:s.history,compatibility:s.compatibility};
}
async function deliverPriority(entry) {
  if (boardContext?.mode==='aggregation') return aggregateSavePriority(entry);
  const result=await priorityJSON('/api/changes',{request_id:entry.request_id,actor:entry.actor,
    changes:[{collection:'todos',id:entry.original.id,revision:entry.revision,record:{...entry.original,priority:entry.priority}}]});
  data=result.data;revision=result.revision;revisions=result.revisions;history=result.history;historyState();
  return {todo:result.data.todos.find(t=>t.id===entry.original.id),revision:result.revisions.todos[entry.original.id],history:result.history};
}
function redrawPriority(key) {
  const expandedBefore=new Set(expanded);
  renderPreservingDrafts();
  for(const value of expandedBefore) expanded.add(value);
  const target=$$('[data-priority], [data-priority-retry]').find(el=>(el.dataset.priorityRetry||el.dataset.priority)===key && !el.disabled);
  target?.focus({preventScroll:true});
}
function acceptPriorityLatest(key,latest) {
  if(boardContext?.mode==='aggregation') acceptAggregatePriority(key,latest);
  else { data.todos=data.todos.map(t=>t.id===key ? latest.todo : t);revisions.todos[key]=latest.revision; }
}
async function changePriority(key,desired,retry=false) {
  restorePriorityDrafts();
  if (busy || pendingRequest || compatibility.read_only) { toast('Finish the pending save or update this read-only board first.');redrawPriority(key);return; }
  const editor=$$('.todo-editor').find(form=>form.dataset.id===key);
  if(editor?.dataset.dirty==='true') { toast('Save or reset this task’s expanded edits before changing its priority. Your draft is preserved.');redrawPriority(key);return; }
  let draft=priorityDrafts.entries.get(key);
  busy=true;
  try {
    if (retry && draft?.status==='conflict') {
      const latest=await priorityLatest(key);
      if(latest.compatibility?.read_only || latest.history?.pending) throw new Error('Source is read-only or has pending Git history. Resolve that first.');
      if(!window.confirm(`Review current saved record:\n${JSON.stringify(latest.todo,null,2)}\n\nApply only your requested priority (${draft.priority}) to this version?`)) return;
      const wanted=draft.priority,actorName=draft.actor;
      acceptPriorityLatest(key,latest);
      priorityDrafts.entries.delete(key);draft=priorityDrafts.begin(key,latest,wanted,actorName);
    } else if (!draft) {
      const displayed=priorityDisplayed(key);if(!displayed?.todo)throw new Error('Source record unavailable. Refresh first.');
      if(displayed.todo.priority===desired) return;
      const actorName=actor();if(!actorName){redrawPriority(key);return;}
      draft=priorityDrafts.begin(key,displayed,desired,actorName);
      draft.message='Checking saved record…';
      persistPriorityDrafts();
      redrawPriority(key);
      const latest=await priorityLatest(key);draft.preflight=latest.preflight;
      if(latest.revision!==displayed.revision || latest.compatibility?.read_only || latest.history?.pending) {
        draft.status='conflict';draft.message='Source changed, is read-only, or has pending history. Review latest.';
        persistPriorityDrafts();redrawPriority(key);return;
      }
    }
    if(!draft){persistPriorityDrafts();redrawPriority(key);return;}
    // Persist before sending so a lost browser response can be retried verbatim.
    persistPriorityDrafts();busy=true;
    const operation=priorityDrafts.send(key,deliverPriority);redrawPriority(key);
    await operation;persistPriorityDrafts();
  } catch(error) {
    if(draft){draft.status='conflict';draft.message=error.message+' · priority draft retained';persistPriorityDrafts();}
    toast(error.message);
  }
  finally { busy=false;redrawPriority(key); }
}
// Native select keyboard behavior remains intact; only summary toggling is stopped.
for(const type of ['click','keydown','keyup']) $('#todos').addEventListener(type,event=>{
  if(event.target.closest('.row-priority')) event.stopPropagation();
});
$('#todos').addEventListener('change',event=>{
  if(event.target.dataset.priority) void changePriority(event.target.dataset.priority,event.target.value);
});
$('#todos').addEventListener('click',event=>{
  const button=event.target.closest('[data-priority-retry]');if(!button)return;
  event.preventDefault();void changePriority(button.dataset.priorityRetry,null,true);
});
