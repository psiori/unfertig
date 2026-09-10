"""Render and exercise maintenance UI with Chromium and disposable responses."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile


def verify(output):
    app = Path(__file__).resolve().parent
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='unfertig-update-browser-') as temporary:
        html = Path(temporary) / 'index.html'
        fixture = r'''
const state={phase:'idle',pending:false,supported:true,blockers:[],error:'',runtime_commit:'a'.repeat(40),hooks:[],update:{},
 currency:{state:'outdated',target_commit:'b'.repeat(40),message:'A new instance is available.'},update_action:{available:true,request:{}}};
let poll, writes=0;
window.setInterval=f=>poll=f;
window.fetch=async(url,options)=>{
 if(url==='/api/state') return {ok:true,json:async()=>({token:'fixture'})};
 if(options?.method==='PUT') {
  const body=JSON.parse(options.body);
  if(body.action!=='update_restart'||body.target_commit!==state.currency.target_commit||options.headers['X-Board-Token']!=='fixture')throw Error('Wrong action');
  writes++;state.update_action.request={id:'fixture',state:'pending'};
 }
 return {ok:true,json:async()=>structuredClone(state)};
};
'''
        checks = r'''
(async()=>{
 const check=(condition,message)=>{if(!condition)throw Error(message);};
 await new Promise(resolve=>setTimeout(resolve,20));
 const panel=document.querySelector('.instance-maintenance');panel.open=true;
 const button=panel.querySelector('button');check(button?.textContent==='Update & restart','Missing action');
 button.click();button.click();await new Promise(resolve=>setTimeout(resolve,20));await poll();
 check(writes===1,'Duplicate submission');check(panel.innerText.includes('not yet confirmed'),'Acceptance reported as installation');
 state.pending=true;state.phase='draining';state.blockers=['Task T2 is running'];await poll();
 check(panel.innerText.includes('queued work is retained')&&panel.innerText.includes('T2'),'Drain evidence lost');
 state.pending=false;state.phase='idle';state.blockers=[];state.update={phase:'failed',message:'Startup failed'};
 state.update_action.request={id:'fixture',state:'failed',message:'Git conflict'};await poll();
 check(panel.innerText.includes('Git conflict')&&panel.innerText.includes('Startup failed'),'Failure hidden');
 state.update={phase:'installed'};state.update_action.request={state:'complete'};state.runtime_commit='b'.repeat(40);
 state.currency={state:'current',target_commit:'b'.repeat(40),checked_at:'2026-09-10T00:00:00Z'};await poll();
 check(panel.hidden&&panel.getBoundingClientRect().height===0,'Healthy panel leaves footprint');
 state.currency={state:'outdated',target_commit:'c'.repeat(40),message:'A new instance is available.'};state.update_action.request={};await poll();
 check(panel.querySelector('button').getBoundingClientRect().right<=innerWidth,'Action overflows viewport');
 document.querySelector('#result').textContent='PASS: update action, duplicate click, acceptance, drain, retained failure, healthy recovery and layout';
})().catch(error=>document.querySelector('#result').textContent='FAIL: '+error.message);
'''
        html.write_text('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                       '<style>' + (app / 'style.css').read_text() + '</style><main><section><h1>Disposable maintenance fixture</h1>'
                       '<p id="result">RUNNING</p></section></main><script>' + fixture + '</script><script>'
                       + (app / 'maintenance.js').read_text() + '</script><script>' + checks + '</script>')
        for width in (1280, 390):
            command = ['chromium', '--headless', '--no-sandbox', '--disable-gpu', '--no-proxy-server',
                       '--user-data-dir=' + str(Path(temporary) / str(width)), '--no-first-run',
                       '--window-size=' + str(width) + ',900', '--virtual-time-budget=2000',
                       '--screenshot=' + str(output / f'update-{width}.png'), '--dump-dom', html.as_uri()]
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            if result.returncode or '<p id="result">PASS:' not in result.stdout:
                raise RuntimeError(result.stdout[-3000:] + result.stderr[-1000:])
        print(json.dumps(dict(result='pass', widths=[1280, 390], fixture='disposable browser responses')))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    verify(parser.parse_args().output.resolve())
