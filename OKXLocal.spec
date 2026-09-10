from pathlib import Path

root = Path(SPECPATH)
a = Analysis([str(root / 'desktop.py')], pathex=[str(root)],
             hiddenimports=['unittest.mock'], datas=[], binaries=[],
             hookspath=[], runtime_hooks=[], excludes=[])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='OKXLocal',
          debug=False, strip=False, upx=False, console=False,
          target_arch='x86_64', codesign_identity=None, entitlements_file=None)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='OKXLocal')
bundle = BUNDLE(collection, name='OKXLocal.app',icon=str(root / 'OKXLocal.icns'),
                bundle_identifier='design.kkay.okxlocal',
                info_plist={'CFBundleShortVersionString': '1.2.1',
                            'CFBundleVersion': '121',
                            'LSMinimumSystemVersion': '14.0',
                            'NSHighResolutionCapable': True})
