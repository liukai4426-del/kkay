import inspect
import unittest

import v146_ui_log_polish_patch as v146


class V146UiLogPolishTests(unittest.TestCase):
    def test_version_and_scaling_constants(self):
        self.assertEqual(v146.V146_VERSION,'1.4.6')
        self.assertEqual(v146.ENERGY_BAR_SCALE,1.6)
        self.assertEqual(v146.ENERGY_BAR_HEIGHT,14)
        self.assertEqual(v146.BRAND_SCALE,1.2)
        self.assertEqual(v146.BRAND_TITLE_FONT,('Helvetica',34,'bold'))
        self.assertEqual(v146.BRAND_META_FONT,('Helvetica',17))
        self.assertEqual(v146.HEADER_LOGO_CANVAS,70)
        self.assertEqual(v146.HEADER_LOGO_IMAGE,62)

    def test_energy_bar_is_thicker_and_capsule_rounded(self):
        source=inspect.getsource(v146._energy_draw)
        self.assertIn('_aa_rounded_photo',source)
        self.assertIn('_capsule',source)
        self.assertIn('_v146_max_round',source)
        init_source=inspect.getsource(v146.apply)
        self.assertIn('requested*ENERGY_BAR_SCALE',init_source)
        self.assertIn('_v146_thickness_scale',init_source)

    def test_account_and_network_share_one_aligned_row(self):
        source=inspect.getsource(v146._align_account_network)
        self.assertIn("padding=(MAIN_PAD,0)",source)
        self.assertIn("account.pack(side='left'",source)
        self.assertIn("network.pack(side='left'",source)
        self.assertIn('_v146_status_inline',source)
        self.assertIn('_v146_status_row_aligned',source)

    def test_run_record_card_replaces_legacy_filter(self):
        source=inspect.getsource(v146._build_log_card)
        self.assertIn("text='运行记录'",source)
        self.assertIn("text='所有策略循环与安全拦截'",source)
        self.assertIn('old_filter.pack_forget()',source)
        self.assertIn('old_log.pack_forget()',source)
        self.assertIn('_v146_log_filter_removed',source)

    def test_status_dot_semantics(self):
        self.assertEqual(v146._log_state('alarm','anything'),('警报',v146.ALARM_COLOR))
        self.assertEqual(v146._log_state('log','等待最新K线'),('等待中',v146.WAIT_COLOR))
        self.assertEqual(v146._log_state('log','连接成功'),('正常运行',v146.NORMAL_COLOR))
        source=inspect.getsource(v146._render_log_row)
        self.assertIn("text='●'",source)

    def test_trading_rules_are_not_rewritten(self):
        source=inspect.getsource(v146)
        self.assertIn('from v145_layout_fix_patch import apply as apply_v145',source)
        for token in ('score_threshold=','tpTriggerPx','slTriggerPx','first_signal_notional=','max_signal_notional='):
            self.assertNotIn(token,source)


if __name__=='__main__':
    unittest.main()
