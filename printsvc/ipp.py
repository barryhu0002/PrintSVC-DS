"""
IPP (Internet Printing Protocol) 1.1 binary encoder/decoder.
RFC 2910 (Encoding and Transport), RFC 2911 (Model and Semantics).
"""
import struct
import io
import logging
import time as _time
from urllib.parse import quote

logger = logging.getLogger("PrintSVC.IPP")

# --- Operation codes (RFC 2911 §4.4.15) ---
OP_PRINT_JOB = 0x0002
OP_VALIDATE_JOB = 0x0003
OP_CANCEL_JOB = 0x0008
OP_GET_JOB_ATTRS = 0x0009
OP_GET_JOBS = 0x000A
OP_GET_PRINTER_ATTRS = 0x000B
OP_GET_PRINTER_SUPPORTED_VALUES = 0x0013

# --- Status codes (RFC 2911 §13.1) ---
OK = 0x0000
OK_IGNORED_OR_SUBSTITUTED = 0x0001
OK_CONFLICTING_ATTRIBUTES = 0x0002
OK_IGNORED_SUBSCRIPTIONS = 0x0003
OK_IGNORED_NOTIFICATIONS = 0x0004
OK_EVENTS_COMPLETE = 0x0005
OK_REDIRECTION = 0x0006
CLIENT_ERROR_BAD_REQUEST = 0x0400
CLIENT_ERROR_FORBIDDEN = 0x0401
CLIENT_ERROR_NOT_AUTHENTICATED = 0x0402
CLIENT_ERROR_NOT_AUTHORIZED = 0x0403
CLIENT_ERROR_NOT_POSSIBLE = 0x0404
CLIENT_ERROR_TIMEOUT = 0x0405
CLIENT_ERROR_NOT_FOUND = 0x0406
CLIENT_ERROR_GONE = 0x0407
CLIENT_ERROR_REQUEST_ENTITY_TOO_LARGE = 0x0408
CLIENT_ERROR_REQUEST_VALUE_TOO_LONG = 0x0409
CLIENT_ERROR_DOCUMENT_FORMAT_NOT_SUPPORTED = 0x040A
CLIENT_ERROR_ATTRIBUTES_OR_VALUES_NOT_SUPPORTED = 0x040B
CLIENT_ERROR_URI_SCHEME_NOT_SUPPORTED = 0x040C
CLIENT_ERROR_CHARSET_NOT_SUPPORTED = 0x040D
CLIENT_ERROR_CONFLICTING_ATTRIBUTES = 0x040E
CLIENT_ERROR_COMPRESSION_NOT_SUPPORTED = 0x040F
CLIENT_ERROR_COMPRESSION_ERROR = 0x0410
CLIENT_ERROR_DOCUMENT_FORMAT_ERROR = 0x0411
CLIENT_ERROR_DOCUMENT_ACCESS_ERROR = 0x0412
SERVER_ERROR_INTERNAL_ERROR = 0x0500
SERVER_ERROR_OPERATION_NOT_SUPPORTED = 0x0501
SERVER_ERROR_SERVICE_UNAVAILABLE = 0x0502
SERVER_ERROR_VERSION_NOT_SUPPORTED = 0x0503
SERVER_ERROR_DEVICE_ERROR = 0x0504
SERVER_ERROR_TEMPORARY_ERROR = 0x0505
SERVER_ERROR_NOT_ACCEPTING_JOBS = 0x0506
SERVER_ERROR_BUSY = 0x0507
SERVER_ERROR_JOB_CANCELED = 0x0508
SERVER_ERROR_MULTIPLE_DOCUMENT_JOBS_NOT_SUPPORTED = 0x0509

# --- Tag values for attribute syntax (RFC 2911 §4.1) ---
TAG_UNSUPPORTED = 0x10
TAG_UNKNOWN = 0x12
TAG_NO_VALUE = 0x13
TAG_INTEGER = 0x21
TAG_BOOLEAN = 0x22
TAG_ENUM = 0x23
TAG_STRING = 0x30  # octetString (undefined)
TAG_DATETIME = 0x31
TAG_RESOLUTION = 0x32
TAG_RANGEOFINTEGER = 0x33
TAG_BEGIN_COLLECTION = 0x34
TAG_END_COLLECTION = 0x37
TAG_TEXT_W_LANG = 0x35
TAG_NAME_W_LANG = 0x36
TAG_TEXT_WO_LANG = 0x41
TAG_NAME_WO_LANG = 0x42
TAG_KEYWORD = 0x44
TAG_URI = 0x45
TAG_URI_SCHEME = 0x46
TAG_CHARSET = 0x47
TAG_NATURAL_LANGUAGE = 0x48
TAG_MIME_MEDIA_TYPE = 0x49

