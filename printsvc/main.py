"""
PrintSVC - Main entry point.
Connects printer, starts mDNS discovery and IPP server.
"""
import argparse
import logging
import os
import signal
import sys
import threading
import time
import webbrowser

import win32api
import win32con
import win32gui

from . import winprint
from .config import load_config
from .discovery import MDNSService, SSDPListener
from .logger import clear_log_file, setup_logging
from .netutils import get_local_ip
from .server import IPPServer, job_store

# Global flags
running = True
server = None
mdns = None
ssdp = None
app_state = None
tray_controller = None


class TrayApp:
    def __init__(self, config, log):
        self.config = config
        self.log = log
        self._server = None
        self._mdns = None
        self._ssdp = None

    @property
    def ipp_port(self):
        return self.config.get("ipp_port", 631)

    @property
    def status_url(self):
        return f"http://localhost:{self.ipp_port}/"

    @property
    def log_file(self):
        return self.config.get("log_file", "")

    def start_services(self):
        global server, mdns, ssdp

        self._server = IPPServer(host=self.config.get("listen_address", "0.0.0.0"), port=self.ipp_port)
        if not self._server.start():
            return False
        server = self._server

        if self.config.get("mDNS_enabled", True):
            advertised_name = self.config.get("service_name", "PrintSVC")
            self._mdns = MDNSService(
                hostname=advertised_name.replace(" ", "-"),
                port=self.ipp_port,
                service_name=advertised_name,
                printer_name=advertised_name,
            )
            self._mdns.start()
            mdns = self._mdns

            self._ssdp = SSDPListener(
                port=self.ipp_port,
                server_name=advertised_name,
                printer_name=advertised_name,
            )
            self._ssdp.start()
            ssdp = self._ssdp

        return True

    def stop_services(self):
        global server, mdns, ssdp

        if self._mdns:
            self._mdns.stop()
            self._mdns = None
            mdns = None
        if self._ssdp:
            self._ssdp.stop()
            self._ssdp = None
            ssdp = None
        if self._server:
            self._server.stop()
            self._server = None
            server = None

    def open_window(self):
        webbrowser.open(self.status_url)

    def clear_logs(self):
        if not self.log_file:
            self.log.warning("No log file configured, nothing to clear")
            return False
        clear_log_file(self.log_file)
        self.log.info("Log file cleared: %s", self.log_file)
        return True

    def shutdown(self):
        global running
        running = False
        self.stop_services()


class TrayController:
    CLASS_NAME = "PrintSVCTrayWindow"
    WM_TRAYICON = win32con.WM_USER + 20
    ID_MENU_OPEN = 1001
    ID_MENU_CLEAR = 1002
    ID_MENU_EXIT = 1003

    def __init__(self, app):
        self.app = app
        self.hwnd = None
        self.hicon = win32gui.LoadIcon(0, win32con.IDI_APPLICATION)
        self._ready = threading.Event()
        self._thread = None
        self._error = None

    def start(self):
        self._thread = threading.Thread(target=self._thread_main, daemon=True, name="PrintSVCTray")
        self._thread.start()
        self._ready.wait(timeout=5.0)
        return self.hwnd is not None and self._error is None

    def join(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)

    def _thread_main(self):
        try:
            self._create_window()
        except Exception as exc:
            self._error = exc
            self._ready.set()
            return

        self._ready.set()
        win32gui.PumpMessages()

    def request_shutdown(self):
        self.app.shutdown()
        if self.hwnd:
            win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)

    def _create_window(self):
        message_map = {
            win32con.WM_CLOSE: self._on_close,
            win32con.WM_DESTROY: self._on_destroy,
            win32con.WM_COMMAND: self._on_command,
            self.WM_TRAYICON: self._on_tray_icon,
        }

        wc = win32gui.WNDCLASS()
        wc.hInstance = win32api.GetModuleHandle(None)
        wc.lpszClassName = self.CLASS_NAME
        wc.lpfnWndProc = message_map

        try:
            class_atom = win32gui.RegisterClass(wc)
        except win32gui.error:
            class_atom = self.CLASS_NAME

        self.hwnd = win32gui.CreateWindow(
            class_atom,
            self.CLASS_NAME,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            wc.hInstance,
            None,
        )
        self._add_tray_icon()

    def _add_tray_icon(self):
        tip = "PrintSVC"
        flags = win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP
        nid = (self.hwnd, 0, flags, self.WM_TRAYICON, self.hicon, tip)
        win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, nid)

    def _remove_tray_icon(self):
        if self.hwnd:
            try:
                win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (self.hwnd, 0))
            except win32gui.error:
                pass

    def _show_menu(self):
        menu = win32gui.CreatePopupMenu()
        try:
            win32gui.AppendMenu(menu, win32con.MF_STRING, self.ID_MENU_OPEN, "打开窗口")
            win32gui.AppendMenu(menu, win32con.MF_STRING, self.ID_MENU_CLEAR, "清空日志")
            win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
            win32gui.AppendMenu(menu, win32con.MF_STRING, self.ID_MENU_EXIT, "关闭程序")

            x, y = win32gui.GetCursorPos()
            win32gui.SetForegroundWindow(self.hwnd)
            win32gui.TrackPopupMenu(
                menu,
                win32con.TPM_LEFTALIGN | win32con.TPM_RIGHTBUTTON,
                x,
                y,
                0,
                self.hwnd,
                None,
            )
            win32gui.PostMessage(self.hwnd, win32con.WM_NULL, 0, 0)
        finally:
            win32gui.DestroyMenu(menu)

    def _on_tray_icon(self, hwnd, msg, wparam, lparam):
        if lparam == win32con.WM_RBUTTONUP:
            self._show_menu()
        elif lparam == win32con.WM_LBUTTONDBLCLK:
            self.app.open_window()
        return True

    def _on_command(self, hwnd, msg, wparam, lparam):
        cmd_id = int(wparam) & 0xFFFF
        if cmd_id == self.ID_MENU_OPEN:
            self.app.open_window()
        elif cmd_id == self.ID_MENU_CLEAR:
            self.app.clear_logs()
        elif cmd_id == self.ID_MENU_EXIT:
            self.request_shutdown()
        return 0

    def _on_close(self, hwnd, msg, wparam, lparam):
        win32gui.DestroyWindow(hwnd)
        return 0

    def _on_destroy(self, hwnd, msg, wparam, lparam):
        self._remove_tray_icon()
        self.hwnd = None
        win32gui.PostQuitMessage(0)
        return 0


