"""Recovery contracts: evidence retention, bounded transfer, and stale dependencies."""
from agent_memory.tests.helpers import MemoryTestCase


class HandoffTests(MemoryTestCase):
    def test_multiline_user_constraints_survive_fresh_client(self):
        from agent_memory import Memory
        text = 'Ship export\n\nKeep region eu-west-3.\nNever enable public access.'
        note = self.memory.save_user(text)
        other = Memory(self.path)
        self.assertEqual(other.find(ids=note)[0].message, text)
        self.assertIn('eu-west-3', other.render_packet('Resume export'))
        other._catalog.close()

    def test_changed_constraint_with_same_headline_is_not_deduplicated(self):
        a = self.memory.save_user('Ship export\nKeep region eu-west-3.')
        b = self.memory.save_user('Ship export\nKeep region eu-west-2.')
        self.assertNotEqual(a, b)
        packet = self.memory.render_packet('Resume export')
        self.assertIn('## Steering', packet)
        self.assertIn('eu-west-2', packet)

    def test_auto_handoff_retains_next_action(self):
        self.memory.save_user('Fix consumer')
        self.memory.save('Consumer patch implemented')
        self.memory.auto_save('Ready for validation\nNext: pytest -k redelivery')
        self.assertIn('Next: pytest -k redelivery', self.memory.render_packet())

    def test_explicit_handoff_survives_incidental_notes(self):
        note = self.memory.save('Parser patch ready\nNext: pytest -k escaped_quote', {'handoff': True})
        self.memory.save('README typo fixed')
        self.assertIn(note[:8], self.memory.render_packet('Continue parser'))
        self.assertIn('pytest -k escaped_quote', self.memory.render_packet('Continue parser'))

    def test_new_handoff_replaces_previous_one(self):
        old = self.memory.save('Next: test old parser', {'handoff': True})
        new = self.memory.save('Next: test new parser', {'handoff': True})
        packet = self.memory.render_packet('continue')
        self.assertIn(new[:8], packet)
        self.assertNotIn(old[:8], packet)

    def test_note_only_dependency_detects_unsaved_change(self):
        self.file.write_text('workers=6')
        self.memory.save('Initial config')
        note = self.memory.save('Workers 6 verified', {'files': ['work.txt'], 'handoff': True})
        self.file.write_text('workers=18')
        self.assertIn(f'[{note[:8]}] [stale]', self.memory.render_packet('workers'))

    def test_latest_snapshot_detects_unsaved_change(self):
        self.file.write_text('workers=6')
        note = self.memory.save('Workers 6 verified')
        self.file.write_text('workers=18')
        self.assertIn(f'[{note[:8]}] [stale]', self.memory.render_packet('workers'))

    def test_priority_line_beyond_old_body_cap_is_visible(self):
        self.memory.save('Migration findings\n' + 'Ordinary progress without decision changes.\n' * 30 + 'Rollback: migrate --restore cp-17')
        self.assertIn('Rollback: migrate --restore cp-17', self.memory.render_packet('rollback'))

    def test_full_find_can_retrieve_tail_without_expanding_default(self):
        note = self.memory.save('Investigation\n' + 'log ' * 400 + '\nTAIL-EVIDENCE')
        args = {'action': 'find', 'ids': [note]}
        self.assertNotIn('TAIL-EVIDENCE', self.memory.call('memory', args))
        self.assertIn('TAIL-EVIDENCE', self.memory.call('memory', {**args, 'full': True}))

    def test_packet_is_bounded_with_many_questions_and_long_notes(self):
        self.memory.save_user('Investigate parser\n' + 'User constraint. ' * 1000)
        for i in range(18):
            self.memory.open_question(f'Question {i}: ' + 'pending evidence ' * 40)
        packet = self.memory.render_packet('Continue parser', max_chars=700)
        self.assertLessEqual(len(packet), 700)
        self.assertIn('## Goal', packet)
        self.assertIn('omitted', packet)

    def test_packet_excludes_duplicate_handoff_from_related(self):
        note = self.memory.save('Maple timeout 45 seconds')
        self.assertEqual(self.memory.render_packet('Maple timeout').count(note[:8]), 1)

    def test_transcript_noise_does_not_crowd_out_evidence(self):
        relevant = self.memory.save('Vela worker retry uses a durable idempotency token.')
        for i in range(40):
            self.memory.auto_save(f'Vela retry update {i}')
        self.memory.save('Unrelated current task')
        found = self.memory.related_notes('Vela retry')
        self.assertIn(relevant, [item.id for item in found])

    def test_full_transcripts_do_not_expand_embedding_inputs(self):
        from agent_memory.catalog import canonical_document
        payload = {"auto": True, "agent": "worker-a"}
        short = canonical_document("Checkpoint", payload)
        long = canonical_document("Checkpoint\n" + "private-tail " * 1000, payload)
        self.assertEqual(short, long)
        note = self.memory.auto_save("Checkpoint\nunique_evidence_tail")
        self.assertIn("unique_evidence_tail", self.memory.find(ids=note)[0].message)
        self.assertEqual(self.memory.find(text="unique_evidence_tail")[0].id, note)

    def test_excerpt_signals_omitted_constraints(self):
        self.memory.save_user("Finish task\nConstraint: " + "required " * 100)
        packet = self.memory.render_packet()
        self.assertIn('[more]', packet)
        self.assertLessEqual(len(packet), 1200)
