import test from 'node:test';
import assert from 'node:assert/strict';
import {installWorkspacePrewarm} from '../studio_bridge.mjs';

test('workspace is ready before handle publication; lease released exactly once', async () => {
  const steps = [];
  const agent = {close(){steps.push('close');}, async [Symbol.asyncDispose](){steps.push('dispose');}};
  const Agent = {async create(options){steps.push('create'); return agent;}};
  const options = {local:{cwd:'/test',settingSources:[]},mcpServers:{memory:{command:'python'}}};
  installWorkspacePrewarm(Agent, async () => ({async prewarmLocalWorkspace(actual){
    assert.equal(actual,options); steps.push('warm'); return async () => steps.push('release');
  }}));
  const ready = await Agent.create(options);
  assert.deepEqual(steps,['warm','create']);
  await ready[Symbol.asyncDispose](); ready.close();
  assert.equal(steps.filter(s=>s==='release').length,1);
});

test('failed create releases prepared workspace', async () => {
  let released = 0;
  const Agent = {async create(){throw new Error('failed');}};
  installWorkspacePrewarm(Agent, async () => ({async prewarmLocalWorkspace(){return async()=>released++;}}));
  await assert.rejects(Agent.create({local:{cwd:'/test'}}), /failed/);
  assert.equal(released,1);
});
