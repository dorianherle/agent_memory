"""Regressions for failures found in the private source review."""
import unittest
import numpy as np

from agent_memory.catalog import vector_bytes
from agent_memory.tests.helpers import MemoryTestCase


class EmbeddingValidationTests(unittest.TestCase):
    def test_nonfinite_embeddings_are_rejected(self):
        for vector in ([float('nan'), 1], [float('inf'), 1], [1, -float('inf')]):
            with self.subTest(vector=vector), self.assertRaises(ValueError):
                vector_bytes(vector)

    def test_embedding_must_be_one_dimensional(self):
        with self.assertRaises(ValueError):
            vector_bytes([[1, 2], [3, 4]])

    def test_large_valid_embedding_keeps_unit_norm(self):
        dimensions, raw = vector_bytes([1e30, -1e30])
        vector = np.frombuffer(raw, dtype=np.float32)
        self.assertEqual(dimensions, 2)
        self.assertTrue(np.isfinite(vector).all())
        self.assertAlmostEqual(float(np.linalg.norm(vector)), 1, places=6)


class EvidenceRecoveryAuditTests(MemoryTestCase):
    def test_fts_filter_does_not_lose_low_ranked_explicit_id(self):
        target = self.memory.save('needle ' + 'ordinary context ' * 100)
        for index in range(24):
            self.memory.save(f'needle needle needle {index}')
        self.assertEqual([item.id for item in self.memory.find('needle', ids=target, k=1)], [target])

    def test_other_branch_hits_do_not_hide_current_branch_evidence(self):
        target = self.memory.save('needle ' + 'ordinary context ' * 100)
        for index in range(24):
            self.memory.save(f'needle needle needle {index}')
        self.memory.branch(target, 'audit-side', summary='Leave the first branch.')
        self.assertEqual([item.id for item in self.memory.find('needle', k=1)], [target])

    def test_previously_missing_dependency_becomes_stale_when_created(self):
        target = self.memory.save('Calibration file is missing.\nNext: obtain calibration.',
                                  {'files': ['calibration.json'], 'handoff': True})
        (self.path / 'calibration.json').write_text('{"rate": 12}')
        self.assertIn(f'[{target[:8]}] [stale]', self.memory.render_packet('calibration'))


class ExactNoteStorageTests(MemoryTestCase):
    def test_save_preserves_exact_whitespace_across_reopen(self):
        text = '  Code example\n\n\n```python\nvalue = """alpha  \n\n\nbeta"""\n```\n\n'
        target = self.memory.save(text, {'kind': 'exact-code'})
        self.assertEqual(self.memory.find(ids=target)[0].message, text)
        from agent_memory import Memory
        reopened = Memory(self.path)
        self.assertEqual(reopened.find(ids=target)[0].message, text)
        reopened._catalog.close()

    def test_legacy_record_keeps_previous_normalization(self):
        from agent_memory.core import FIELD_SEPARATOR, PAYLOAD_PREFIX
        raw = FIELD_SEPARATOR.join(['legacy-id', '', '2026-10-13T12:00:00+00:00',
                                    '  legacy note  \n\n' + PAYLOAD_PREFIX + '{}'])
        self.assertEqual(self.memory._parse_record(raw)['message'], 'legacy note')

    def test_format_marker_does_not_enter_public_payload(self):
        target = self.memory.save('  exact note  ', {'kind': 'note'})
        item = self.memory.find(ids=target)[0]
        self.assertEqual(item.message, '  exact note  ')
        self.assertNotIn('_agent_memory', item.payload)
        self.assertEqual(item.payload['kind'], 'note')

    def test_record_separator_in_note_round_trips(self):
        text = 'Evidence before\x02Evidence after\n'
        target = self.memory.save(text)
        self.assertEqual(self.memory.find(ids=target)[0].message, text)

    def test_null_character_in_note_round_trips(self):
        text = 'Evidence before\x00Evidence after\n'
        target = self.memory.save(text)
        self.assertEqual(self.memory.find(ids=target)[0].message, text)

    def test_legacy_record_separator_can_be_recovered(self):
        from agent_memory.core import PAYLOAD_PREFIX
        text = 'Legacy evidence before\x02Legacy evidence after'
        body = text + '\n\n' + PAYLOAD_PREFIX + '{"agent":"legacy"}'
        self.memory._run('commit', '--allow-empty', '--cleanup=verbatim', '-m', body)
        self.assertEqual(self.memory.state()[0].message, text)

    def test_carriage_return_line_endings_round_trip(self):
        text = 'Windows line\r\nNext line\rTerminal newline\r\n'
        target = self.memory.save(text)
        self.assertEqual(self.memory.find(ids=target)[0].message, text)

    def test_older_catalog_is_rebuilt_with_exact_git_text(self):
        text = 'Exact Windows line\r\nNext line\r\n'
        target = self.memory.save(text)
        self.memory._catalog.connection.execute('UPDATE memories SET message=? WHERE id=?',
                                               (text.replace('\r\n', '\n'), target))
        self.memory._catalog.connection.execute('PRAGMA user_version=4')
        self.memory._catalog.connection.commit()
        self.memory._catalog.close()
        from agent_memory import Memory
        reopened = Memory(self.path)
        self.assertEqual(reopened.find(ids=target)[0].message, text)
        reopened._catalog.close()

    def test_unicode_noncharacters_do_not_change_other_text(self):
        text = 'Unicode: café 漢字 \ufffe \uffff \ufdd0 😀\n'
        target = self.memory.save(text)
        self.assertEqual(self.memory.find(ids=target)[0].message, text)

    def test_find_tool_promotes_covering_summary_and_marks_stale(self):
        cfg = self.path / 'config.py'
        cfg.write_text('TTL = 3600\n')
        old_id = self.memory.save('Auth token TTL is 3600s in config.py', {'files': ['config.py']})
        cfg.write_text('TTL = 86400\n')
        self.memory.save('Dead end: 3600s dropped sessions; changed TTL to 86400s in config.py', {'files': ['config.py']})
        sum_id = self.memory.summarize('Verified: Auth token TTL is 86400s in config.py')
        cfg.write_text('TTL = 172800\n')
        out = self.memory.call('memory', {'action': 'find', 'query': 'Auth token TTL config.py', 'k': 1})
        self.assertTrue(out.splitlines()[0].startswith(f'[{sum_id[:8]}] [stale]'))
        self.assertIn(old_id[:8], out)

    def test_session_start_injects_packet_and_turn_two_updates_query(self):
        import os
        from unittest.mock import patch
        from agent_memory.cursor.hook import handle

        with patch.dict(os.environ, {'AGENT_MEMORY_ROOT': str(self.path)}):
            handle('beforeSubmitPrompt', {'prompt': 'Initial setup of monorepo'})
            self.memory.save('Stripe webhook signature uses HMAC-SHA256 in billing/webhook.py')
            self.memory.save('Logger uses JSON formatter')
            handle('beforeSubmitPrompt', {'prompt': 'Fix the Stripe webhook HMAC signature bug'})
            started = handle('sessionStart', {'session_id': 's1'})
            self.assertIn('# MEMORY STATE', started['additional_context'])
            self.assertIn('Stripe webhook', started['additional_context'])

    def test_save_does_not_fail_when_provider_env_lacks_api_key(self):
        import os
        from unittest.mock import patch

        with patch.dict(
            os.environ,
            {'AGENT_MEMORY_PROVIDER': 'agent_memory.providers.gemini:GeminiProvider'},
            clear=False,
        ):
            os.environ.pop('GEMINI_API_KEY', None)
            item_id = self.memory.save('Saved offline without API key')
            self.assertTrue(item_id)