# --- Delimiter tags ---
TAG_OPERATION = 0x01
TAG_JOB = 0x02
TAG_END = 0x03
TAG_PRINTER = 0x04
TAG_UNSUPPORTED_GROUP = 0x05

# --- Attribute names we care about ---
# These attributes are always text strings regardless of IPP tag encoding
_TEXT_ATTR_NAMES = frozenset({
    "attributes-charset", "attributes-natural-language",
    "printer-uri", "job-uri",
    "job-id", "requesting-user-name",
    "job-name", "document-format",
    "document-natural-language",
    "sides", "media",
    "printer-resolution",
})

_STATUS_STR = {
    OK: "successful-ok",
    CLIENT_ERROR_BAD_REQUEST: "client-error-bad-request",
    CLIENT_ERROR_FORBIDDEN: "client-error-forbidden",
    CLIENT_ERROR_NOT_FOUND: "client-error-not-found",
    CLIENT_ERROR_DOCUMENT_FORMAT_NOT_SUPPORTED: "client-error-document-format-not-supported",
    CLIENT_ERROR_ATTRIBUTES_OR_VALUES_NOT_SUPPORTED: "client-error-attributes-or-values-not-supported",
    SERVER_ERROR_INTERNAL_ERROR: "server-error-internal-error",
    SERVER_ERROR_OPERATION_NOT_SUPPORTED: "server-error-operation-not-supported",
    SERVER_ERROR_DEVICE_ERROR: "server-error-device-error",
}


def status_str(code):
    return _STATUS_STR.get(code, f"0x{code:04X}")


# --- Encoder ---


def encode_attribute(name, value_tag, value):
    """Encode a single IPP attribute. Returns bytes.

    value must be:
      - int for TAG_INTEGER, TAG_ENUM
      - bool for TAG_BOOLEAN
      - (int, int) tuple for TAG_RANGEOFINTEGER
      - str for text-based types (keyword, uri, charset, etc.)
      - bytes for TAG_STRING (octetString)
    """
    buf = io.BytesIO()
    buf.write(struct.pack("!B", value_tag))
    name_bytes = name.encode("ascii", errors="replace")
    buf.write(struct.pack("!H", len(name_bytes)))
    buf.write(name_bytes)
    if value_tag == TAG_INTEGER or value_tag == TAG_ENUM:
        buf.write(struct.pack("!H", 4))
        buf.write(struct.pack("!i", int(value)))
    elif value_tag == TAG_BOOLEAN:
        buf.write(struct.pack("!H", 1))
        buf.write(struct.pack("!B", 1 if value else 0))
    elif value_tag == TAG_RANGEOFINTEGER:
        buf.write(struct.pack("!H", 8))
        buf.write(struct.pack("!ii", int(value[0]), int(value[1])))
    elif value_tag == TAG_BEGIN_COLLECTION:
        # Collection: value is a list of (name, tag, val) member tuples
        # First encode all members to calculate total value-length
        members_buf = io.BytesIO()
        for member_name, member_tag, member_val in value:
            members_buf.write(encode_attribute(member_name, member_tag, member_val))
        member_data = members_buf.getvalue()
        # Write value-length = members + 1 byte for end-collection (RFC 3380 §4.2)
        buf.write(struct.pack("!H", len(member_data) + 1))
        buf.write(member_data)
        # End collection marker — single byte (0x1B), no trailing fields
        buf.write(struct.pack("!B", TAG_END_COLLECTION))
    elif isinstance(value, str):
        # Text-based types: keyword, uri, charset, naturalLanguage, name, mimeMediaType, text
        value_bytes = value.encode("utf-8")
        buf.write(struct.pack("!H", len(value_bytes)))
        buf.write(value_bytes)
    elif isinstance(value, bytes):
        # Binary types: octetString (TAG_STRING), unknown
        buf.write(struct.pack("!H", len(value)))
        buf.write(value)
    else:
        raise TypeError(f"Unsupported value type for tag 0x{value_tag:02X}: {type(value).__name__}")
    return buf.getvalue()


