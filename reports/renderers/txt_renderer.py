import re


def render_txt(content: str) -> bytes:
    """Devuelve el contenido como texto plano limpio, sin caracteres Markdown."""
    content = _strip_markdown(content)
    return content.encode("utf-8")


def _strip_markdown(text: str) -> str:
    """Elimina la sintaxis Markdown para producir texto plano legible."""
    text = text.strip()

    # Code fences: ```lang\n...\n```
    text = re.sub(r"```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"```", "", text)

    lines = []
    for line in text.splitlines():
        # Headings → texto en mayúsculas (h1/h2) o title case (h3)
        if re.match(r"^### ", line):
            line = line[4:].strip()
        elif re.match(r"^## ", line):
            line = line[3:].strip().upper()
        elif re.match(r"^# ", line):
            line = line[2:].strip().upper()

        # Líneas de separador horizontal --- / *** / ___
        if re.match(r"^[-*_]{3,}\s*$", line):
            line = ""

        # Bold e italic: **text**, *text*, __text__, _text_
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"\*(.+?)\*", r"\1", line)
        line = re.sub(r"__(.+?)__", r"\1", line)
        line = re.sub(r"_(.+?)_", r"\1", line)

        # Inline code: `code`
        line = re.sub(r"`(.+?)`", r"\1", line)

        # Blockquotes: > texto
        line = re.sub(r"^>\s?", "", line)

        lines.append(line)

    return "\n".join(lines)
