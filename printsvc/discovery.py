"""
mDNS / DNS-SD service discovery for LAN printers.
Advertises PrintSVC as an IPP printer so Android (Mopria) and Windows can discover it.
"""
import logging
import socket
import threading
import time

from .netutils import get_local_ip

logger = logging.getLogger("PrintSVC.mDNS")

try:
    from zeroconf import Zeroconf, ServiceInfo, ServiceBrowser, ServiceStateChange
    HAVE_ZEROCONF = True
except ImportError:
    HAVE_ZEROCONF = False
    logger.warning("zeroconf not available. Install with: pip install zeroconf")


class MDNSService:
    """mDNS advertiser for IPP printer services."""

    def __init__(self, hostname="PrintSVC", port=631, service_name="Toshiba E-Studio 240s",
                 printer_name="TOSHIBA_eSTUDIO240s"):
        self.hostname = hostname
        self.port = port
        self.service_name = service_name
        self.printer_name = printer_name
        self._zeroconf = None
        self._running = False

    def start(self):
        """Start mDNS advertising."""
        if not HAVE_ZEROCONF:
            logger.error("Cannot start mDNS: zeroconf library not installed")
            return False

        if self._running:
            logger.warning("mDNS service already running")
            return True

        try:
            self._zeroconf = Zeroconf()
            local_ip = get_local_ip()

            valid_host = self.hostname.replace(" ", "-")
            qualified_host = f"{valid_host}.local."

            # DNS TXT 值上限 255 字节，控制在单条以内（Office 格式通过 IPP 查询获取）
            pdl_compact = "application/pdf,image/png,image/jpeg,image/tiff"

            props = {
                "rp": f"ipp/print/{self.printer_name}",
                "ty": self.service_name,
                "adminurl": f"http://{local_ip}:{self.port}/",
                "note": "PrintSVC Network Print Service",
                "product": "(Toshiba E-Studio 240s)",
                "usb_MFG": "TOSHIBA",
                "usb_MDL": "e-STUDIO240s",
                "priority": "50",
                "txtvers": "1",
                "qtotal": "1",
                "pdl": pdl_compact,
                "TLS": "1.2",
                "Color": "F",
                "Duplex": "T",
                "Copies": "T",
            }

            # Compact UUID without dashes to keep it small
            compact_ip = local_ip.replace(".", "").zfill(12)[:12]
            props["UUID"] = f"ffffffff-ffff-ffff-ffff-{compact_ip}"

            ipp_info = ServiceInfo(
                type_="_ipp._tcp.local.",
                name=f"{valid_host}._ipp._tcp.local.",
                addresses=[socket.inet_aton(local_ip)],
                port=self.port,
                weight=0,
                priority=0,
                properties=props,
                server=qualified_host,
            )

            # _printer._tcp — keep under 255 bytes
            printer_props = {
                "ty": self.service_name,
                "adminurl": f"http://{local_ip}:{self.port}/",
                "note": "PrintSVC Network Print Service",
                "pdl": pdl_compact,
                "txtvers": "1",
                "qtotal": "1",
            }

            printer_info = ServiceInfo(
                type_="_printer._tcp.local.",
                name=f"{valid_host}._printer._tcp.local.",
                addresses=[socket.inet_aton(local_ip)],
                port=self.port,
                weight=0,
                priority=0,
                properties=printer_props,
                server=qualified_host,
            )

            self._zeroconf.register_service(ipp_info)
            self._zeroconf.register_service(printer_info)
            self._running = True

            pdl_info = ServiceInfo(
                type_="_pdl-datastream._tcp.local.",
                name=f"{valid_host}._pdl-datastream._tcp.local.",
                addresses=[socket.inet_aton(local_ip)],
                port=9100,
                weight=0,
                priority=0,
                properties={
                    "ty": self.service_name,
                    "note": "PrintSVC Raw Print Service",
                    "adminurl": f"http://{local_ip}:{self.port}/",
                },
                server=qualified_host,
            )
            self._zeroconf.register_service(pdl_info)

            logger.info("mDNS services registered on %s:%d", local_ip, self.port)
            logger.info("  _ipp._tcp     - IPP Printing")
            logger.info("  _printer._tcp - Legacy printer discovery")
            logger.info("  _pdl-datastream._tcp - Raw printing")
            return True

        except Exception as e:
            logger.error("Failed to start mDNS: %s", e, exc_info=True)
            if self._zeroconf:
                try:
                    self._zeroconf.close()
                except Exception:
                    pass
                self._zeroconf = None
            return False

    def stop(self):
        """Stop mDNS advertising."""
        if self._zeroconf:
            try:
                self._zeroconf.close()
            except Exception as e:
                logger.warning("Error closing zeroconf: %s", e)
            self._zeroconf = None
        self._running = False
        logger.info("mDNS services stopped")

    @property
    def running(self):
        return self._running


