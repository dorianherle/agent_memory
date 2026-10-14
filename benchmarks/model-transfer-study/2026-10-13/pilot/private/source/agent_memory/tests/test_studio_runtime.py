from concurrent.futures import CancelledError
from threading import Event
from unittest import TestCase
from unittest.mock import Mock

from agent_memory.studio_runtime import AgentConfig, StandbyAgent, model_selection


class StandbyTests(TestCase):
    def setUp(self):
        self.config = AgentConfig('/tmp/project', 'grok-4.5', 'test-key')

    def test_grok_default_has_explicit_high_fast_params(self):
        self.assertEqual(model_selection('grok-4.5'), {
            'id': 'grok-4.5',
            'params': [{'id':'effort','value':'high'}, {'id':'fast','value':'true'}],
        })
        self.assertEqual(model_selection('composer-2.5'), {'id':'composer-2.5'})
        self.assertNotIn('test-key', repr(self.config))

    def test_standby_is_consumed_once_without_sending(self):
        handles = [Mock(), Mock()]
        factory = Mock(side_effect=handles)
        pool = StandbyAgent(factory)
        pool.warm(self.config).result(timeout=2)
        self.assertEqual(pool.status(), 'ready')
        first, metrics = pool.take(self.config)
        self.assertIs(first, handles[0])
        self.assertTrue(metrics['standby_ready'])
        self.assertEqual(pool.status(), 'idle')
        pool.warm(self.config).result(timeout=2)
        second, _ = pool.take(self.config)
        self.assertIs(second, handles[1])
        self.assertIsNot(first, second)
        for handle in handles:
            handle.send.assert_not_called()
            handle.close.assert_not_called()

    def test_config_change_closes_obsolete_ready_agent(self):
        first, second = Mock(), Mock()
        pool = StandbyAgent(Mock(side_effect=[first, second]))
        pool.warm(self.config).result(timeout=2)
        other = AgentConfig('/tmp/other', 'grok-4.5', 'different-key')
        pool.warm(other).result(timeout=2)
        first.close.assert_called_once()
        current, _ = pool.take(other)
        self.assertIs(current, second)

    def test_pending_obsolete_agent_cannot_replace_new_project(self):
        entered, release, closed = Event(), Event(), Event()
        old, new = Mock(), Mock()
        old.close.side_effect = closed.set
        def factory(config):
            if config == self.config:
                entered.set()
                release.wait(2)
                return old
            return new
        pool = StandbyAgent(factory)
        job = pool.warm(self.config)
        self.assertTrue(entered.wait(2))
        other = AgentConfig('/tmp/other', 'grok-4.5', 'test-key')
        pool.warm(other).result(timeout=2)
        release.set()
        self.assertTrue(closed.wait(2))
        with self.assertRaises(CancelledError):
            job.result()
        current, _ = pool.take(other)
        self.assertIs(current, new)

    def test_failed_preparation_allows_retry(self):
        agent = Mock()
        pool = StandbyAgent(Mock(side_effect=[RuntimeError('offline'), agent]))
        with self.assertRaisesRegex(RuntimeError, 'offline'):
            pool.take(self.config)
        self.assertEqual(pool.status(), 'idle')
        current, _ = pool.take(self.config)
        self.assertIs(current, agent)

    def test_shutdown_closes_unused_agent(self):
        agent=Mock()
        pool=StandbyAgent(lambda config:agent)
        pool.warm(self.config).result(timeout=2)
        pool.clear()
        self.assertEqual(pool.status(), 'idle')
        agent.close.assert_called_once()

    def test_cancel_pending_start_releases_waiter(self):
        from time import perf_counter
        entered, release, cancel = Event(), Event(), Event()
        agent = Mock()
        def create(config):
            entered.set()
            release.wait(3)
            return agent
        pool = StandbyAgent(create)
        pool.warm(self.config)
        self.assertTrue(entered.wait(1))
        cancel.set()
        start = perf_counter()
        try:
            with self.assertRaises(CancelledError):
                pool.take(self.config, cancelled=cancel.is_set)
            self.assertLess(perf_counter() - start, .2)
            self.assertEqual(pool.status(), 'idle')
        finally:
            release.set()


class WorkspaceScopeTests(TestCase):
    def test_nested_workspace_mcp_is_explicit_and_parent_hooks_are_disabled(self):
        import tempfile, json
        from pathlib import Path
        from unittest.mock import patch
        from agent_memory.studio_runtime import create_agent
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            child = root / 'nested'
            (root / '.cursor').mkdir()
            (child / '.cursor').mkdir(parents=True)
            (root / '.cursor' / 'mcp.json').write_text(json.dumps({'mcpServers': {'parent-only': {'command':'wrong'}}}))
            (child / '.cursor' / 'mcp.json').write_text(json.dumps({'mcpServers': {
                'other': {'command':'keep'}, 'agent-memory': {'command':'python', 'args':['--root', str(root)]}
            }}))
            with patch('cursor_sdk.Agent.create') as create, patch('agent_memory.studio_runtime.workspace_client'):
                create_agent(AgentConfig(str(child), 'grok-4.5', 'test-key'))
            servers = create.call_args.args[0]['mcp_servers']
            self.assertNotIn('parent-only', servers)
            self.assertEqual(servers['other'], {'command':'keep'})
            self.assertEqual(servers['agent-memory']['args'][-1], str(child))
            self.assertEqual(create.call_args.kwargs['local'].setting_sources, [])
            self.assertEqual(create.call_args.kwargs['local'].cwd, str(child))