def encode_group(delimiter_tag, attributes):
    """Encode a group of attributes preceded by a delimiter tag.
    Each attribute is a 3-tuple: (name, tag, value)."""
    buf = io.BytesIO()
    buf.write(struct.pack("!B", delimiter_tag))
    for attr in attributes:
        name, value_tag, value = attr
        buf.write(encode_attribute(name, value_tag, value))
    return buf.getvalue()


def encode_ipp_response(version_major, version_minor, status_code, request_id,
                        operation_attrs, printer_attrs=None, job_attrs=None,
                        unsupported_attrs=None):
    """Build a full IPP response binary."""
    buf = io.BytesIO()
    buf.write(struct.pack("!BB", version_major, version_minor))
    buf.write(struct.pack("!H", status_code))
    buf.write(struct.pack("!I", request_id))

    buf.write(encode_group(TAG_OPERATION, operation_attrs))

    if printer_attrs:
        buf.write(encode_group(TAG_PRINTER, printer_attrs))

    if job_attrs:
        buf.write(encode_group(TAG_JOB, job_attrs))

    if unsupported_attrs:
        buf.write(encode_group(TAG_UNSUPPORTED_GROUP, unsupported_attrs))

    buf.write(struct.pack("!B", TAG_END))
    return buf.getvalue()


# --- Decoder ---

class IPPAttribute:
    """Represents a single IPP attribute."""
    __slots__ = ("name", "tag", "value")

    def __init__(self, name, tag, value):
        self.name = name
        self.tag = tag
        self.value = value

    def __repr__(self):
        return f"<IPPAttr {self.name} tag=0x{self.tag:02X} val={self.value!r}>"


class IPPRequest:
    """Parsed IPP request."""
    __slots__ = ("version_major", "version_minor", "operation_id", "request_id",
                 "operation_attrs", "printer_attrs", "job_attrs", "document")

    def __init__(self):
        self.version_major = 1
        self.version_minor = 1
        self.operation_id = 0
        self.request_id = 0
        self.operation_attrs = []
        self.printer_attrs = []
        self.job_attrs = []
        self.document = b""

    def get_op_attr(self, name, default=None):
        for a in self.operation_attrs:
            if a.name == name:
                return a.value
        return default

    def get_printer_attr(self, name, default=None):
        for a in self.printer_attrs:
            if a.name == name:
                return a.value
        return default

    @property
    def document_format(self):
        return self.get_op_attr("document-format", "application/octet-stream")

    @property
    def job_name(self):
        return self.get_op_attr("job-name", "Untitled")

    @property
    def username(self):
        return self.get_op_attr("requesting-user-name", "unknown")


def _read_attr_value(stream, tag, length):
    """Read attribute value of given tag and length from stream."""
    if tag in (TAG_INTEGER, TAG_ENUM):
        data = stream.read(4)
        if len(data) < 4:
            return None
        return struct.unpack("!i", data)[0]
    elif tag == TAG_BOOLEAN:
        data = stream.read(1)
        return data[0] != 0 if data else None
    elif tag == TAG_RANGEOFINTEGER:
        data = stream.read(8)
        if len(data) < 8:
            return None
        lo, hi = struct.unpack("!ii", data)
        return (lo, hi)
    elif tag in (TAG_TEXT_WO_LANG, TAG_NAME_WO_LANG, TAG_KEYWORD, TAG_URI,
                 TAG_URI_SCHEME, TAG_CHARSET, TAG_NATURAL_LANGUAGE, TAG_MIME_MEDIA_TYPE,
                 TAG_TEXT_W_LANG, TAG_NAME_W_LANG):
        return stream.read(length).decode("utf-8", errors="replace")
    elif tag == TAG_STRING:
        return stream.read(length)
    else:
        return stream.read(length)  # raw bytes


