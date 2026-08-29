"""
UPI QR-code generator.

Produces a standard UPI deep-link QR as a temporary PNG file.
The caller is responsible for deleting the file after use.
"""

import os
import tempfile
import logging
from decimal import Decimal
from urllib.parse import quote

import qrcode                       # type: ignore[import-untyped]
from qrcode.constants import ERROR_CORRECT_H  # type: ignore[import-untyped]

from config import UPI_ID, UPI_NAME

logger = logging.getLogger(__name__)


def _build_upi_url(amount: Decimal, payment_code: str) -> str:
    """
    Build a UPI deep-link URL.

    Format:
        upi://pay?pa=<UPI_ID>&pn=<UPI_NAME>&am=<AMOUNT>&cu=INR&tn=<PAYMENT_CODE>
    """
    url = (
        f"upi://pay"
        f"?pa={quote(UPI_ID, safe='')}"
        f"&pn={quote(UPI_NAME, safe='')}"
        f"&am={amount}"
        f"&cu=INR"
        f"&tn={quote(payment_code, safe='')}"
    )
    logger.debug("UPI URL: %s", url)
    return url


def generate_qr(amount: Decimal, payment_code: str) -> str:
    """
    Generate a UPI QR code PNG and return the temporary file path.

    Raises:
        RuntimeError: if QR generation or file-write fails.
    """
    try:
        upi_url = _build_upi_url(amount, payment_code)

        qr = qrcode.QRCode(
            version=None,               # auto-size
            error_correction=ERROR_CORRECT_H,
            box_size=10,
            border=4,
        )
        qr.add_data(upi_url)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")

        # Write to a temp file that persists until explicitly deleted
        fd, path = tempfile.mkstemp(suffix=".png", prefix="upi_qr_")
        os.close(fd)
        img.save(path)

        logger.info("QR saved: %s", path)
        return path

    except Exception as exc:
        logger.exception("QR generation failed")
        raise RuntimeError("Failed to generate QR code") from exc
