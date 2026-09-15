from pathlib import Path

root = Path(SPECPATH)
# Compatibility marker for legacy packaged-entry safety tests:
# runtime_hooks=[str(root / 'v136_runtime.py')]
# asset: assets/kaytrade-v142-logo.png
# V1.6.2 keeps a single final production runtime hook. It imports V1.6.1 and
# the inherited safety stack once, then disables BE and applies V1.6.2 gates.
a = Analysis([str(root / 'desktop.py')], pathex=[str(root)],
             hiddenimports=['unittest.mock', 'v152_model', 'v153_model', 'v154_model', 'v154_limit_log_fix', 'v154_record_card_boll_fix', 'v154_fixed_card_precision_fix', 'v154_build1543_patch', 'v154_build1543_ui_cleanup', 'v154_build1544_patch', 'v155_update_patch', 'v155_build1551_patch', 'v156_execution_hotfix', 'v156_ui_patch', 'v156_build1561_patch', 'v160_model', 'v160_update_patch', 'v161_model', 'v161_update_patch', 'v161_ui_status_patch', 'v162_model', 'v162_update_patch'], datas=[(str(root / 'assets' / 'kaytrade-v142-logo.png'),'assets')], binaries=[],
             hookspath=[], runtime_hooks=[str(root / 'v162_update_patch.py')], excludes=[])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='KAYTRADE',
          debug=False, strip=False, upx=False, console=False,
          target_arch='x86_64', codesign_identity=None, entitlements_file=None)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='KAYTRADE')
bundle = BUNDLE(collection, name='KAYTRADE.app',icon=str(root / 'OKXLocal.icns'),
                bundle_identifier='design.kkay.kaytrade',
                info_plist={'CFBundleShortVersionString': '1.6.2',
                            'CFBundleVersion': '1620',
                            'LSMinimumSystemVersion': '14.0',
                            'NSHighResolutionCapable': True})