def parse_ipp_request(data):
    """Parse an IPP request from raw bytes."""
    stream = io.BytesIO(data)
    req = IPPRequest()

    header = stream.read(8)
    if len(header) < 8:
        raise ValueError("Truncated IPP header")
    req.version_major, req.version_minor, req.operation_id, req.request_id = \
        struct.unpack("!BBHI", header)

    current_group = req.operation_attrs
    while True:
        tag = stream.read(1)
        if not tag:
            break
        tag = tag[0]

        if tag == TAG_END:
            break
        elif tag == TAG_OPERATION:
            current_group = req.operation_attrs
            continue
        elif tag == TAG_JOB:
            current_group = req.job_attrs
            continue
        elif tag == TAG_PRINTER:
            current_group = req.printer_attrs
            continue
        elif tag == TAG_UNSUPPORTED_GROUP:
            current_group = []  # discard unsupported
            continue

        # Read attribute
        name_len_bytes = stream.read(2)
        if len(name_len_bytes) < 2:
            break
        name_len = struct.unpack("!H", name_len_bytes)[0]
        name = stream.read(name_len).decode("ascii", errors="replace")

        val_len_bytes = stream.read(2)
        if len(val_len_bytes) < 2:
            break
        val_len = struct.unpack("!H", val_len_bytes)[0]
        value = _read_attr_value(stream, tag, val_len)
        if value is not None:
            # Normalize bytes to str for known text attributes
            # (some clients send text attributes as TAG_STRING/octetString)
            if isinstance(value, bytes) and name in _TEXT_ATTR_NAMES:
                value = value.decode("utf-8", errors="replace")
            current_group.append(IPPAttribute(name, tag, value))

    # Rest of data is the document
    req.document = stream.read()
    return req


# --- Helper functions for building common responses ---

