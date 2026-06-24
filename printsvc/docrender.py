"""
Document rendering via Office COM automation.
Converts Word (.docx), Excel (.xlsx), PowerPoint (.pptx) to PDF
using locally-installed Microsoft Office.

Thread safety: a module-level lock ensures only one Office conversion
runs at a time (COM STA limitation).

Important: requires Microsoft Office 2007 or later installed on the machine.
"""
import logging
import os
import tempfile
import threading

logger = logging.getLogger("PrintSVC.DocRender")

# ---------------------------------------------------------------------------
# COM thread safety lock — Office is STA (single-threaded apartment)
# ---------------------------------------------------------------------------
_com_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Office MIME types for IPP
# ---------------------------------------------------------------------------
MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MIME_DOC = "application/msword"
MIME_XLS = "application/vnd.ms-excel"
MIME_PPT = "application/vnd.ms-powerpoint"
MIME_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

OFFICE_FORMATS = {MIME_DOCX, MIME_XLSX, MIME_DOC, MIME_XLS, MIME_PPT, MIME_PPTX}

# Friendly name mapping for web UI
MIME_FRIENDLY = {
    MIME_DOCX: "Word (.docx)",
    MIME_DOC: "Word (.doc)",
    MIME_XLSX: "Excel (.xlsx)",
    MIME_XLS: "Excel (.xls)",
    MIME_PPTX: "PowerPoint (.pptx)",
    MIME_PPT: "PowerPoint (.ppt)",
}


def is_office_format(mime_type):
    """Check if a MIME type is an Office format handled by this module."""
    return mime_type in OFFICE_FORMATS


def friendly_name(mime_type):
    """Return a short friendly name for a MIME type."""
    return MIME_FRIENDLY.get(mime_type, mime_type)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ensure_file_ext(data, ext):
    """Write data to a temp file with the given extension, return the path."""
    fd, path = tempfile.mkstemp(suffix=ext, prefix="printsvc_")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


def _cleanup_temp(*paths):
    """Safely remove temporary files, logging warnings on failure."""
    for p in paths:
        if p and os.path.exists(p):
            try:
                os.unlink(p)
            except OSError as e:
                logger.warning("Failed to remove temp file %s: %s", p, e)


# ---------------------------------------------------------------------------
# Word -> PDF
# ---------------------------------------------------------------------------
def word_to_pdf(docx_data, ext=".docx"):
    """
    Convert Word document data to PDF via Word COM automation.
    Returns PDF bytes.
    """
    import win32com.client

    src = None
    dst = None
    word = None

    with _com_lock:
        try:
            src = _ensure_file_ext(docx_data, ext)
            dst = tempfile.mktemp(suffix=".pdf", prefix="printsvc_word_")

            word = win32com.client.Dispatch("Word.Application")
            word.Visible = False
            word.DisplayAlerts = False  # wdAlertsNone

            doc = word.Documents.Open(os.path.normpath(src))
            doc.SaveAs(os.path.normpath(dst), FileFormat=17)  # 17 = wdFormatPDF
            doc.Close()

            with open(dst, "rb") as f:
                pdf_bytes = f.read()

            logger.info("Word -> PDF: %d bytes, src=%s", len(docx_data), os.path.basename(src))
            return pdf_bytes

        except Exception as e:
            logger.error("Word-to-PDF failed: %s", e, exc_info=True)
            raise RuntimeError(f"Office Word conversion failed: {e}") from e

        finally:
            _cleanup_temp(src, dst)
            if word:
                try:
                    word.Quit()
                except Exception as e:
                    logger.warning("Failed to quit Word: %s", e)


