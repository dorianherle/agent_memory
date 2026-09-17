// Adapt the Python bridge's CreateAgent to include actual workspace preparation.
// SDK workspace leases are shared across fresh agents in this same process.
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

export function installWorkspacePrewarm(Agent, createAgentPlatform) {
  const originalCreate = Agent.create.bind(Agent);
  let platform;
  Agent.create = async (options) => {
    if (options.cloud) return originalCreate(options);
    platform ??= createAgentPlatform();
    const release = await (await platform).prewarmLocalWorkspace(options);
    let released = false;
    const releaseOnce = async () => {
      if (!released) { released = true; await release(); }
    };
    try {
      const agent = await originalCreate(options);
      const dispose = agent[Symbol.asyncDispose].bind(agent);
      agent[Symbol.asyncDispose] = async () => {
        try { await dispose(); } finally { await releaseOnce(); }
      };
      const close = agent.close.bind(agent);
      agent.close = () => {
        try { return close(); } finally { void releaseOnce(); }
      };
      return agent;
    } catch (error) {
      await releaseOnce();
      throw error;
    }
  };
}

if (process.argv[2]) {
  const entry = process.argv[2];
  const require = createRequire(entry);
  // Match the bridge's ESM import; patching a separate CJS copy has no effect.
  const sdkEntry = require.resolve('@cursor/sdk').replace('/dist/cjs/', '/dist/esm/');
  const sdk = await import(pathToFileURL(sdkEntry));
  installWorkspacePrewarm(sdk.Agent, sdk.createAgentPlatform);
  process.argv.splice(1, 2, entry);
  await import(pathToFileURL(entry));
}
