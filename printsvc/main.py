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

from . import winprint
from .config import load_config
from .discovery import MDNSService, SSDPListener
from .logger import setup_logging
from .netutils import get_local_ip
from .server import IPPServer, job_store

# Global flags
running = True
server = None
mdns = None
ssdp = None


def signal_handler(sig, frame):
    global running
    logger = logging.getLogger("PrintSVC")
    logger.info("Received signal %s, shutting down...", sig)
    running = False


def main():
    global running, server, mdns, ssdp

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
        # Resolve relative paths relative to the exe/config directory
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
        return

    printer_display_name = _connect_printer()
    if not printer_display_name:
        log.warning("No printer found. Service will start but printing will fail until a printer is configured.")
        log.warning("  Use --printer \"Printer Name\" to specify a printer.")
        log.warning("  Or open the web status page to see available printers.")

    global server
    server = IPPServer(host=config.get("listen_address", "0.0.0.0"), port=config.get("ipp_port", 631))
    if not server.start():
        log.error("Failed to start IPP server, exiting")
        print("\nERROR: IPP server failed to start. Port 631 may be in use (run as administrator).")
        print("       Check " + (log_file or "console") + " for details.")
        input("\nPress Enter to exit...")
        sys.exit(1)

    advertised_name = config.get("service_name", "PrintSVC")

    if config.get("mDNS_enabled", True):
        global mdns, ssdp
        mdns = MDNSService(
            hostname=advertised_name.replace(" ", "-"),
            port=config.get("ipp_port", 631),
            service_name=advertised_name,
            printer_name=advertised_name,
        )
        mdns.start()

        ssdp = SSDPListener(
            port=config.get("ipp_port", 631),
            server_name=advertised_name,
            printer_name=advertised_name,
        )
        ssdp.start()

    local_ip = get_local_ip()
    log.info("")
    log.info("PrintSVC is ready!")
    log.info("  Web Status:    http://localhost:%d/", config.get("ipp_port", 631))
    log.info("  IPP Endpoint:  ipp://%s:%d/ipp/%s", local_ip, config.get("ipp_port", 631),
             advertised_name)
    log.info("  LAN Discovery: mDNS/_ipp._tcp active on port %d", config.get("ipp_port", 631))
    log.info("")
    log.info("Print from your device:")
    log.info("  Android: Open file -> Print -> Select 'PrintSVC'")
    log.info("  Windows: Settings -> Printers & scanners -> Add device")
    log.info("  iOS:     Open file -> Print -> Select 'PrintSVC'")
    log.info("")

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

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

    log.info("Shutting down PrintSVC...")
    if mdns:
        mdns.stop()
    if ssdp:
        ssdp.stop()
    if server:
        server.stop()

    log.info("PrintSVC stopped. Goodbye!")
    sys.exit(0)


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
    log.info('  <executable>%%BASE%%\\\\start_printsvc.bat</executable>')
    log.info('  <workingdirectory>%%BASE%%</workingdirectory>')
    log.info('  <log mode="roll"></log>')
    log.info('</service>')
    log.info("")
    log.info("  3. Run: winsw install")
    log.info("  4. Run: winsw start")


if __name__ == "__main__":
    main()
