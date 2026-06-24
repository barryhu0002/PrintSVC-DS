# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PrintSVC is a network print service that turns legacy USB printers (e.g., Toshiba E-Studio 240s) into network printers. It runs on **Windows 7 32-bit** as the "printer server" — the computer physically connected to the old printer. Android phones, other Windows PCs, and iOS devices on the same LAN can discover it and print without installing extra apps.

**Key constraint**: The target runtime is Win7 32-bit, which limits Python to 3.8 32-bit and requires PyInstaller to bundle everything into a standalone `.exe`.

## Architecture

```
Device (phone/PC) --IPP--> PrintSVC server --GDI--> Windows printer driver --USB/LPT--> Printer
```

The application has five core modules and two utility modules:

### Core modules

| Module | File | Role |
|---|---|---|
| **IPP Protocol** | `printsvc/ipp.py` | Binary encoder/decoder for IPP 1.1 (RFC 2910/2911). Handles `Get-Printer-Attributes`, `Print-Job`, `Validate-Job`, `Get-Jobs`, `Get-Job-Attributes`, `Cancel-Job`. |
| **HTTP/IPP Server** | `printsvc/server.py` | HTTP server on port 631, routes IPP requests, serves web status page at `/`. Contains `JobStore` (in-memory job queue) and `IPPServer` wrapper. |
| **Windows Print Backend** | `printsvc/winprint.py` | Printer discovery (win32print), GDI printing via PIL ImageWin.Dib, PDF printing via PyMuPDF, raw data printing. |
| **Office Renderer** | `printsvc/docrender.py` | COM automation to convert Word/Excel/PowerPoint to PDF using locally-installed Microsoft Office. Thread-safe via `_com_lock`. |
| **LAN Discovery** | `printsvc/discovery.py` | mDNS (zeroconf) advertising `_ipp._tcp`, `_printer._tcp`, `_pdl-datastream._tcp`. SSDP listener for Windows UPnP discovery. |

### Utility modules

| Module | File | Role |
|---|---|---|
| **Config** | `printsvc/config.py` | JSON config management — loads `printsvc.json` from the exe directory. |
| **Logging** | `printsvc/logger.py` | Root logger setup with console + rotating file handler (10MB, 5 backups). |
| **Network Utils** | `printsvc/netutils.py` | Cached `get_local_ip()` used by multiple modules. |
| **Entry** | `printsvc/main.py` | Orchestrates startup: printer connect → IPP server → mDNS/SSDP → main loop. |

### Data flow for a print job

1. Client sends `Print-Job` IPP request (POST `/` with `Content-Type: application/ipp`)
2. `IPPHandler._handle_ipp_request` parses binary IPP → creates `JobStore` entry → starts background thread
3. Background thread calls `_print_document` which dispatches by MIME type:
   - `application/pdf` → `winprint.print_pdf` (PyMuPDF render → GDI)
   - `image/*` → `winprint.print_image` (Pillow → GDI)
   - Office formats → `docrender.office_to_pdf` (COM → PDF) → `winprint.print_pdf`
   - `application/octet-stream` → `winprint.print_raw`

## Build System

### Environment setup (manual, requires proxy/network)

```bash
pip install pywin32 Pillow PyMuPDF zeroconf pyinstaller
```

On Win7 32-bit, use Python 3.8.10 32-bit. PyMuPDF and zeroconf need cp38-win32 wheels (newer versions dropped support — pin to compatible versions if needed).

### Building the executable

```bash
# From project root
python -m PyInstaller \
    --name PrintSVC \
    --onefile \
    --console \
    --clean \
    --noconfirm \
    --add-data "printsvc.json;." \
    --add-binary "path\to\pywin32_system32\pythoncom38.dll;." \
    --add-binary "path\to\pywin32_system32\pywintypes38.dll;." \
    --hidden-import printsvc \
    --hidden-import printsvc.ipp \
    --hidden-import printsvc.server \
    --hidden-import printsvc.winprint \
    --hidden-import printsvc.discovery \
    --hidden-import printsvc.config \
    --hidden-import printsvc.logger \
    --hidden-import printsvc.main \
    --hidden-import printsvc.docrender \
    --hidden-import printsvc.netutils \
    --hidden-import PIL \
    --hidden-import PIL.Image \
    --hidden-import PIL.ImageWin \
    --hidden-import zeroconf \
    --hidden-import fitz \
    --hidden-import ifaddr \
    --collect-all zeroconf \
    run.py
```

Output: `dist/PrintSVC.exe` (~24 MB single-file exe).

A `build.bat` script also exists but requires updating the pywin32 DLL paths for the target machine.

**Important**: The `--add-binary` for `pythoncom38.dll` and `pywintypes38.dll` is mandatory — without them the exe crashes at startup with `ImportError: Module 'pythoncom' isn't in frozen sys.path`. These DLLs are located in Python's `Lib/site-packages/pywin32_system32/` directory. Adjust the path to match the build machine.

### Testing

No formal test framework. Unit tests are ad-hoc Python one-liners:

```bash
# IPP module tests
python -c "from printsvc import ipp; ..."

# JobStore tests
python -c "from printsvc.server import JobStore; ..."

# Verify all modules import
python -c "from printsvc import ipp, server, winprint, discovery, config, logger, docrender, netutils"

# Run the app locally (dry-run, 5-second test)
PrintSVC.exe --log-level DEBUG
```

## Configuration

`printsvc.json` (auto-detected alongside the exe):

```json
{
    "printer_name": "",          // leave empty for auto-detect
    "ipp_port": 631,
    "listen_address": "0.0.0.0",
    "log_file": "printsvc.log",
    "log_level": "INFO",
    "mDNS_enabled": true
}
```

## Key Technical Details

- **IPP protocol**: Binary format per RFC 2910. Operations and attributes use tag-length-value encoding. The `encode_attribute` function handles all IPP tag types (integer, boolean, enum, rangeOfInteger, keyword, uri, charset, mimeMediaType, text, name, octetString).
- **GDI printing**: Uses `win32ui.CreateDC()` + `ImageWin.Dib.draw()`. The `pDevMode` object can be `None` — code must handle this.
- **Office COM**: `_com_lock` ensures single-threaded access to Office applications (COM STA requirement). Temp files use `tempfile.mkstemp` and paths are normalized with `os.path.normpath` before passing to COM.
- **mDNS**: zeroconf library registers `_ipp._tcp`, `_printer._tcp`, and `_pdl-datastream._tcp` services. On current zeroconf ≥0.132, only binary wheels (cp38-win32) are available — `py3-none-any` wheels stopped around 0.39.x.
- **SSDP**: Port 1900 may conflict with Windows SSDP Discovery Service on Win7. Failure is non-fatal — mDNS still works.
- **Signal handling**: `SIGINT`/`SIGTERM` + `KeyboardInterrupt` catch for clean shutdown. On Win7, `SIGTERM` is not natively supported — only Ctrl+C works in console mode.

## Key Constraints

- **Win7 32-bit**: Python 3.8.10 32-bit is the last compatible version. PyInstaller 6.x works but requires explicit `--add-binary` for `pythoncom38.dll` and `pywintypes38.dll`.
- **Admin rights**: Required for printer access and binding port 631 and SSDP port 1900.
- **Office dependency**: docrender module requires Microsoft Office installed (2007 or later). Without it, the program starts but Office format printing fails gracefully.
- **Built-in HTTP server**: Python's `HTTPServer` is single-threaded (`handle_request()` loop). Adequate for home/office use but not high-concurrency.
- **其他要求**: 对话中你的每次回复都要在最前面加上“答：”
