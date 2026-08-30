from pathlib import Path
import fitz
from docx import Document


def extract_text(filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        doc = fitz.open(stream=data, filetype="pdf")
        return "\n".join(page.get_text("text") for page in doc)
    if suffix == ".docx":
        import io
        doc = Document(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs)
    if suffix in {".txt", ".md", ".csv"}:
        return data.decode("utf-8", errors="ignore")
    raise ValueError("Supported files: PDF, DOCX, TXT, MD, CSV")