def make_printer_attributes(printer_name, printer_state=3, state_reason="none",
                            accepting_jobs=True, formats=None, supported=None,
                            host_ip=None, printer_uuid=None, printer_up_time=None,
                            make_model=None, device_id=None):
    """Build the standard printer attribute list for Get-Printer-Attributes response.
    host_ip: actual IP address for printer-uri-supported (if None, uses localhost).
    printer_uuid: UUID string matching mDNS TXT record (required by Mopria).
    printer_up_time: seconds since printer started (required by Mopria).
    make_model: human-readable make/model string (default: "PrintSVC Network Printer").
    device_id: printer-device-id string per IEEE 1284.4 (default: generic).
    """
    if formats is None:
        formats = [
            "application/pdf",
            "image/png",
            "image/jpeg",
            "image/tiff",
            "application/octet-stream",
            # Microsoft Office
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/msword",
            "application/vnd.ms-excel",
            "application/vnd.ms-powerpoint",
        ]
    if supported is None:
        supported = {
            "sides": ["one-sided", "two-sided-long-edge", "two-sided-short-edge"],
            "media": [
                "iso_a4_210x297mm", "iso_a3_297x420mm",
                "na_letter_8.5x11in", "na_legal_8.5x14in",
                "jis_b5_182x257mm", "jis_b4_257x364mm",
            ],
            "finishings": [3],  # 3 = none
            "orientation-requested": [1, 2, 3, 4],  # portrait, landscape, rev-portrait, rev-landscape
            "print-quality": [3, 4, 5],  # draft, normal, high
            "resolution": ["600x600dpi", "300x300dpi"],
        }

    if state_reason is None:
        state_reason = "none"

    encoded_name = quote(printer_name, safe="")
    host = host_ip or "localhost"
    rp_path = "ipp/print"
    uri_base = f"ipp://{host}:631/{rp_path}"

    attrs = [
        ("printer-uri-supported", TAG_URI, uri_base),
        ("uri-authentication-supported", TAG_KEYWORD, "requesting-user-name"),
        ("uri-security-supported", TAG_KEYWORD, "none"),
        ("printer-name", TAG_NAME_WO_LANG, printer_name),
        ("printer-state", TAG_ENUM, printer_state),  # 3=idle
        ("printer-state-reasons", TAG_KEYWORD, state_reason),
        ("printer-is-accepting-jobs", TAG_BOOLEAN, accepting_jobs),
        ("printer-uuid", TAG_URI, printer_uuid or "urn:uuid:00000000-0000-0000-0000-000000000000"),
        ("queued-job-count", TAG_INTEGER, 0),
        ("color-supported", TAG_BOOLEAN, False),  # monochrome printer
        ("operations-supported", TAG_ENUM, OP_PRINT_JOB),
        ("operations-supported", TAG_ENUM, OP_VALIDATE_JOB),
        ("operations-supported", TAG_ENUM, OP_CANCEL_JOB),
        ("operations-supported", TAG_ENUM, OP_GET_JOB_ATTRS),
        ("operations-supported", TAG_ENUM, OP_GET_JOBS),
        ("operations-supported", TAG_ENUM, OP_GET_PRINTER_ATTRS),
        ("charset-configured", TAG_CHARSET, "utf-8"),
        ("charset-supported", TAG_CHARSET, "utf-8"),
        ("natural-language-configured", TAG_NATURAL_LANGUAGE, "en"),
        ("natural-language-supported", TAG_NATURAL_LANGUAGE, "en"),
        ("printer-make-and-model", TAG_TEXT_WO_LANG, make_model or "PrintSVC Network Printer"),
        ("printer-location", TAG_TEXT_WO_LANG, ""),
        ("printer-info", TAG_TEXT_WO_LANG, "PrintSVC Network Print Service"),
        ("printer-up-time", TAG_INTEGER, printer_up_time if printer_up_time is not None else 0),
        ("compression-supported", TAG_KEYWORD, "none"),
        ("job-creation-attributes-supported", TAG_KEYWORD, "document-format"),
        ("job-creation-attributes-supported", TAG_KEYWORD, "job-name"),
        ("job-creation-attributes-supported", TAG_KEYWORD, "copies"),
        ("job-creation-attributes-supported", TAG_KEYWORD, "sides"),
        ("job-creation-attributes-supported", TAG_KEYWORD, "media"),
        ("job-creation-attributes-supported", TAG_KEYWORD, "media-col"),
        ("job-creation-attributes-supported", TAG_KEYWORD, "finishings"),
        ("page-ranges-supported", TAG_BOOLEAN, True),
        ("multiple-document-jobs-supported", TAG_BOOLEAN, False),
        ("number-up-supported", TAG_BOOLEAN, True),
        ("reference-uri-schemes-supported", TAG_URI_SCHEME, "ipp"),
        ("ipp-versions-supported", TAG_KEYWORD, "1.1"),
    ]

    # Mopria certification attributes (required for Android discovery)
    attrs.append(("mopria-certified", TAG_KEYWORD, "1.3"))
    attrs.append(("print-color-mode-supported", TAG_KEYWORD, "monochrome"))
    attrs.append(("print-scaling-supported", TAG_KEYWORD, "auto"))
    attrs.append(("print-scaling-default", TAG_KEYWORD, "auto"))
    attrs.append(("media-default", TAG_KEYWORD, "iso_a4_210x297mm"))
    # media-col-supported: declare which members are valid in media-col collections
    attrs.append(("media-col-supported", TAG_KEYWORD, "media-size"))
    attrs.append(("media-col-supported", TAG_KEYWORD, "media-size-name"))
    # media-col-default: collection with media-size and media-size-name (A4)
    attrs.append(("media-col-default", TAG_BEGIN_COLLECTION, [
        ("media-size", TAG_BEGIN_COLLECTION, [
            ("x-dimension", TAG_INTEGER, 21000),
            ("y-dimension", TAG_INTEGER, 29700),
        ]),
        ("media-size-name", TAG_KEYWORD, "iso_a4_210x297mm"),
    ]))
    # media-col-ready: collection entries for each supported media size
    _media_col_entries = [
        ("iso_a4_210x297mm", 21000, 29700),
        ("iso_a3_297x420mm", 29700, 42000),
        ("na_letter_8.5x11in", 21590, 27940),
        ("na_legal_8.5x14in", 21590, 35560),
        ("jis_b5_182x257mm", 18200, 25700),
        ("jis_b4_257x364mm", 25700, 36400),
    ]
    for size_name, x_dim, y_dim in _media_col_entries:
        attrs.append(("media-col-ready", TAG_BEGIN_COLLECTION, [
            ("media-size", TAG_BEGIN_COLLECTION, [
                ("x-dimension", TAG_INTEGER, x_dim),
                ("y-dimension", TAG_INTEGER, y_dim),
            ]),
            ("media-size-name", TAG_KEYWORD, size_name),
        ]))
    # media-ready: keywords for each supported media
    for size_name, _, _ in _media_col_entries:
        attrs.append(("media-ready", TAG_KEYWORD, size_name))
    attrs.append(("printer-device-id", TAG_TEXT_WO_LANG,
                  device_id or "MFG:PrintSVC;MDL:Network Printer;CMD:PDF,JPEG,PNG;"))
    attrs.append(("printer-dns-sd-name", TAG_NAME_WO_LANG, f"PrintSVC-{printer_name}".replace(" ", "-")))
    attrs.append(("printer-output-tray", TAG_KEYWORD, "top"))
    attrs.append(("output-bin-supported", TAG_KEYWORD, "top"))
    attrs.append(("media-type-supported", TAG_KEYWORD, "stationery"))
    attrs.append(("media-left-margin-supported", TAG_INTEGER, 300))
    attrs.append(("media-right-margin-supported", TAG_INTEGER, 300))
    attrs.append(("media-top-margin-supported", TAG_INTEGER, 300))
    attrs.append(("media-bottom-margin-supported", TAG_INTEGER, 300))
    attrs.append(("job-pages-per-set-supported", TAG_BOOLEAN, False))
    attrs.append(("pwg-raster-document-sheet-back", TAG_KEYWORD, "normal"))
    attrs.append(("document-format-details-supported", TAG_KEYWORD, "document-format"))
    # PCLm/PCLm/ePCL compatibility
    attrs.append(("epcl-version-supported", TAG_KEYWORD, "2.1"))
    attrs.append(("pclm-raster-back-side", TAG_KEYWORD, "flipped"))
    attrs.append(("pclm-strip-height-preferred", TAG_INTEGER, 0))
    attrs.append(("pclm-compression-method-preferred", TAG_KEYWORD, "none"))
    attrs.append(("pclm-source-resolution-supported", TAG_KEYWORD, "600"))

    for fmt in formats:
        attrs.append(("document-format-supported", TAG_MIME_MEDIA_TYPE, fmt))

    for side in supported.get("sides", ["one-sided"]):
        attrs.append(("sides-supported", TAG_KEYWORD, side))
    for media in supported.get("media", []):
        attrs.append(("media-supported", TAG_KEYWORD, media))
    for fin in supported.get("finishings", [3]):
        attrs.append(("finishings-supported", TAG_ENUM, fin))
    for orient in supported.get("orientation-requested", [1]):
        attrs.append(("orientation-requested-supported", TAG_ENUM, orient))
    for qual in supported.get("print-quality", [4]):
        attrs.append(("print-quality-supported", TAG_ENUM, qual))
    for res in supported.get("resolution", ["600x600dpi"]):
        attrs.append(("printer-resolution-supported", TAG_KEYWORD, res))

    # Mopria requires copies-supported even if we only support 1
    attrs.append(("copies-supported", TAG_RANGEOFINTEGER, (1, 99)))

    return attrs


