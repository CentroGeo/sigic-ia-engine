import io
import re

from docx import Document
from docx.shared import Pt


def render_docx(content: str, output_format: str) -> bytes:
    """
    Convierte *content* a bytes DOCX.

    Parameters
    ----------
    content       : texto generado por el LLM
    output_format : 'markdown' | 'plain_text'
    """
    # Limpiar siempre: quitar code fences que el LLM a veces agrega
    content = _strip_code_fences(content)

    doc = Document()

    # Siempre parsear como markdown: el LLM suele generar markdown incluso
    # cuando se solicita plain_text. El parser lo maneja en ambos casos.
    _parse_markdown_to_docx(doc, content)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _parse_markdown_to_docx(doc: Document, content: str) -> None:
    """Convierte Markdown básico a párrafos/headings de docx."""
    lines = content.splitlines()
    in_table = False
    table_rows: list[list[str]] = []

    for line in lines:
        # Heading 1
        if line.startswith("# "):
            _flush_table(doc, table_rows)
            table_rows = []
            in_table = False
            doc.add_heading(line[2:].strip(), level=1)

        # Heading 2
        elif line.startswith("## "):
            _flush_table(doc, table_rows)
            table_rows = []
            in_table = False
            doc.add_heading(line[3:].strip(), level=2)

        # Heading 3
        elif line.startswith("### "):
            _flush_table(doc, table_rows)
            table_rows = []
            in_table = False
            doc.add_heading(line[4:].strip(), level=3)

        # Table row
        elif line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            # Ignore separator rows like |---|---|
            if all(re.match(r"^[-: ]+$", c) for c in cells if c):
                continue
            table_rows.append(cells)
            in_table = True

        # Bullet list
        elif line.startswith("- ") or line.startswith("* "):
            _flush_table(doc, table_rows)
            table_rows = []
            in_table = False
            p = doc.add_paragraph(line[2:].strip(), style="List Bullet")

        # Numbered list
        elif re.match(r"^\d+\. ", line):
            _flush_table(doc, table_rows)
            table_rows = []
            in_table = False
            text = re.sub(r"^\d+\. ", "", line).strip()
            doc.add_paragraph(text, style="List Number")

        # Empty line — flush pending table if any
        elif line.strip() == "":
            if in_table:
                _flush_table(doc, table_rows)
                table_rows = []
                in_table = False
            # else just skip blank lines

        # Regular paragraph — strip inline markdown bold/italic
        else:
            _flush_table(doc, table_rows)
            table_rows = []
            in_table = False
            clean = _strip_inline_md(line)
            if clean:
                doc.add_paragraph(clean)

    # Flush any remaining table
    _flush_table(doc, table_rows)


def _parse_plain_text_to_docx(doc: Document, content: str) -> None:
    """Divide por doble salto de línea y agrega un párrafo por bloque."""
    blocks = content.split("\n\n")
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        # Lines in ALL CAPS treated as headings
        first_line = block.splitlines()[0].strip()
        if first_line == first_line.upper() and len(first_line) > 3:
            doc.add_heading(first_line, level=2)
            rest = "\n".join(block.splitlines()[1:]).strip()
            if rest:
                doc.add_paragraph(rest)
        else:
            doc.add_paragraph(block)


def _flush_table(doc: Document, rows: list[list[str]]) -> None:
    if not rows:
        return
    max_cols = max(len(r) for r in rows)
    table = doc.add_table(rows=0, cols=max_cols)
    table.style = "Table Grid"
    for i, row_data in enumerate(rows):
        row = table.add_row()
        for j, cell_text in enumerate(row_data):
            if j < max_cols:
                row.cells[j].text = cell_text
                if i == 0:
                    for run in row.cells[j].paragraphs[0].runs:
                        run.bold = True


def _strip_code_fences(content: str) -> str:
    """Quita bloques ```...``` o ```markdown...``` que el LLM envuelve a veces."""
    content = content.strip()
    content = re.sub(r"^```[a-zA-Z]*\n?", "", content)
    content = re.sub(r"\n?```$", "", content)
    return content.strip()


def _strip_inline_md(text: str) -> str:
    """Remove bold/italic markers from a line."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"_(.+?)_", r"\1", text)
    return text