def signal_handler(sig, frame):
    global running, tray_controller
    logger = logging.getLogger("PrintSVC")
    logger.info("Received signal %s, shutting down...", sig)
    running = False
    if tray_controller:
        tray_controller.request_shutdown()


def _status_loop():
    last_status_time = 0
    while running:
        time.sleep(5)
        if not running:
            break
        now = time.time()
        if now - last_status_time >= 60:
            _print_status()
            last_status_time = now


def main():
    global running, app_state, tray_controller

    parser = argparse.ArgumentParser(description="PrintSVC - Network Print Service for legacy printers")
    parser.add_argument("--config", "-c", type=str, help="Path to config file")
    parser.add_argument("--printer", "-p", type=str, help="Printer name to use")
    parser.add_argument("--port", type=int, default=631, help="IPP server port (default: 631)")
    parser.add_argument("--no-mdns", action="store_true", help="Disable mDNS advertising")
    parser.add_argument("--log-file", type=str, default="", help="Log file path")
    parser.add_argument("--log-level", type=str, default="INFO", help="Log level (DEBUG, INFO, WARNING, ERROR)")
    parser.add_argument("--install-service", action="store_true", help="Install as Windows service (srvany)")
    args = parser.parse_args()

    config = load_config()
    if args.printer:
        config["printer_name"] = args.printer
    if args.port:
        config["ipp_port"] = args.port
    if args.log_file:
        config["log_file"] = args.log_file
    if args.log_level:
        config["log_level"] = args.log_level
    if args.no_mdns:
        config["mDNS_enabled"] = False

    level = getattr(logging, config.get("log_level", "INFO").upper(), logging.INFO)
    log_file = config.get("log_file", "")
    if log_file and not os.path.isabs(log_file):
        exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        log_file = os.path.join(exe_dir, log_file)
    log = setup_logging(log_file=log_file, level=level)

    log.info("=" * 60)
    log.info("  PrintSVC v%s - Network Print Service", __import__("printsvc").__version__)
    log.info("=" * 60)

    import printsvc.server as svr
    svr.printer_name = config.get("printer_name", "")
    svr.advertised_printer_name = config.get("service_name", "PrintSVC")
    svr.server_port = config.get("ipp_port", 631)

    if args.install_service:
        _install_service(config)
        return 0

    printer_display_name = _connect_printer()
    if not printer_display_name:
        log.warning("No printer found. Service will start but printing will fail until a printer is configured.")
        log.warning("  Use --printer \"Printer Name\" to specify a printer.")
        log.warning("  Or open the web status page to see available printers.")

    app = TrayApp(config, log)
    app_state = app
    if not app.start_services():
        log.error("Failed to start IPP server, exiting")
        print("\nERROR: IPP server failed to start. Port 631 may be in use (run as administrator).")
        print("       Check " + (log_file or "console") + " for details.")
        input("\nPress Enter to exit...")
        sys.exit(1)

    local_ip = get_local_ip()
    log.info("")
    log.info("PrintSVC is ready!")
    log.info("  Web Status:    http://localhost:%d/", config.get("ipp_port", 631))
    log.info("  IPP Endpoint:  ipp://%s:%d/ipp/print", local_ip, config.get("ipp_port", 631))
    log.info("  LAN Discovery: mDNS/_ipp._tcp active on port %d", config.get("ipp_port", 631))
    log.info("")
    log.info("Print from your device:")
    log.info("  Android: Open file -> Print -> Select 'PrintSVC'")
    log.info("  Windows: Settings -> Printers & scanners -> Add device")
    log.info("  iOS:     Open file -> Print -> Select 'PrintSVC'")
    log.info("")

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    status_thread = threading.Thread(target=_status_loop, daemon=True, name="PrintSVCStatus")
    status_thread.start()

    tray_controller = TrayController(app)
    if not tray_controller.start():
        log.error("Failed to start tray controller")
        running = False
        app.stop_services()
        status_thread.join(timeout=2.0)
        return 1

    try:
        last_status_time = 0
        while running:
            try:
                time.sleep(5)
            except KeyboardInterrupt:
                log.info("KeyboardInterrupt received, shutting down...")
                running = False
                break
            now = time.time()
            if now - last_status_time >= 60:
                _print_status()
                last_status_time = now
    finally:
        running = False
        app.stop_services()
        if tray_controller:
            tray_controller.request_shutdown()
            tray_controller.join(timeout=2.0)
        status_thread.join(timeout=2.0)
        tray_controller = None
        app_state = None

    log.info("PrintSVC stopped. Goodbye!")
    return 0


