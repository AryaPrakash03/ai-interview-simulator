"""
resume_parser.py — Extracts text content from uploaded PDF resumes.

Uses pdfplumber for reliable text extraction with fallback handling
for scanned or malformed PDFs.
"""

import pdfplumber
import io
import logging

logger = logging.getLogger(__name__)


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """
    Extract all text content from a PDF file.

    Args:
        file_bytes: Raw bytes of the uploaded PDF file.

    Returns:
        Concatenated text from all pages, stripped and cleaned.

    Raises:
        ValueError: If the PDF contains no extractable text.
        Exception: If the PDF cannot be opened or parsed.
    """
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            pages_text = []
            for i, page in enumerate(pdf.pages):
                text = page.extract_text()
                if text:
                    pages_text.append(text.strip())
                else:
                    logger.warning(f"Page {i + 1}: No text extracted (possibly scanned/image-based).")

            if not pages_text:
                raise ValueError(
                    "No readable text found in the PDF. "
                    "The file may be scanned or image-based."
                )

            full_text = "\n\n".join(pages_text)
            logger.info(f"Extracted {len(full_text)} characters from {len(pdf.pages)} page(s).")
            return full_text

    except ValueError:
        raise
    except Exception as e:
        logger.error(f"PDF parsing failed: {e}")
        raise RuntimeError(f"Failed to parse PDF: {str(e)}")
