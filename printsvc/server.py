"""
HTTP/IPP server for PrintSVC.
Listens on port 631 (IPP) and handles:
  - IPP requests (POST with application/ipp content-type)
  - Web status page (GET /)
  - Printer info endpoints
"""
import json
import logging
import socket
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

from . import ipp as ipp_proto
from . import winprint
from . import docrender
from .netutils import get_local_ip
from urllib.parse import quote, unquote

logger = logging.getLogger("PrintSVC.Server")


class JobStore:
    """Simple in-memory job tracking."""

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs = {}
        self._next_id = 1

    def create_job(self, printer_uri, username="unknown", job_name="Untitled",
                   document_format="application/pdf", copies=1, sides="one-sided"):
        with self._lock:
            jid = self._next_id
            self._next_id += 1
            job = {
                "id": jid,
                "printer_uri": printer_uri,
                "username": username,
                "job_name": job_name,
                "document_format": document_format,
                "copies": copies,
                "sides": sides,
                "state": "pending",
                "state_reason": "none",
                "created_at": time.time(),
                "processed_at": None,
                "completed_at": None,
                "message": "",
            }
            self._jobs[jid] = job
            return jid, job

    def update_job(self, job_id, **kwargs):
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(kwargs)

    def get_job(self, job_id):
        with self._lock:
            return self._jobs.get(job_id)

    def get_active_jobs(self):
        with self._lock:
            active_states = {"pending", "processing", "pending-held", "stopped"}
            return [j for j in self._jobs.values() if j["state"] in active_states]

    def get_all_jobs(self, limit=50):
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j["created_at"], reverse=True)
            return jobs[:limit]

    def __len__(self):
        with self._lock:
            return len(self._jobs)


# Global job store
job_store = JobStore()

# Printer status tracking
printer_name = None
printer_config = {}