# ---------------------------------------------------------------------------
# Excel -> PDF
# ---------------------------------------------------------------------------
def excel_to_pdf(xlsx_data, ext=".xlsx"):
    """
    Convert Excel document data to PDF via Excel COM automation.
    Only exports the active sheet, not all sheets.
    Returns PDF bytes.
    """
    import win32com.client

    src = None
    dst = None
    excel = None

    with _com_lock:
        try:
            src = _ensure_file_ext(xlsx_data, ext)
            dst = tempfile.mktemp(suffix=".pdf", prefix="printsvc_xls_")

            excel = win32com.client.Dispatch("Excel.Application")
            excel.Visible = False
            excel.DisplayAlerts = False

            wb = excel.Workbooks.Open(os.path.normpath(src))
            # Activate the first worksheet
            if wb.Worksheets.Count > 0:
                wb.Worksheets(1).Activate()
            # Export active sheet only (Type=0=PDF, Quality=0=standard)
            wb.ExportAsFixedFormat(0, os.path.normpath(dst), 1)  # 1 = xlPrintActiveSheet
            wb.Close(False)

            with open(dst, "rb") as f:
                pdf_bytes = f.read()

            logger.info("Excel -> PDF: %d bytes, src=%s", len(xlsx_data), os.path.basename(src))
            return pdf_bytes

        except Exception as e:
            logger.error("Excel-to-PDF failed: %s", e, exc_info=True)
            raise RuntimeError(f"Office Excel conversion failed: {e}") from e

        finally:
            _cleanup_temp(src, dst)
            if excel:
                try:
                    excel.Quit()
                except Exception as e:
                    logger.warning("Failed to quit Excel: %s", e)


# ---------------------------------------------------------------------------
# PowerPoint -> PDF
# ---------------------------------------------------------------------------
def ppt_to_pdf(ppt_data, ext=".pptx"):
    """
    Convert PowerPoint document data to PDF via PowerPoint COM automation.
    Returns PDF bytes.
    """
    import win32com.client

    src = None
    dst = None
    ppt_app = None

    with _com_lock:
        try:
            src = _ensure_file_ext(ppt_data, ext)
            dst = tempfile.mktemp(suffix=".pdf", prefix="printsvc_ppt_")

            ppt_app = win32com.client.Dispatch("PowerPoint.Application")
            ppt_app.Visible = False
            ppt_app.DisplayAlerts = False  # ppAlertsNone

            presentation = ppt_app.Presentations.Open(os.path.normpath(src), WithWindow=False)
            presentation.ExportAsFixedFormat(os.path.normpath(dst), 2, PrintRange=None)
            presentation.Close()

            with open(dst, "rb") as f:
                pdf_bytes = f.read()

            logger.info("PowerPoint -> PDF: %d bytes, src=%s", len(ppt_data), os.path.basename(src))
            return pdf_bytes

        except Exception as e:
            logger.error("PowerPoint-to-PDF failed: %s", e, exc_info=True)
            raise RuntimeError(f"Office PowerPoint conversion failed: {e}") from e

        finally:
            _cleanup_temp(src, dst)
            if ppt_app:
                try:
                    ppt_app.Quit()
                except Exception as e:
                    logger.warning("Failed to quit PowerPoint: %s", e)


# ---------------------------------------------------------------------------
# Dispatch by MIME type
# ---------------------------------------------------------------------------
def office_to_pdf(data, mime_type):
    """
    Convert Office document to PDF based on its MIME type.
    Returns PDF bytes.

    Raises RuntimeError if the format is not supported or conversion fails.
    """
    if mime_type == MIME_DOCX:
        return word_to_pdf(data, ".docx")
    elif mime_type == MIME_DOC:
        return word_to_pdf(data, ".doc")
    elif mime_type == MIME_XLSX:
        return excel_to_pdf(data, ".xlsx")
    elif mime_type == MIME_XLS:
        return excel_to_pdf(data, ".xls")
    elif mime_type == MIME_PPTX:
        return ppt_to_pdf(data, ".pptx")
    elif mime_type == MIME_PPT:
        return ppt_to_pdf(data, ".ppt")
    else:
        raise ValueError(f"Unsupported Office format: {mime_type}")