def make_job_attributes(job_id, printer_uri, status="pending", job_name="Untitled",
                        username="unknown", document_format="application/pdf",
                        copies=1, sides="one-sided", job_state=3):
    """Build job attribute list for job-related responses."""
    state_map = {"pending": 3, "pending-held": 4, "processing": 5,
                 "stopped": 6, "canceled": 7, "aborted": 8, "completed": 9}

    return [
        ("job-uri", TAG_URI, f"{printer_uri}/{job_id}"),
        ("job-id", TAG_INTEGER, job_id),
        ("job-name", TAG_NAME_WO_LANG, job_name),
        ("job-originating-user-name", TAG_NAME_WO_LANG, username),
        ("job-state", TAG_ENUM, state_map.get(status, 3)),
        ("job-state-reasons", TAG_KEYWORD, "none"),
        ("copies", TAG_INTEGER, copies),
        ("sides", TAG_KEYWORD, sides),
        ("document-format-supplied", TAG_MIME_MEDIA_TYPE, document_format),
        ("job-k-octets", TAG_INTEGER, 0),
        ("job-impressions", TAG_INTEGER, 0),
        ("job-media-sheets", TAG_INTEGER, 0),
        ("job-printer-up-time", TAG_INTEGER, 0),
        ("time-at-creation", TAG_INTEGER, 0),
        ("time-at-processing", TAG_INTEGER, 0),
        ("time-at-completed", TAG_INTEGER, 0),
    ]
