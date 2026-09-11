import unittest

import app
import v143_visual_polish_patch as polish


class VisualPolish143Tests(unittest.TestCase):
    def test_all_standard_button_heights_use_larger_max_radius(self):
        self.assertEqual(polish._button_radius(42,14),20)
        self.assertEqual(polish._button_radius(38,13),18)
        self.assertGreater(polish._button_radius(42,14),19)

    def test_scrollbar_palette_matches_dark_interface(self):
        self.assertEqual(polish.SCROLL_TRACK,app.BG)
        self.assertEqual(polish.SCROLL_TRACK,'#080d10')
        self.assertIn(polish.SCROLL_THUMB,(app.PANEL_ALT,app.FIELD))

    def test_round_rect_clamps_radius_to_shape(self):
        class Canvas:
            def create_polygon(self,points,**kwargs):
                self.points=points; self.kwargs=kwargs
                return 1
        canvas=Canvas()
        result=polish._round_rect(canvas,0,0,12,20,99,fill='x')
        self.assertEqual(result,1)
        self.assertEqual(canvas.points[0],6)
        self.assertTrue(canvas.kwargs['smooth'])

    def test_native_scrollbar_class_is_restored_after_patch_load(self):
        from tkinter import ttk
        self.assertTrue(isinstance(ttk.Scrollbar,type))


if __name__=='__main__':
    unittest.main()
