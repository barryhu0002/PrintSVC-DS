"""
Windows printer backend using win32print and GDI.
Handles printer discovery, image and PDF printing via Windows driver.
"""
import io
import logging
import os
import tempfile
import time
from PIL import Image, ImageWin

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

logger = logging.getLogger("PrintSVC.Backend")

# Printer state constants
PRINTER_STATE_IDLE = 3
PRINTER_STATE_PRINTING = 4
PRINTER_STATE_STOPPED = 5
PRINTER_STATE_UNKNOWN = 0


def list_printers():
    """List available Windows printers with their status."""
    try:
        import win32print
        printers = []
        for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS):
            name = p[2]
            try:
                h = win32print.OpenPrinter(name)
                try:
                    info = win32print.GetPrinter(h, 2)
                    status = info.get("Status", 0)
                    attrs = info.get("Attributes", 0)
                    printers.append({
                        "name": name,
                        "status": status,
                        "is_default": bool(attrs & 4),  # PRINTER_ATTRIBUTE_DEFAULT
                        "is_shared": bool(attrs & 8),
                        "comment": info.get("Comment", ""),
                        "driver": info.get("DriverName", ""),
                        "port": info.get("PortName", ""),
                        "location": info.get("Location", ""),
                    })
                finally:
                    win32print.ClosePrinter(h)
            except Exception as e:
                logger.warning("Failed to query printer '%s': %s", name, e)
                printers.append({"name": name, "status": -1, "error": str(e)})
        return printers
    except Exception as e:
        logger.error("Failed to enumerate printers: %s", e)
        return []


def find_printer(name_hint=None):
    """
    Find a printer by name hint. Returns the full name or the default printer.
    """
    import win32print
    if name_hint:
        try:
            h = win32print.OpenPrinter(name_hint)
            win32print.ClosePrinter(h)
            return name_hint
        except Exception:
            pass
        # Try partial match
        for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS):
            if name_hint.lower() in p[2].lower():
                return p[2]

    default = win32print.GetDefaultPrinter()
    if default:
        logger.info("Using default printer: %s", default)
        return default

    printers = list_printers()
    if printers:
        logger.info("Using first available printer: %s", printers[0]["name"])
        return printers[0]["name"]

    return None


def get_printer_status(printer_name):
    """Get numeric printer status."""
    try:
        import win32print
        h = win32print.OpenPrinter(printer_name)
        try:
            info = win32print.GetPrinter(h, 2)
            return info.get("Status", 0)
        finally:
            win32print.ClosePrinter(h)
    except Exception as e:
        logger.error("Error querying printer status: %s", e)
        return -1


def print_image(printer_name, image_bytes, page_size=None, orientation=1, copies=1, sides="one-sided"):
    """
    Print an image (PNG, JPEG, etc.) via Windows GDI.
    """
    import win32print
    import win32ui

    image = Image.open(io.BytesIO(image_bytes))
    img_width, img_height = image.size

    if page_size is None:
        page_width_px = int(8.27 * 300)
        page_height_px = int(11.69 * 300)
    else:
        page_width_px, page_height_px = page_size

    if orientation == 2 or orientation == 4:
        page_width_px, page_height_px = page_height_px, page_width_px

    hprinter = win32print.OpenPrinter(printer_name)
    try:
        printer_info = win32print.GetPrinter(hprinter, 2)
        devmode = printer_info.get("pDevMode")
        if devmode is None:
            # Use default devmode if none available
            devmode = win32print.GetPrinter(hprinter, 2)["pDevMode"]

        devmode.Copies = copies
        if sides == "two-sided-long-edge":
            devmode.Flags |= 0x00010000
            devmode.Duplex = 3
        elif sides == "two-sided-short-edge":
            devmode.Flags |= 0x00010000
            devmode.Duplex = 2

        hdc = win32ui.CreateDC()
        hdc.CreatePrinterDC(printer_name, devmode)
        hdc.StartDoc(printer_name)

        if image.mode != "RGB":
            image = image.convert("RGB")
        dib = ImageWin.Dib(image)

        scale = min(page_width_px / img_width, page_height_px / img_height)
        draw_w = int(img_width * scale)
        draw_h = int(img_height * scale)
        offset_x = (page_width_px - draw_w) // 2
        offset_y = (page_height_px - draw_h) // 2

        for _ in range(copies):
            hdc.StartPage()
            dib.draw(hdc.GetHandleOutput(), (offset_x, offset_y, offset_x + draw_w, offset_y + draw_h))
            hdc.EndPage()

        hdc.EndDoc()
        hdc.DeleteDC()
        logger.info("Printed image (%dx%d) -> %s", img_width, img_height, printer_name)
    finally:
        win32print.ClosePrinter(hprinter)


