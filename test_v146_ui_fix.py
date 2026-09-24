import inspect
import unittest

import v146_ui_fix_patch as fix


class V146UiFixTests(unittest.TestCase):
    def test_fix_constants(self):
        self.assertEqual(fix.V146_FIX_VERSION,'1.4.6')
        self.assertEqual(fix.LOG_CARD_HEIGHT,130)
        self.assertEqual(fix.STATUS_LEFT_PAD,19)
        self.assertGreater(fix.LOG_HISTORY_LIMIT,3)

    def test_connection_result_box_is_hidden_not_destroyed(self):
        source=inspect.getsource(fix._hide_connection_result_box)
        self.assertIn('grid_remove()',source)
        self.assertNotIn('.destroy()',source)
        self.assertIn('_v146_fix_connection_box_removed',source)

    def test_status_aligns_to_logo_artwork_edge(self):
        source=inspect.getsource(fix._realign_status_to_logo)
        self.assertIn('STATUS_LEFT_PAD',source)
        self.assertIn("padding=(STATUS_LEFT_PAD,0)",source)
        self.assertIn('_v146_fix_status_logo_aligned',source)

    def test_risk_and_execution_use_same_scrollable_page(self):
        risk=inspect.getsource(fix._rebuild_risk_page)
        execution=inspect.getsource(fix._rebuild_execution_page)
        helper=inspect.getsource(fix._scroll_surface)
        self.assertIn('app.ScrollablePage',helper)
        self.assertIn('_v146_fix_risk_scrollbar',risk)
        self.assertIn('_v146_fix_execution_scrollbar',execution)
        self.assertIn("'first_signal_notional'",risk)
        self.assertIn("'third_signal_notional'",risk)
        self.assertIn("'score_threshold'",execution)
        self.assertIn("disabled=(key=='score_threshold')",execution)

    def test_run_record_is_half_height_and_scrollable(self):
        source=inspect.getsource(fix._build_compact_log_card)
        render=inspect.getsource(fix._render_logs)
        self.assertIn('height=LOG_CARD_HEIGHT',source)
        self.assertIn('app.WideScrollbar',source)
        self.assertIn('yscrollcommand=scroll.set',source)
        self.assertIn('_v146_fix_log_scrollbar',source)
        self.assertIn('LOG_HISTORY_LIMIT',render)

    def test_patch_remains_ui_only(self):
        source=inspect.getsource(fix)
        for token in ('tpTriggerPx','slTriggerPx','score_threshold=','submit_order(','place_order('):
            self.assertNotIn(token,source)


if __name__=='__main__':
    unittest.main()