def _connect_printer():
    log = logging.getLogger("PrintSVC")
    import printsvc.server as svr

    pname = svr.printer_name
    if not pname:
        pname = winprint.find_printer()
    else:
        found = winprint.find_printer(pname)
        if found:
            pname = found

    if pname:
        info = winprint.get_printer_info(pname)
        log.info("Printer connected:")
        log.info("  Name:   %s", info.get("name", pname))
        log.info("  Driver: %s", info.get("driver", ""))
        log.info("  Port:   %s", info.get("port", ""))
        log.info("  Status: %s", _status_text(info.get("state", 0)))

        svr.printer_name = pname
        return info.get("name", pname)
    else:
        log.warning("No printer found. Please check:")
        log.warning("  1. Printer is connected and powered on")
        log.warning("  2. Printer driver is installed")
        log.warning("  3. Run with --printer \"Printer Name\" to specify")
        return None


def _status_text(state):
    texts = {3: "Idle", 4: "Printing", 5: "Stopped"}
    return texts.get(state, f"Unknown ({state})")


def _print_status():
    log = logging.getLogger("PrintSVC")
    import printsvc.server as svr
    pname = svr.printer_name or "None"
    active = job_store.get_active_jobs()
    total = len(job_store)
    log.info("Status: printer=%s | active_jobs=%d | total_jobs=%d",
             pname, len(active), total)


def _install_service(config):
    log = logging.getLogger("PrintSVC")
    log.info("Installing as Windows service...")

    exe_path = os.path.abspath(sys.argv[0])
    working_dir = os.path.dirname(exe_path)

    bat_path = os.path.join(working_dir, "start_printsvc.bat")
    with open(bat_path, "w") as f:
        f.write("@echo off\r\n")
        f.write(f'cd /d "{working_dir}"\r\n')
        f.write(f'"{exe_path}" --log-file=printsvc.log\r\n')

    log.info("Created startup script: %s", bat_path)
    log.info("")
    log.info("To install as a Windows service:")
    log.info("  1. Download 'Windows Service Wrapper' (winsw):")
    log.info("     https://github.com/winsw/winsw/releases")
    log.info("  2. Create printsvc.xml next to winsw.exe:")
    log.info("")
    log.info('<service>')
    log.info('  <id>PrintSVC</id>')
    log.info('  <name>PrintSVC</name>')
    log.info('  <description>Network Print Service for legacy printers</description>')
    log.info('  <executable>%%BASE%%\\start_printsvc.bat</executable>')
    log.info('  <workingdirectory>%%BASE%%</workingdirectory>')
    log.info('  <log mode="roll"></log>')
    log.info('</service>')
    log.info("")
    log.info("  3. Run: winsw install")
    log.info("  4. Run: winsw start")


if __name__ == "__main__":
    main()
