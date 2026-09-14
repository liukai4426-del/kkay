from pathlib import Path

root = Path(SPECPATH)
# Compatibility marker for the legacy packaged-entry safety test:
# runtime_hooks=[str(root / 'v136_runtime.py')]
# asset: assets/kaytrade-v142-logo.png
# Build1551 deliberately uses ONE runtime hook.  That hook imports/applies the
# complete inherited patch chain as normal Python modules exactly once.  Listing
# every historical patch as its own runtime hook as well creates duplicate module
# identities inside PyInstaller and can form cyclic App.__init__ wrapper chains.
a = Analysis([str(root / 'desktop.py')], pathex=[str(root)],
             hiddenimports=['unittest.mock', 'v152_model', 'v153_model', 'v154_model', 'v154_limit_log_fix', 'v154_record_card_boll_fix', 'v154_fixed_card_precision_fix', 'v154_build1543_patch', 'v154_build1543_ui_cleanup', 'v154_build1544_patch', 'v155_update_patch', 'v155_build1551_patch'], datas=[(str(root / 'assets' / 'kaytrade-v142-logo.png'),'assets')], binaries=[],
             hookspath=[], runtime_hooks=[str(root / 'v155_build1551_patch.py')], excludes=[])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='KAYTRADE',
          debug=False, strip=False, upx=False, console=False,
          target_arch='x86_64', codesign_identity=None, entitlements_file=None)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='KAYTRADE')
bundle = BUNDLE(collection, name='KAYTRADE.app',icon=str(root / 'OKXLocal.icns'),
                bundle_identifier='design.kkay.kaytrade',
                info_plist={'CFBundleShortVersionString': '1.5.5',
                            'CFBundleVersion': '1551',
                            'LSMinimumSystemVersion': '14.0',
                            'NSHighResolutionCapable': True})