def print_pdf(printer_name, pdf_bytes, copies=1, sides="one-sided", orientation=1):
    """
    Print a PDF by rendering each page to an image and sending to GDI.
    Uses PyMuPDF (fitz) for PDF rendering.
    """
    if fitz is None:
        raise RuntimeError("PyMuPDF (fitz) is not available. Install with: pip install PyMuPDF")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    num_pages = len(doc)
    logger.info("Printing PDF with %d pages -> %s", num_pages, printer_name)

    import win32print
    import win32ui

    hprinter = win32print.OpenPrinter(printer_name)
    try:
        printer_info = win32print.GetPrinter(hprinter, 2)
        devmode = printer_info.get("pDevMode")
        if devmode is None:
            devmode = win32print.GetPrinter(hprinter, 2)["pDevMode"]
        if copies > 1:
            devmode.Copies = copies
        if sides.startswith("two-sided"):
            devmode.Flags |= 0x00010000
            devmode.Duplex = 3 if "long" in sides else 2

        hdc = win32ui.CreateDC()
        hdc.CreatePrinterDC(printer_name, devmode)
        hdc.StartDoc(printer_name)

        for page_num in range(num_pages):
            page = doc.load_page(page_num)
            zoom = 300 / 72
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            page_width = hdc.GetDeviceCaps(110)
            page_height = hdc.GetDeviceCaps(111)
            if orientation in (2, 4):
                page_width, page_height = page_height, page_width

            img_width, img_height = img.size
            scale = min(page_width / img_width, page_height / img_height)
            draw_w = int(img_width * scale)
            draw_h = int(img_height * scale)
            offset_x = (page_width - draw_w) // 2
            offset_y = (page_height - draw_h) // 2

            dib = ImageWin.Dib(img)
            for _ in range(copies if page_num == 0 else 1):
                hdc.StartPage()
                dib.draw(hdc.GetHandleOutput(), (offset_x, offset_y, offset_x + draw_w, offset_y + draw_h))
                hdc.EndPage()

        hdc.EndDoc()
        hdc.DeleteDC()
        doc.close()
        logger.info("PDF printed: %d pages, %d copies", num_pages, copies)
    finally:
        win32print.ClosePrinter(hprinter)


def print_raw(printer_name, data, doc_name="PrintSVC Job"):
    """
    Send raw data directly to printer driver.
    For use with formats the driver can handle natively (e.g. already-rendered data).
    """
    import win32print
    try:
        h = win32print.OpenPrinter(printer_name)
        try:
            win32print.StartDocPrinter(h, 1, (doc_name, None, "RAW"))
            win32print.StartPagePrinter(h)
            win32print.WritePrinter(h, data)
            win32print.EndPagePrinter(h)
            win32print.EndDocPrinter(h)
            logger.info("Raw data sent to %s (%d bytes)", printer_name, len(data))
        finally:
            win32print.ClosePrinter(h)
    except Exception as e:
        logger.error("Print (raw) failed: %s", e)
        raise


def get_printer_info(printer_name):
    """Get detailed info about a printer."""
    try:
        import win32print
        h = win32print.OpenPrinter(printer_name)
        try:
            info = win32print.GetPrinter(h, 2)
            status = info.get("Status", 0)
            state = PRINTER_STATE_IDLE
            if status:
                if status & 0x00000002:  # PRINTER_STATUS_PRINTING
                    state = PRINTER_STATE_PRINTING
                elif status & 0x00000010 or status & 0x00000040:  # PRINTER_STATUS_STOPPED or OFFLINE
                    state = PRINTER_STATE_STOPPED
                else:
                    state = PRINTER_STATE_PRINTING
            return {
                "name": info.get("PrinterName", printer_name),
                "driver": info.get("DriverName", ""),
                "port": info.get("PortName", ""),
                "status": status,
                "state": state,
                "is_shared": bool(info.get("Attributes", 0) & 8),
                "comment": info.get("Comment", ""),
                "location": info.get("Location", ""),
            }
        finally:
            win32print.ClosePrinter(h)
    except Exception as e:
        logger.error("Failed to get printer info for '%s': %s", printer_name, e)
        return {"name": printer_name, "error": str(e)}
