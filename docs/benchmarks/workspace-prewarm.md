# Actual workspace startup preparation

Cursor's documented `Agent.create()` returns a handle without resolving the local
workspace. `prewarmLocalWorkspace(options)` acquires the executor ahead of the
first send, and returns a release function. Options must match the later agent.
Source: https://cursor.com/docs/sdk/typescript#prewarm-a-local-workspace

The installed Python SDK does not expose this API. Studio now launches its
bundled bridge through studio_bridge.mjs, which wraps the same ESM Agent.create
used by the bridge. It acquires the workspace lease with the exact agent options
before publishing the handle and releases it at disposal or failed creation.
All fresh Studio agents use the same bridge, sharing the SDK executor cache.
No dummy send, model inference, or extra conversation history is used to warm.

Validation:
- Bundled real bridge launches and closes successfully.
- 19 Python Studio/runtime tests pass.
- 2 Node tests verify prepare-before-create and lease cleanup, including failure.
- Local save/state/packet preparation in an isolated tiny workspace: 242.4,
  259.0, 259.7 ms across three samples. Not a large-project measurement.
- Updated preview: http://127.0.0.1:51801/.
- Authenticated before/after startup timing remains pending user connection.

The adapter relies on the Python SDK's bundled bridge layout and its matching
ESM SDK entry. An SDK packaging change may require updating this adapter.
Do not claim measured startup improvement until the authenticated run completes.
