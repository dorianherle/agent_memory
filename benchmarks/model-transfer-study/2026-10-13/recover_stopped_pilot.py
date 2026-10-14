"""Recover completed and interrupted records from preserved native event files."""
import json
import sys
from pathlib import Path
from evaluation.model_transfer.runner import decode_completion, write_json, append_jsonl, DockerTools, Settings
from evaluation.model_transfer.study import grade_workspace, report

root=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else Path(__file__).resolve().parent/'pilot-v3'
stop_info=json.loads((root/'study-stop.json').read_text()) if (root/'study-stop.json').exists() else {}
stop_reason=stop_info.get('reason','metering_fault')
manifest=json.loads((root/'manifest.json').read_text())
settings=Settings(**manifest['settings'])
settings_image=json.loads((root/'preflight/result.json').read_text())['image_id']
tasks={t['id']:t for t in json.loads((root/'private/task-pack.json').read_text())}
path=root/'records.jsonl'
records=[json.loads(x) for x in path.read_text().splitlines()] if path.exists() else[]
existing={(r['task'],r['repeat'],r['arm']) for r in records if r.get('comparison')=='main'}
for folder in sorted((root/'runs').glob('*/repeat-*/*')):
 task_id,repeat,arm=folder.parts[-3:]
 repeat=int(repeat.removeprefix('repeat-'))
 if (task_id,repeat,arm) in existing:continue
 task=tasks[task_id]
 stages=[]
 for stage_folder in sorted(folder.glob('stage-*')):
  number=int(stage_folder.name.removeprefix('stage-'))
  result_path=stage_folder/'result.json'
  if result_path.exists():
   stages.append(json.loads(result_path.read_text()));continue
  completions=[]
  for completion_folder in sorted(stage_folder.glob('completion-*')):
   cp=completion_folder/'completion.json'
   if cp.exists():c=json.loads(cp.read_text())
   else:
    # Unknown process exit is not a verified successful completion.
    c=decode_completion((completion_folder/'events.jsonl').read_text(),-15)
    c.update(recovered_after_abort=True,requested_model=settings.model,reasoning=settings.reasoning)
    write_json(cp,c)
   completions.append(c)
  events=[json.loads(x) for x in (stage_folder/'run.jsonl').read_text().splitlines()] if (stage_folder/'run.jsonl').exists() else[]
  with DockerTools(folder/'workspace',arm,settings_image) as tools:snapshot=tools.execute('_snapshot')
  complete_stage=stage_folder/'stage.json'
  recovered=json.loads(complete_stage.read_text()) if complete_stage.exists() else {}
  result={'stage':number,'stop':'study_aborted_'+stop_reason,'tool_calls':sum(e.get('operation_count',1) for e in events if e['type']=='tool'),
          'usage_complete':False,'tokens':None,'known_tokens':sum(c['usage']['input_tokens']+c['usage']['output_tokens'] for c in completions),
          'input_tokens':sum(c['usage']['input_tokens'] for c in completions),'output_tokens':sum(c['usage']['output_tokens'] for c in completions),
          'cached_input_tokens':None,'reasoning_output_tokens':None,'elapsed_seconds':0,'elapsed_complete':False,
          'memory':snapshot,'grade':grade_workspace(folder/'workspace',task,task['stages'][number-1],settings_image),
          'recovered_after_abort':True,**recovered}
  write_json(result_path,result);stages.append(result)
  append_jsonl(stage_folder/'run.jsonl',{'type':'study_abort','reason':stop_reason,'result':result})
 known=bool(stages) and all(s['usage_complete'] for s in stages)
 row={'task':task_id,'domain':task['domain'],'family':task['family'],'repeat':repeat,'arm':arm,'comparison':'main',
      'complete':len(stages)==3 and stages[-1]['grade']['full_pass'],'isolated':True,'run_valid':stop_reason!='worker_contract_fault',
      'model_transfer_complete':arm!='models' or len(stages)==3 and all(s['memory'].get('result',{}).get('model') and s['memory']['result']['checks'].get('status')=='tested' for s in stages),
      'usage_complete':known,'tokens':sum(s['tokens'] for s in stages) if known else None,
      'known_tokens':sum(s['known_tokens'] for s in stages),'stages':stages,
      'elapsed_seconds':sum(s['elapsed_seconds'] for s in stages),'elapsed_complete':all(s.get('elapsed_complete',True) for s in stages),
      'recovered_after_abort':True}
 append_jsonl(path,row)
readiness=json.loads((root/'preflight/result.json').read_text())
result=report(root,readiness)
print(json.dumps({'conclusion':result['conclusion'],'trajectories':result['trajectories'],'reasons':result['reasons']}))
