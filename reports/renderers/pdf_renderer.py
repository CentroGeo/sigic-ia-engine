import io
import re

import markdown as md_lib
import weasyprint


_BASE_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: Arial, sans-serif; font-size: 12pt; line-height: 1.5;
          margin: 2cm; color: #222; }}
  h1, h2, h3 {{ color: #1a1a2e; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
  th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; }}
  th {{ background: #f0f0f0; }}
  pre, code {{ background: #f8f8f8; padding: 2px 4px; font-size: 10pt; }}
</style>
</head>
<body>{body}</body>
</html>"""


def render_pdf(content: str, output_format: str) -> bytes:
    """
    Convierte *content* a bytes PDF.

    Parameters
    ----------
    content       : texto generado por el LLM
    output_format : 'markdown' | 'plain_text'
    """
    # Limpiar siempre: quitar code fences que el LLM a veces agrega
    content = _strip_code_fences(content)

    # Siempre pasar por el parser de Markdown: el LLM suele generar markdown
    # incluso cuando se solicita plain_text. El parser lo maneja en ambos casos
    # (texto plano sin formato se convierte en párrafos <p> normalmente).
    body = md_lib.markdown(
        content,
        extensions=["tables", "fenced_code", "nl2br"],
    )

    html_str = _BASE_HTML.format(body=body)
    pdf_bytes = weasyprint.HTML(string=html_str).write_pdf()
    return pdf_bytes


def _strip_code_fences(content: str) -> str:
    """Quita bloques ```...``` o ```markdown...``` que el LLM envuelve a veces."""
    content = content.strip()
    # Quitar fence de apertura: ```markdown, ```md, ``` solos
    content = re.sub(r"^```[a-zA-Z]*\n?", "", content)
    # Quitar fence de cierre
    content = re.sub(r"\n?```$", "", content)
    return content.strip()


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )
