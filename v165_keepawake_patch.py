"""V1.6.5 macOS keep-awake integration.

When the user authorizes automatic trading, keep the Mac awake so the local
engine can continue refreshing markets and evaluating entries. The display is
allowed to sleep. Stopping automatic trading or quitting releases the assertion.
"""
import subprocess
import sys

import app


def _proc_alive(proc):
    if proc is None:
        return False
    try:
        return proc.poll() is None
    except Exception:
        return False


def _start_keepawake(self):
    if sys.platform != 'darwin':
        return False
    current = getattr(self, '_v165_keepawake_proc', None)
    if _proc_alive(current):
        return True
    try:
        proc = subprocess.Popen(
            ['/usr/bin/caffeinate', '-i', '-m'],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except Exception as exc:
        self._v165_keepawake_proc = None
        try:
            self.emit('log', 'Mac防睡眠启动失败：'+str(exc)+'；自动交易仍可运行，但系统进入睡眠后程序会暂停')
        except Exception:
            pass
        return False
    self._v165_keepawake_proc = proc
    try:
        self.emit('log', 'Mac防睡眠已启用：自动交易期间保持系统唤醒；屏幕仍可自动熄灭')
    except Exception:
        pass
    return True


def _stop_keepawake(self, reason=''):
    proc = getattr(self, '_v165_keepawake_proc', None)
    self._v165_keepawake_proc = None
    if not _proc_alive(proc):
        return False
    try:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except Exception:
            proc.kill()
    except Exception:
        pass
    try:
        suffix = ('（'+reason+'）') if reason else ''
        self.emit('log', 'Mac防睡眠已解除'+suffix+'；系统恢复正常睡眠策略')
    except Exception:
        pass
    return True


def apply():
    if getattr(app.App, '_kaytrade_v165_keepawake_applied', False):
        return

    original_init = app.App.__init__
    original_arm = app.App.arm
    original_stop = app.App.stop
    original_quit = app.App.quit

    def app_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._v165_keepawake_proc = None

    def app_arm(self, *args, **kwargs):
        result = original_arm(self, *args, **kwargs)
        engine_obj = getattr(self, 'engine', None)
        if engine_obj is not None and not getattr(self, 'network_paused', False):
            _start_keepawake(self)
        return result

    def app_stop(self, *args, **kwargs):
        _stop_keepawake(self, '已停止自动交易')
        return original_stop(self, *args, **kwargs)

    def app_quit(self, *args, **kwargs):
        result = original_quit(self, *args, **kwargs)
        try:
            exists = bool(self.root.winfo_exists())
        except Exception:
            exists = False
        if not exists:
            _stop_keepawake(self, '程序退出')
        return result

    app.App.__init__ = app_init
    app.App.arm = app_arm
    app.App.stop = app_stop
    app.App.quit = app_quit
    app.App._kaytrade_v165_keepawake_applied = True


apply()
