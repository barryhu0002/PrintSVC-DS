==========================================
  PrintSVC - Network Print Service
  For Toshiba E-Studio 240s (legacy printer)
==========================================

WHAT IS IT?
PrintSVC turns a legacy USB/LPT printer into a network printer.
Any device on the same LAN (Android phone, Windows PC, iPad, etc.)
can print to it without installing special apps.

HOW TO USE:
1. Make sure the printer is connected and powered on
2. Double-click start_printsvc.bat
3. Open http://localhost:631/ in a browser to check status
4. On your phone/other PC:
   - Android: Open file -> Print -> Select "PrintSVC"
   - Windows: Settings -> Printers & scanners -> Add device
   - iOS:     Open file -> Print -> Select "PrintSVC"

REQUIREMENTS:
- Windows 7 or later
- Printer driver installed and working
- All devices on the same network

CONFIGURATION:
Edit printsvc.json (restart required):
  "printer_name": ""      - Leave empty for auto-detect
  "ipp_port": 631         - IPP service port
  "log_level": "INFO"     - Set to "DEBUG" for detailed logs

TROUBLESHOOTING:
1. Check printsvc.log for error messages
2. Ensure firewall allows port 631 (IPP) and 5353 (mDNS)
3. Run from command line to see real-time output:
     PrintSVC.exe --log-level DEBUG

==========================================
  PrintSVC v1.0.0
==========================================