class SSDPListener:
    """
    Simple SSDP (Simple Service Discovery Protocol) responder.
    Allows Windows to discover the printer via network.

    Note: On Win7, port 1900 may conflict with the Windows SSDP Discovery Service.
    If binding fails, the listener logs a warning and continues — mDNS discovery
    still works for most clients.
    """

    SSDP_ADDR = "239.255.255.250"
    SSDP_PORT = 1900

    def __init__(self, port=631, server_name="PrintSVC", printer_name="TOSHIBA_eSTUDIO240s"):
        self.port = port
        self.server_name = server_name
        self.printer_name = printer_name
        self._running = False
        self._thread = None
        self._sock = None

    def start(self):
        """Start SSDP listener thread."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="SSDP")
        self._thread.start()
        logger.info("SSDP listener started on port %d", self.SSDP_PORT)
        return True

    def _create_socket(self):
        """Create and configure the SSDP multicast socket."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # On Windows, also set SO_REUSE_MULTICAST (optional, may not exist on Win7)
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        except OSError:
            pass

        return sock

    def _run(self):
        """SSDP multicast listener loop."""
        sock = None
        try:
            sock = self._create_socket()

            # Try binding to SSDP port
            try:
                sock.bind(("0.0.0.0", self.SSDP_PORT))
            except OSError as e:
                logger.warning("SSDP port %d bind failed (%s). SSDP discovery may not work, "
                               "but mDNS discovery is still available.", self.SSDP_PORT, e)
                self._running = False
                return

            mreq = socket.inet_aton(self.SSDP_ADDR) + socket.inet_aton("0.0.0.0")
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            sock.settimeout(2.0)

            self._sock = sock
            local_ip = get_local_ip()

            while self._running:
                try:
                    data, addr = sock.recvfrom(2048)
                    msg = data.decode("utf-8", errors="replace")
                    logger.debug("SSDP received from %s: %s", addr, msg[:200])

                    if "M-SEARCH" in msg:
                        self._handle_search(msg, addr, local_ip)
                except socket.timeout:
                    continue
                except OSError:
                    if self._running:
                        logger.warning("SSDP socket error", exc_info=True)
                    break
        except Exception as e:
            logger.error("SSDP listener error: %s", e)
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
            self._sock = None
            logger.info("SSDP listener stopped")

    def _handle_search(self, msg, addr, local_ip):
        """Respond to SSDP M-SEARCH requests."""
        st_map = {
            "urn:schemas-upnp-org:device:Printer:1": True,
            "ssdp:all": True,
            "upnp:rootdevice": True,
        }
        target = None
        for line in msg.split("\r\n"):
            if line.upper().startswith("ST:"):
                target = line.split(":", 1)[1].strip()
                break

        if target is None or target not in st_map:
            return

        response = (
            f"HTTP/1.1 200 OK\r\n"
            f"CACHE-CONTROL: max-age=1800\r\n"
            f"DATE: {time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime())}\r\n"
            f"EXT:\r\n"
            f"LOCATION: http://{local_ip}:{self.port}/\r\n"
            f"SERVER: {self.server_name}/1.0 UPnP/1.0\r\n"
            f"ST: urn:schemas-upnp-org:device:Printer:1\r\n"
            f"USN: uuid:PrintSVC-{self.printer_name}::urn:schemas-upnp-org:device:Printer:1\r\n"
            f"Content-Length: 0\r\n\r\n"
        )

        try:
            # Create a temporary socket for sending the response
            send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                send_sock.sendto(response.encode(), addr)
                logger.debug("SSDP response sent to %s", addr)
            finally:
                send_sock.close()
        except Exception as e:
            logger.warning("SSDP response failed: %s", e)

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
