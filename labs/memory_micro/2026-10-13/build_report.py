"""Build the reviewed suite report. Do not pool task-level correctness."""
from __future__ import annotations
from html import escape
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent

def build():
    suite=json.loads((ROOT/'suite.json').read_text())
    inventory=json.loads((ROOT/'evidence-inventory.json').read_text())
    reviewed=json.loads((ROOT/'summary.json').read_text())
    evidence={item['id']:item for item in inventory['experiments']}
    rows=[]; details=[]; text=[]
    text.append('Memory experiment screen. 13 October 2026.\n')
    text.append(reviewed['main_finding']+'\n')
    for experiment in reviewed['experiments']:
        ident=experiment['id']
        record=next(x for x in suite['experiments'] if x['id']==ident)
        path=record['path']
        assert all(evidence[ident]['files'].values()), ident+' is missing evidence.'
        rows.append('<tr><td>'+escape(ident+' '+experiment['name'])+'</td><td>'+escape(experiment['hypothesis'])+'</td><td>'+escape(experiment['finding'])+'</td><td>'+escape(experiment['classification'])+'</td></tr>')
        text += [ident+' '+experiment['name'], 'Hypothesis: '+experiment['hypothesis'],
                 'Finding: '+experiment['finding'], 'Classification: '+experiment['classification'],
                 'Evidence: '+str((ROOT/path/'results.json').resolve()), '']
        report=(ROOT/path/'report.txt').read_text()
        links=' '.join('<a href="'+escape(path+'/'+name)+'">'+escape(name)+'</a>'
                       for name in ['protocol.json','results.json','script.py','report.txt'])
        details.append('<details><summary>'+escape(ident+' evidence and limits')+'</summary><p>'+links+'</p><pre>'+escape(report)+'</pre></details>')
    applicable=[x for x in inventory['experiments'] if x['native_usage_applicable']]
    assert all(x['total_native_usage_verified'] for x in applicable)
    calls=sum(x['model_calls'] for x in applicable)
    usage={key:sum(x['known_native_usage'][key] for x in applicable)
           for key in ['input_tokens','output_tokens']}
    accounting=(str(calls)+' worker calls. Recorded native input tokens: '+format(usage['input_tokens'],',')+
                '. Recorded native output tokens: '+format(usage['output_tokens'],',')+'.')
    note='These counts exclude parent and subagent orchestration. The successful setup probe is separate. Packet sizes use o200k_base estimates. They are not total model-call costs.'
    text += [accounting,note,'','Limits:']+['- '+x for x in reviewed['limits']]
    text += ['','Next experiments:']+['- '+x for x in reviewed['next_experiments']]
    (ROOT/'report.txt').write_text('\n'.join(text)+'\n')
    reviewed['worker_accounting']={'calls':calls,'native_usage':usage,'note':note}
    (ROOT/'summary.json').write_text(json.dumps(reviewed,indent=2)+'\n')
    limits='<ul>'+''.join('<li>'+escape(x)+'</li>' for x in reviewed['limits'])+'</ul>'
    next_tests='<ul>'+''.join('<li>'+escape(x)+'</li>' for x in reviewed['next_experiments'])+'</ul>'
    html='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Memory experiment screen</title><style>
body{font:16px/1.55 system-ui,sans-serif;color:#18212c;background:#f5f7fa;margin:0}main{max-width:1180px;margin:auto;padding:32px 24px}h1{font-size:30px;line-height:1.2}p{max-width:85ch}.box{background:white;border:1px solid #d9e0e8;border-radius:12px;padding:18px;margin:20px 0}.table{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}td,th{text-align:left;vertical-align:top;border-bottom:1px solid #d9e0e8;padding:12px}th{background:#eaf0f6}td:first-child{min-width:140px}td:nth-child(2),td:nth-child(3){min-width:240px}a{color:#165fa5}details{background:white;border:1px solid #d9e0e8;border-radius:8px;padding:12px;margin:12px 0}summary{cursor:pointer;font-weight:600}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.5 ui-monospace,monospace}.muted{color:#536171;font-size:14px}</style><main><p class="muted">Private development evidence · 13 October 2026</p><h1>Memory experiment screen</h1>'''
    html+='<div class="box"><p>'+escape(reviewed['main_finding'])+'</p><p>'+escape(reviewed['interpretation'])+'</p></div>'
    html+='<div class="table"><table><thead><tr><th>Test</th><th>Hypothesis</th><th>Observed result</th><th>Classification</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>'
    html+='<div class="box"><h2>What the evidence supports</h2>'+limits+'<h2>Next tests</h2>'+next_tests+'</div>'
    html+='<p>'+escape(accounting)+'</p><p class="muted">'+escape(note)+'</p><p><a href="evidence-inventory.json">Evidence inventory</a> · <a href="audit.txt">Independent audit</a> · <a href="summary.json">Reviewed summary</a> · <a href="report.txt">Text report</a></p>'
    html+=''.join(details)+'</main></html>'
    (ROOT/'report.html').write_text(html)
    print(json.dumps({'report':str((ROOT/'report.html').resolve()),'calls':calls,'native_usage':usage},indent=2))

if __name__=='__main__': build()
