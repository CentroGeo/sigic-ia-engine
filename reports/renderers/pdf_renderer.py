import io
import re

import markdown as md_lib
import weasyprint


_BASE_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  @page {{
    margin: {margin_regular};
  }}
  @page:first {{
    margin-top: {margin_first_top};
    margin-bottom: {margin_first_bottom};
  }}
  body {{ font-family: Arial, sans-serif; font-size: 12pt; line-height: 1.5; color: #222; }}
  h1, h2, h3 {{ color: #1a1a2e; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
  th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; }}
  th {{ background: #f0f0f0; }}
  pre, code {{ background: #f8f8f8; padding: 2px 4px; font-size: 10pt; }}
</style>
</head>
<body>{body}</body>
</html>"""


def render_pdf(content: str, output_format: str, use_letterhead: bool = False) -> bytes:
    """
    Convierte *content* a bytes PDF.

    Parameters
    ----------
    content       : texto generado por el LLM
    output_format : 'markdown' | 'plain_text'
    use_letterhead: booleano para aplicar formato SECIHTI usando plantilla_secihti.pdf
    """
    import os
    from django.conf import settings
    
    # Limpiar siempre: quitar code fences que el LLM a veces agrega
    content = _strip_code_fences(content)

    body = md_lib.markdown(
        content,
        extensions=["tables", "fenced_code", "nl2br"],
    )

    # Si es membretado, empujar el contenido hacia abajo y abajo-arriba
    # para no chocar con el encabezado/pie precocido en la plantilla física
    if use_letterhead:
        margin_regular = "3.5cm 2cm 3.5cm 2cm"
        margin_first_top = "3.5cm"
        margin_first_bottom = "3.5cm"
    else:
        margin_regular = "2cm 2cm 2cm 2cm"
        margin_first_top = "2cm"
        margin_first_bottom = "2cm"
    
    html_str = _BASE_HTML.format(
        body=body, 
        margin_regular=margin_regular,
        margin_first_top=margin_first_top,
        margin_first_bottom=margin_first_bottom
    )
    pdf_bytes = weasyprint.HTML(string=html_str).write_pdf()
    
    if use_letterhead:
        try:
            from PyPDF2 import PdfReader, PdfWriter
            template_path = os.path.join(settings.BASE_DIR, "reports", "templates", "plantilla_secihti.pdf")
            
            if os.path.exists(template_path):
                template_pdf = PdfReader(template_path)
                content_pdf = PdfReader(io.BytesIO(pdf_bytes))
                writer = PdfWriter()
                
                # Asumimos que la plantilla PDF tiene 1 página que actúa como formato principal
                template_page = template_pdf.pages[0]
                
                for page in content_pdf.pages:
                    # Mezclar la página base de texto con el membrete encima (watermark) o bajo (background)
                    # Aquí la plantilla sirve como fondo (merge_page superpone)
                    new_page = PdfReader(template_path).pages[0] # Clonamos el fondo
                    new_page.merge_page(page) # Ponemos el texto sobre el fondo
                    writer.add_page(new_page)
                    
                buf = io.BytesIO()
                writer.write(buf)
                return buf.getvalue()
            else:
                print(f"[REPORT] ADVERTENCIA: Plantilla {template_path} no encontrada. Generando sin ella.")
        except Exception as e:
            print(f"[REPORT] Error renderizando plantilla PDF: {e}")

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
