from pathlib import Path

root = Path(SPECPATH)
# Compatibility marker for the legacy packaged-entry safety test:
# runtime_hooks=[str(root / 'v136_runtime.py')]
# asset: assets/kaytrade-v142-logo.png
a = Analysis([str(root / 'desktop.py')], pathex=[str(root)],
             hiddenimports=['unittest.mock', 'v152_model', 'v153_model', 'v154_model', 'v154_limit_log_fix', 'v154_log_layout_fix'], datas=[(str(root / 'assets' / 'kaytrade-v142-logo.png'),'assets')], binaries=[],
             hookspath=[], runtime_hooks=[str(root / 'v136_runtime.py'), str(root / 'v136_entry_fix.py'), str(root / 'v137_strategy_patch.py'), str(root / 'v137_cancel_fix.py'), str(root / 'v138_strategy_patch.py'), str(root / 'v139_position_patch.py'), str(root / 'v140_score_dialog_patch.py'), str(root / 'v141_dialog_signal_positions_patch.py'), str(root / 'v141_history_return_chart_patch.py'), str(root / 'v141_exit_limit_patch.py'), str(root / 'v142_visual_patch.py'), str(root / 'v143_score_arbitration_patch.py'), str(root / 'v143_visual_polish_patch.py'), str(root / 'v144_ui_polish_patch.py'), str(root / 'v145_layout_fix_patch.py'), str(root / 'v146_ui_log_polish_patch.py'), str(root / 'v146_ui_fix_patch.py'), str(root / 'v147_strategy_patch.py'), str(root / 'v150_strategy_patch.py'), str(root / 'v151_strategy_patch.py'), str(root / 'v152_strategy_patch.py'), str(root / 'v152_safety_patch.py'), str(root / 'v152_ui_busy_fix.py'), str(root / 'v153_runtime_patch.py'), str(root / 'v154_runtime_patch.py'), str(root / 'v154_limit_log_fix.py'), str(root / 'v154_log_layout_fix.py')], excludes=[])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='KAYTRADE',
          debug=False, strip=False, upx=False, console=False,
          target_arch='x86_64', codesign_identity=None, entitlements_file=None)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='KAYTRADE')
bundle = BUNDLE(collection, name='KAYTRADE.app',icon=str(root / 'OKXLocal.icns'),
                bundle_identifier='design.kkay.kaytrade',
                info_plist={'CFBundleShortVersionString': '1.5.4',
                            'CFBundleVersion': '1541',
                            'LSMinimumSystemVersion': '14.0',
                            'NSHighResolutionCapable': True})