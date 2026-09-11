import inspect
import unittest

import v145_layout_fix_patch as v145


class V145LayoutFixTests(unittest.TestCase):
    def test_version(self):
        self.assertEqual(v145.V145_VERSION,'1.4.5')

    def test_main_page_uses_full_relative_width(self):
        source=inspect.getsource(v145)
        self.assertIn("relwidth=1.0",source)
        self.assertIn("relheight=1.0",source)
        self.assertIn("_v145_main_layout",source)
        self.assertIn("selected.lift()",source)

    def test_connection_page_is_rebuilt_as_expanding_card(self):
        source=inspect.getsource(v145._rebuild_connection_page)
        self.assertIn("surface.pack(fill='both',expand=True",source)
        self.assertIn("body.grid_columnconfigure(1,weight=1)",source)
        self.assertIn("sticky='ew'",source)
        self.assertIn("测试连接",source)
        self.assertIn("网络自检",source)

    def test_result_modal_is_centered_without_black_outer_canvas(self):
        source=inspect.getsource(v145._centered_modal)
        self.assertIn("win.configure(bg=app.PANEL",source)
        self.assertNotIn("tk.Canvas(win",source)
        self.assertGreaterEqual(source.count("anchor='center'"),4)
        self.assertIn("justify='center'",source)
        self.assertIn("_v145_no_outer_black_frame",source)
        self.assertIn("_v145_all_centered",source)

    def test_header_brand_and_logo_are_slightly_larger(self):
        self.assertEqual(v145.BRAND_TITLE_FONT,('Helvetica',28,'bold'))
        self.assertEqual(v145.BRAND_META_FONT,('Helvetica',14))
        self.assertEqual(v145.HEADER_LOGO_CANVAS,58)
        self.assertEqual(v145.HEADER_LOGO_IMAGE,52)
        self.assertAlmostEqual(v145.LOGO_CROP_RATIO,0.02)
        source=inspect.getsource(v145._header_logo_mark)
        self.assertIn("Image.Resampling.LANCZOS",source)
        self.assertIn("_crop_logo",source)

    def test_connection_badge_uses_normal_background_and_connected_green_text(self):
        source=inspect.getsource(v145._flatten_connection_badge)
        self.assertIn("options['bg']=app.BG",source)
        self.assertIn("text.startswith('已连接：')",source)
        self.assertIn("app.GREEN",source)
        self.assertIn("_v145_connection_badge_flat",source)

    def test_runtime_note_is_small_gray_and_panel_right_aligned(self):
        source=inspect.getsource(v145._style_runtime_note)
        self.assertEqual(v145.RUNTIME_NOTE_COLOR,'#8b9ca6')
        self.assertEqual(v145.RUNTIME_NOTE_FONT,('Helvetica',9))
        self.assertIn("anchor='e'",source)
        self.assertIn("justify='right'",source)
        self.assertIn("padx=MAIN_PAD",source)

    def test_three_header_lines_share_right_alignment_contract(self):
        source=inspect.getsource(v145._align_right_header_lines)
        self.assertIn("_v142_status_label",source)
        self.assertIn("owner.equity",source)
        self.assertIn("_v145_runtime_note",source)
        self.assertIn("justify='right'",source)

    def test_signal_summary_is_smaller_and_left_aligned(self):
        self.assertEqual(v145.SIGNAL_FONT,('Helvetica',11))
        source=inspect.getsource(v145._style_signal_summary)
        self.assertIn("V145SignalSummary.TLabel",source)
        self.assertIn("anchor='w'",source)
        self.assertIn("padx=(4,0)",source)

    def test_history_chart_removes_only_axis_title_words(self):
        self.assertEqual(v145.HISTORY_AXIS_TITLES,{'收益率','日期'})
        source=inspect.getsource(v145.apply)
        self.assertIn("itemcget(item,'text') in HISTORY_AXIS_TITLES",source)
        self.assertIn("previous_draw_curve",source)

    def test_width_fix_is_scoped_to_main_tabs(self):
        source=inspect.getsource(v145)
        self.assertIn("Nested score/indicator tabs keep the previous compact layout",source)
        self.assertIn("return previous_tabs_select(self,page)",source)

    def test_strategy_is_inherited_not_rewritten(self):
        source=inspect.getsource(v145)
        self.assertIn("from v144_ui_polish_patch import apply as apply_v144",source)
        for token in ('score_threshold=','tpTriggerPx','slTriggerPx','first_signal_notional='):
            self.assertNotIn(token,source)


if __name__=='__main__':
    unittest.main()