class IPPHandler(BaseHTTPRequestHandler):
    """HTTP request handler that understands IPP protocol."""

    # Suppress default logging (we do our own)
    def log_message(self, format, *args):
        logger.debug("HTTP: %s - %s", self.client_address, format % args)

    def do_GET(self):
        """Handle GET requests - status page, printer info, etc."""
        path = self.path

        if path == "/" or path == "/status":
            self._handle_status_page()
        elif path == "/api/status":
            self._handle_api_status()
        elif path == "/api/printers":
            self._handle_api_printers()
        elif path.startswith("/ipp/"):
            self._handle_ipp_get_status()
        elif path == "/favicon.ico":
            self.send_response(404)
            self.end_headers()
        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Not Found")

    def do_POST(self):
        """Handle POST requests - primarily IPP print jobs."""
        content_type = self.headers.get("Content-Type", "")
        content_length = int(self.headers.get("Content-Length", 0))

        logger.debug("POST %s from %s, Content-Type: %s, Length: %d",
                     self.path, self.client_address, content_type, content_length)

        if "application/ipp" in content_type:
            body = self.rfile.read(content_length) if content_length > 0 else b""
            self._handle_ipp_request(body)
        elif content_type == "application/json":
            body = self.rfile.read(content_length) if content_length > 0 else b""
            self._handle_json_api(body)
        else:
            self.send_response(400)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Unsupported Content-Type")

    # ---- IPP Request Handling ----

    def _handle_ipp_request(self, body):
        """Parse and handle an IPP request."""
        global printer_name

        try:
            req = ipp_proto.parse_ipp_request(body)
            logger.info("IPP request: op=0x%04X (%s) id=%d from %s",
                        req.operation_id, self._op_name(req.operation_id),
                        req.request_id, self.client_address[0])

            # Dump raw request bytes for debugging
            logger.info("IPP request hex[%d]: %s", len(body), body.hex())

            # Dispatch based on operation
            if req.operation_id == ipp_proto.OP_GET_PRINTER_ATTRS:
                response = self._ipp_get_printer_attrs(req)
            elif req.operation_id == ipp_proto.OP_PRINT_JOB:
                response = self._ipp_print_job(req)
            elif req.operation_id == ipp_proto.OP_VALIDATE_JOB:
                response = self._ipp_validate_job(req)
            elif req.operation_id == ipp_proto.OP_GET_JOBS:
                response = self._ipp_get_jobs(req)
            elif req.operation_id == ipp_proto.OP_GET_JOB_ATTRS:
                response = self._ipp_get_job_attrs(req)
            elif req.operation_id == ipp_proto.OP_CANCEL_JOB:
                response = self._ipp_cancel_job(req)
            else:
                logger.warning("Unsupported IPP operation: 0x%04X", req.operation_id)
                response = self._ipp_error(req, ipp_proto.SERVER_ERROR_OPERATION_NOT_SUPPORTED)

            # Dump raw response bytes for debugging
            logger.info("IPP response hex[%d]: %s", len(response), response.hex())

            # Send IPP response over HTTP
            self.send_response(200)
            self.send_header("Content-Type", "application/ipp")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        except Exception as e:
            logger.error("IPP request handling failed: %s", e, exc_info=True)
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(f"IPP Error: {e}".encode())

    def _op_name(self, op):
        names = {
            0x0002: "Print-Job",
            0x0003: "Validate-Job",
            0x0008: "Cancel-Job",
            0x0009: "Get-Job-Attributes",
            0x000A: "Get-Jobs",
            0x000B: "Get-Printer-Attributes",
        }
        return names.get(op, f"Unknown(0x{op:04X})")

    def _ipp_get_printer_attrs(self, req):
        """Handle Get-Printer-Attributes request."""
        logger.info("Get-Printer-Attributes from %s", self.client_address[0])

        # Extract printer name from printer-uri: ipp://host:port/ipp/PrinterName
        printer_uri = req.get_op_attr("printer-uri", "")
        pname = printer_name
        if printer_uri and "/ipp/" in printer_uri:
            pname = pname or unquote(printer_uri.rsplit("/ipp/", 1)[-1].split("/")[0])
        pname = pname or "Printer"

        local_ip = get_local_ip()

        # Build the same UUID used by mDNS discovery
        compact_ip = local_ip.replace(".", "").zfill(12)[:12]
        printer_uuid = f"ffffffff-ffff-ffff-ffff-{compact_ip}"

        # Build response attributes
        op_attrs = [
            ("attributes-charset", ipp_proto.TAG_CHARSET, "utf-8"),
            ("attributes-natural-language", ipp_proto.TAG_NATURAL_LANGUAGE, "en"),
        ]

        printer_attrs = ipp_proto.make_printer_attributes(
            printer_name=pname,
            printer_state=3,
            accepting_jobs=True,
            host_ip=local_ip,
            printer_uuid=printer_uuid,
            make_model=f"PrintSVC - {pname}",
            device_id="MFG:PrintSVC;MDL:Network Printer;CMD:PDF,JPEG,PNG;CLASS:1.3;",
        )

        response = ipp_proto.encode_ipp_response(
            1, 1, ipp_proto.OK, req.request_id,
            op_attrs,
            printer_attrs=printer_attrs
        )
        return response

    def _ipp_print_job(self, req):
        """Handle Print-Job request."""
        global printer_name
        pname = printer_name
        if not pname:
            pname = winprint.find_printer()
            if not pname:
                logger.error("No printer available for Print-Job")
                return self._ipp_error(req, ipp_proto.SERVER_ERROR_DEVICE_ERROR)

        doc_format = req.document_format
        job_name = req.job_name or "PrintSVC Job"
        username = req.username or "unknown"

        # Get job attributes from operation attrs or job attrs
        copies = 1
        sides = "one-sided"
        orientation = 1
        for attr_list in (req.operation_attrs, req.job_attrs):
            for a in attr_list:
                if a.name == "copies":
                    copies = int(a.value)
                elif a.name == "sides":
                    sides = a.value
                elif a.name == "orientation-requested":
                    orientation = int(a.value)

        logger.info("Print-Job: fmt=%s, copies=%d, sides=%s, orientation=%d, job=%s, user=%s, size=%d bytes",
                    doc_format, copies, sides, orientation, job_name, username, len(req.document))

        local_ip = get_local_ip()
        printer_uri = f"ipp://{local_ip}:631/ipp/print"

        # Create job record
        jid, job_record = job_store.create_job(
            printer_uri=printer_uri,
            username=username,
            job_name=job_name,
            document_format=doc_format,
            copies=copies,
            sides=sides,
        )
        # Store orientation in job record for the print thread
        job_record["orientation"] = orientation

        # Start printing in background thread
        def _do_print():
            try:
                job_store.update_job(jid, state="processing")
                _print_document(pname, req.document, doc_format, copies, sides, orientation)
                job_store.update_job(jid, state="completed", completed_at=time.time(),
                                     message="Printed successfully")
                logger.info("Job #%d completed: %s", jid, job_name)
            except Exception as e:
                logger.error("Job #%d failed: %s", jid, e, exc_info=True)
                job_store.update_job(jid, state="aborted", message=str(e))

        t = threading.Thread(target=_do_print, daemon=True, name=f"PrintJob-{jid}")
        t.start()

        # Build response
        op_attrs = [
            ("attributes-charset", ipp_proto.TAG_CHARSET, "utf-8"),
            ("attributes-natural-language", ipp_proto.TAG_NATURAL_LANGUAGE, "en"),
        ]

        job_attrs = ipp_proto.make_job_attributes(
            job_id=jid,
            printer_uri=printer_uri,
            status="pending",
            job_name=job_name,
            username=username,
            document_format=doc_format,
            copies=copies,
            sides=sides,
        )

        response = ipp_proto.encode_ipp_response(
            1, 1, ipp_proto.OK, req.request_id,
            op_attrs,
            job_attrs=job_attrs
        )
        return response

    def _ipp_validate_job(self, req):
        """Handle Validate-Job request."""
        doc_format = req.document_format
        supported_formats = [
            "application/pdf",
            "image/png", "image/jpeg", "image/tiff", "image/bmp",
            "application/octet-stream",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/msword",
            "application/vnd.ms-excel",
            "application/vnd.ms-powerpoint",
        ]

        if doc_format not in supported_formats and not doc_format.startswith("image/"):
            logger.warning("Validate-Job: unsupported format %s", doc_format)
            return self._ipp_error(req, ipp_proto.CLIENT_ERROR_DOCUMENT_FORMAT_NOT_SUPPORTED)

        op_attrs = [
            ("attributes-charset", ipp_proto.TAG_CHARSET, "utf-8"),
            ("attributes-natural-language", ipp_proto.TAG_NATURAL_LANGUAGE, "en"),
        ]
        response = ipp_proto.encode_ipp_response(
            1, 1, ipp_proto.OK, req.request_id, op_attrs
        )
        logger.info("Validate-Job OK for format %s", doc_format)
        return response

    def _ipp_get_jobs(self, req):
        """Handle Get-Jobs request."""
        op_attrs = [
            ("attributes-charset", ipp_proto.TAG_CHARSET, "utf-8"),
            ("attributes-natural-language", ipp_proto.TAG_NATURAL_LANGUAGE, "en"),
        ]

        local_ip = get_local_ip()
        encoded_name = quote((printer_name or "Printer"), safe="")
        printer_uri = f"ipp://{local_ip}:631/ipp/{encoded_name}"

        job_attrs = []
        for j in job_store.get_all_jobs(limit=20):
            pname = printer_name or "Printer"
            job_attrs.extend(ipp_proto.make_job_attributes(
                job_id=j["id"],
                printer_uri=printer_uri,
                status=j["state"],
                job_name=j["job_name"],
                username=j["username"],
                document_format=j["document_format"],
                copies=j["copies"],
                sides=j["sides"],
            ))

        response = ipp_proto.encode_ipp_response(
            1, 1, ipp_proto.OK, req.request_id,
            op_attrs,
            job_attrs=job_attrs,
        )
        return response

    def _ipp_get_job_attrs(self, req):
        """Handle Get-Job-Attributes request."""
        job_id_val = req.get_op_attr("job-id")
        if job_id_val is None:
            return self._ipp_error(req, ipp_proto.CLIENT_ERROR_BAD_REQUEST)

        job = job_store.get_job(int(job_id_val))
        if not job:
            return self._ipp_error(req, ipp_proto.CLIENT_ERROR_NOT_FOUND)

        pname = printer_name or "Printer"
        local_ip = get_local_ip()
        printer_uri = f"ipp://{local_ip}:631/ipp/print"

        op_attrs = [
            ("attributes-charset", ipp_proto.TAG_CHARSET, "utf-8"),
            ("attributes-natural-language", ipp_proto.TAG_NATURAL_LANGUAGE, "en"),
        ]
        job_attrs = ipp_proto.make_job_attributes(
            job_id=job["id"],
            printer_uri=printer_uri,
            status=job["state"],
            job_name=job["job_name"],
            username=job["username"],
            document_format=job["document_format"],
            copies=job["copies"],
            sides=job["sides"],
        )

        response = ipp_proto.encode_ipp_response(
            1, 1, ipp_proto.OK, req.request_id,
            op_attrs,
            job_attrs=job_attrs,
        )
        return response

    def _ipp_cancel_job(self, req):
        """Handle Cancel-Job request."""
        job_id_val = req.get_op_attr("job-id")
        if job_id_val is None:
            return self._ipp_error(req, ipp_proto.CLIENT_ERROR_BAD_REQUEST)

        job = job_store.get_job(int(job_id_val))
        if not job:
            return self._ipp_error(req, ipp_proto.CLIENT_ERROR_NOT_FOUND)

        if job["state"] in ("completed", "aborted", "canceled"):
            return self._ipp_error(req, ipp_proto.CLIENT_ERROR_GONE)

        job_store.update_job(int(job_id_val), state="canceled", message="Canceled by user")
        logger.info("Job #%s canceled", job_id_val)

        op_attrs = [
            ("attributes-charset", ipp_proto.TAG_CHARSET, "utf-8"),
            ("attributes-natural-language", ipp_proto.TAG_NATURAL_LANGUAGE, "en"),
        ]
        response = ipp_proto.encode_ipp_response(
            1, 1, ipp_proto.OK, req.request_id, op_attrs
        )
        return response

    def _ipp_error(self, req, status_code):
        """Build a simple error IPP response."""
        op_attrs = [
            ("attributes-charset", ipp_proto.TAG_CHARSET, "utf-8"),
            ("attributes-natural-language", ipp_proto.TAG_NATURAL_LANGUAGE, "en"),
        ]
        logger.warning("IPP error response: %s", ipp_proto.status_str(status_code))
        return ipp_proto.encode_ipp_response(1, 1, status_code, req.request_id, op_attrs)

    # ---- HTTP API and Status Pages ----

    def _handle_status_page(self):
        """Serve the main status HTML page."""
        pname = printer_name or "Not configured"
        printer_info = None
        if pname and pname != "Not configured":
            printer_info = winprint.get_printer_info(pname)

        state_map = {3: "Idle", 4: "Printing", 5: "Stopped"}
        pstate = state_map.get(printer_info.get("state", 0) if printer_info else 0, "Unknown")
        pdriver = printer_info.get("driver", "") if printer_info else ""
        pport = printer_info.get("port", "") if printer_info else ""

        jobs = job_store.get_all_jobs(limit=20)
        job_rows = ""
        for j in jobs:
            created = time.strftime("%H:%M:%S", time.localtime(j["created_at"]))
            fmt_display = j['document_format']
            # Show friendly name for Office formats
            if docrender.is_office_format(fmt_display):
                fmt_display = docrender.friendly_name(fmt_display)
            elif fmt_display.startswith("image/"):
                fmt_display = fmt_display.replace("image/", "").upper()
            elif fmt_display == "application/pdf":
                fmt_display = "PDF"
            job_rows += f"""<tr>
                <td>{j['id']}</td>
                <td>{j['job_name'][:40]}</td>
                <td>{j['username']}</td>
                <td>{j['state']}</td>
                <td>{fmt_display}</td>
                <td>{j['copies']}</td>
                <td>{created}</td>
            </tr>"""

        hostname = socket.gethostname()
        local_ip = get_local_ip()

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PrintSVC - Network Print Service</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font: 14px/1.6 -apple-system, 'Microsoft YaHei', sans-serif; background: #f5f5f5; color: #333; }}
  .container {{ max-width: 960px; margin: 20px auto; padding: 0 16px; }}
  h1 {{ font-size: 24px; color: #1a1a2e; margin-bottom: 8px; }}
  h2 {{ font-size: 18px; color: #16213e; margin: 24px 0 12px; border-bottom: 2px solid #e0e0e0; padding-bottom: 6px; }}
  .card {{ background: #fff; border-radius: 8px; padding: 20px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  .status-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
  .status-grid .label {{ color: #666; }}
  .status-grid .value {{ font-weight: 600; }}
  .status-dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }}
  .dot-green {{ background: #4caf50; }}
  .dot-red {{ background: #f44336; }}
  .dot-yellow {{ background: #ff9800; }}
  .dot-gray {{ background: #9e9e9e; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #eee; font-size: 13px; }}
  th {{ background: #fafafa; font-weight: 600; color: #555; }}
  tr:hover {{ background: #f8f9fa; }}
  .footer {{ text-align: center; color: #999; font-size: 12px; margin: 30px 0; }}
</style>
</head>
<body>
<div class="container">
  <h1>🖨️ PrintSVC</h1>
  <p style="color:#666;margin-bottom:20px;">Network Print Service for {hostname} ({local_ip})</p>

  <div class="card">
    <h2>Printer Status</h2>
    <div class="status-grid">
      <span class="label">Printer:</span>
      <span class="value">{pname}</span>
      <span class="label">Driver:</span>
      <span class="value">{pdriver}</span>
      <span class="label">Port:</span>
      <span class="value">{pport}</span>
      <span class="label">State:</span>
      <span class="value">
        <span class="status-dot dot-{'green' if pstate == 'Idle' else 'yellow' if pstate == 'Printing' else 'red'}"></span>
        {pstate}
      </span>
    </div>
  </div>

  <div class="card">
    <h2>Print Service</h2>
    <div class="status-grid">
      <span class="label">Service:</span>
      <span class="value"><span class="status-dot dot-green"></span> Running</span>
      <span class="label">IPP Port:</span>
      <span class="value">631</span>
      <span class="label">mDNS:</span>
      <span class="value"><span class="status-dot dot-green"></span> Active</span>
      <span class="label">Printer URI:</span>
      <span class="value"><code>ipp://{local_ip}:631/ipp/{pname or 'printer'}</code></span>
    </div>
  </div>

  <div class="card">
    <h2>Recent Jobs ({len(jobs)})</h2>
    <table>
      <thead>
        <tr><th>ID</th><th>Name</th><th>User</th><th>Status</th><th>Format</th><th>Copies</th><th>Time</th></tr>
      </thead>
      <tbody>
        {job_rows if job_rows else '<tr><td colspan="7" style="text-align:center;color:#999;">No print jobs yet</td></tr>'}
      </tbody>
    </table>
  </div>

  <div class="card">
    <h2>Connect Instructions</h2>
    <h3>Android (Mopria)</h3>
    <p>Open file → Print → Select "PrintSVC" from printer list.</p>
    <h3>Windows</h3>
    <p>Settings → Bluetooth & devices → Printers & scanners → Add device → Select "PrintSVC".</p>
    <h3>iOS / macOS</h3>
    <p>Open file → Print → Select "PrintSVC".</p>
  </div>

  <div class="footer">PrintSVC v1.0.0 - Running on {hostname}</div>
</div>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _handle_api_status(self):
        """JSON API for status."""
        pname = printer_name or "Not configured"
        info = winprint.get_printer_info(pname) if pname != "Not configured" else None
        state = info.get("state", 0) if info else 0
        status_data = {
            "service": "PrintSVC",
            "version": "1.0.0",
            "printer": pname,
            "printer_state": state,
            "printer_ok": state == 3 or state == 4,
            "driver": info.get("driver", "") if info else "",
            "port": info.get("port", "") if info else "",
            "active_jobs": len(job_store.get_active_jobs()),
            "total_jobs": len(job_store),
        }
        self._send_json(status_data)

    def _handle_api_printers(self):
        """JSON API listing available printers."""
        printers = winprint.list_printers()
        self._send_json({"printers": printers, "selected": printer_name})

    def _handle_ipp_get_status(self):
        """Handle IPP path GET (for printer status checks)."""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"IPP/1.1 200 OK\r\n")

    def _handle_json_api(self, body):
        """Handle JSON API POST requests."""
        try:
            data = json.loads(body)
            action = data.get("action", "")
            if action == "list_printers":
                printers = winprint.list_printers()
                self._send_json({"printers": printers, "selected": printer_name})
            else:
                self._send_json({"error": f"Unknown action: {action}"}, 400)
        except json.JSONDecodeError:
            self._send_json({"error": "Invalid JSON"}, 400)

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _print_document(pname, doc_data, doc_format, copies=1, sides="one-sided", orientation=1):
    """Dispatch a print job to the appropriate printer backend."""
    logger.info("Printing %d bytes to %s (format=%s, copies=%d, sides=%s, orientation=%d)",
                len(doc_data), pname, doc_format, copies, sides, orientation)

    if doc_format == "application/pdf":
        winprint.print_pdf(pname, doc_data, copies=copies, sides=sides, orientation=orientation)
    elif doc_format in ("image/png", "image/jpeg", "image/tiff", "image/bmp"):
        winprint.print_image(pname, doc_data, copies=copies, sides=sides, orientation=orientation)
    elif doc_format == "application/octet-stream" or doc_format == "application/vnd.cups-raw":
        winprint.print_raw(pname, doc_data)
    elif docrender.is_office_format(doc_format):
        logger.info("Converting Office document (%s) to PDF for printing", doc_format)
        pdf_data = docrender.office_to_pdf(doc_data, doc_format)
        winprint.print_pdf(pname, pdf_data, copies=copies, sides=sides, orientation=orientation)
    else:
        # Try as image if it looks like one
        try:
            winprint.print_image(pname, doc_data, copies=copies, sides=sides)
        except Exception:
            # Fallback: try raw
            logger.warning("Format %s not recognized, trying raw print", doc_format)
            winprint.print_raw(pname, doc_data)


class IPPServer:
    """Main IPP server wrapper."""

    def __init__(self, host="0.0.0.0", port=631):
        self.host = host
        self.port = port
        self._server = None
        self._thread = None
        self._running = False

    def start(self):
        """Start the HTTP/IPP server."""
        try:
            self._server = HTTPServer((self.host, self.port), IPPHandler)
            self._server.timeout = 0.5
            self._running = True
            self._thread = threading.Thread(target=self._run, daemon=True, name="IPPServer")
            self._thread.start()
            logger.info("IPP server running on %s:%d", self.host, self.port)
            return True
        except Exception as e:
            logger.error("Failed to start IPP server on %s:%d: %s", self.host, self.port, e)
            return False

    def _run(self):
        """Server main loop."""
        while self._running:
            try:
                self._server.handle_request()
            except OSError:
                break
            except Exception as e:
                if self._running:
                    logger.warning("Server handler error: %s", e)

    def stop(self):
        """Stop the server."""
        self._running = False
        if self._server:
            try:
                self._server.server_close()
            except Exception as e:
                logger.warning("Error closing server: %s", e)
            self._server = None
        logger.info("IPP server stopped")
