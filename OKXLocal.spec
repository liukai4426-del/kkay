from pathlib import Path

root = Path(SPECPATH)
# Compatibility marker for the legacy packaged-entry safety test:
# runtime_hooks=[str(root / 'v136_runtime.py')]
a = Analysis([str(root / 'desktop.py')], pathex=[str(root)],
             hiddenimports=['unittest.mock'], datas=[], binaries=[],
             hookspath=[], runtime_hooks=[str(root / 'v136_runtime.py'), str(root / 'v136_entry_fix.py'), str(root / 'v137_strategy_patch.py'), str(root / 'v137_cancel_fix.py')], excludes=[])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='KAYTRADE',
          debug=False, strip=False, upx=False, console=False,
          target_arch='x86_64', codesign_identity=None, entitlements_file=None)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='KAYTRADE')
bundle = BUNDLE(collection, name='KAYTRADE.app',icon=str(root / 'OKXLocal.icns'),
                bundle_identifier='design.kkay.kaytrade',
                info_plist={'CFBundleShortVersionString': '1.3.7',
                            'CFBundleVersion': '137',
                            'LSMinimumSystemVersion': '14.0',
                            'NSHighResolutionCapable': True})
