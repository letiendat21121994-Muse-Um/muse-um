"""Optional pywin32 service entry point; importable on non-Windows hosts."""
from __future__ import annotations

import asyncio
import sys
import threading

from .client import require_relay_url
from .daemon import Daemon
from .state import get_state_dir


def _service_class():
    import win32service
    import win32serviceutil

    class MuseUmDaemonService(win32serviceutil.ServiceFramework):
        _svc_name_ = "MuseUmDaemon"
        _svc_display_name_ = "Muse UM Daemon"
        _svc_description_ = "Keeps the Muse UM client connected to its Relay."

        def __init__(self, args):
            super().__init__(args)
            self.loop = None
            self.daemon = None
            self.stopping = threading.Event()

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self.stopping.set()
            if self.loop is not None and self.daemon is not None:
                self.loop.call_soon_threadsafe(self.daemon._stop.set)

        def SvcDoRun(self):
            self.loop = asyncio.ProactorEventLoop()
            asyncio.set_event_loop(self.loop)
            try:
                self.daemon = Daemon(require_relay_url(), get_state_dir())
                if self.stopping.is_set():
                    self.daemon._stop.set()
                self.loop.run_until_complete(self.daemon.run())
            finally:
                self.loop.run_until_complete(self.loop.shutdown_asyncgens())
                self.loop.close()

    return MuseUmDaemonService


def main() -> int:
    if sys.platform != "win32":
        print("MuseUmDaemon service requires Windows and pywin32.", file=sys.stderr)
        return 1
    try:
        import win32serviceutil
        service_class = _service_class()
    except ImportError:
        print("MuseUmDaemon service requires pywin32; install requirements.txt.", file=sys.stderr)
        return 1
    win32serviceutil.HandleCommandLine(
        service_class, serviceClassString="tools.muse_client.winservice.MuseUmDaemonService"
    )
    return 0


if sys.platform == "win32":
    try:
        MuseUmDaemonService = _service_class()
    except ImportError:
        pass


if __name__ == "__main__":
    sys.exit(main())
