from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH)
# Compatibility marker for legacy packaged-entry safety tests:
# runtime_hooks=[str(root / 'v136_runtime.py')]
# V1.6.8 Build1681 keeps Build1680 strategy/risk/order semantics unchanged and
# repairs the final runtime/log boundary: legacy BOLL/window/copy normalization
# plus clearer Algo read-side resynchronization diagnostics.
hidden = ['unittest.mock', 'v152_model', 'v153_model', 'v154_model', 'v154_limit_log_fix', 'v154_record_card_boll_fix', 'v154_fixed_card_precision_fix', 'v154_build1543_patch', 'v154_build1543_ui_cleanup', 'v154_build1544_patch', 'v155_update_patch', 'v155_build1551_patch', 'v156_execution_hotfix', 'v156_ui_patch', 'v156_build1561_patch', 'v160_model', 'v160_update_patch', 'v161_model', 'v161_update_patch', 'v161_ui_status_patch', 'v162_model', 'v162_update_patch', 'v163_model', 'v163_update_patch', 'v163_ui_patch', 'v164_model', 'v164_update_patch', 'v164_ui_patch', 'v165_model', 'v165_update_patch', 'v165_ui_patch', 'v165_15m_outer_patch', 'v166_ui_patch', 'v166_readonly_resilience_patch', 'v167_strategy_patch', 'v167_algo_sync_patch', 'v167_15m_boll_only_fix', 'v168_update_patch', 'v168_build1681_patch', 'v170_update_patch', 'v170_build1701_patch', 'v170_build1702_patch']
hidden += collect_submodules('websocket')
a = Analysis([str(root / 'desktop.py')], pathex=[str(root)],
             hiddenimports=hidden, datas=[(str(root / 'assets' / 'kaytrade-v142-logo.png'),'assets')], binaries=[],
             hookspath=[], runtime_hooks=[str(root / 'v165_update_patch.py')], excludes=[])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='KAYTRADE',
          debug=False, strip=False, upx=False, console=False,
          target_arch='x86_64', codesign_identity=None, entitlements_file=None)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='KAYTRADE')
bundle = BUNDLE(collection, name='KAYTRADE.app',icon=str(root / 'OKXLocal.icns'),
                bundle_identifier='design.kkay.kaytrade',
                info_plist={'CFBundleShortVersionString': '1.7.0',
                            'CFBundleVersion': '1702',
                            'LSMinimumSystemVersion': '14.0',
                            'NSHighResolutionCapable': True})
