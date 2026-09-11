from pathlib import Path

root = Path(SPECPATH)
a = Analysis([str(root / 'desktop.py')], pathex=[str(root)],
             hiddenimports=['unittest.mock'], datas=[], binaries=[],
             hookspath=[], runtime_hooks=[str(root / 'v135_flatten_noop_hotfix.py')], excludes=[])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='KAYTRADE',
          debug=False, strip=False, upx=False, console=False,
          target_arch='x86_64', codesign_identity=None, entitlements_file=None)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='KAYTRADE')
bundle = BUNDLE(collection, name='KAYTRADE.app',icon=str(root / 'OKXLocal.icns'),
                bundle_identifier='design.kkay.kaytrade',
                info_plist={'CFBundleShortVersionString': '1.3.5',
                            'CFBundleVersion': '135',
                            'LSMinimumSystemVersion': '14.0',
                            'NSHighResolutionCapable': True})
