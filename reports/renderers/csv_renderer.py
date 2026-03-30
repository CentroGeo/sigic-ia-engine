import csv as csv_mod
import io
import re


def render_csv(content: str) -> bytes:
    """
    Limpia el output del LLM y lo devuelve como bytes UTF-8 CSV.

    Casos que maneja:
    - Código envuelto en ```csv...``` o ```...```
    - Todo el CSV entre comillas dobles "..." (el LLM a veces lo hace)
    - Filas empaquetadas como campos CSV en una sola línea: "fila1","fila2","fila3"
    - Texto introductorio/de cierre alrededor del CSV real
    """
    content = content.strip()

    # 1. Quitar code fences: ```csv...``` o ```...```
    content = re.sub(r"^```[a-zA-Z]*\n?", "", content)
    content = re.sub(r"\n?```$", "", content)
    content = content.strip()

    lines = content.splitlines()

    # 2. Buscar la primera y última línea con comas
    start = 0
    for i, line in enumerate(lines):
        if "," in line:
            start = i
            break

    end = len(lines)
    for i in range(len(lines) - 1, start - 1, -1):
        if "," in lines[i]:
            end = i + 1
            break

    csv_lines = lines[start:end]

    # 3. Detectar si el LLM empaquetó todas las filas como campos en una sola línea:
    #    patrón: "fila_header","fila1","fila2",...  (todo en ≤3 líneas)
    #    Cada campo contiene comas => es en realidad una fila CSV completa.
    csv_lines = _unpack_rows_if_needed(csv_lines)

    # 4. Quitar comilla envolvente simple (primer char " sin cerrar en misma línea)
    if csv_lines:
        first = csv_lines[0]
        last = csv_lines[-1]
        if first.startswith('"') and last.endswith('"'):
            if not first.endswith('"'):
                csv_lines[0] = first[1:]
                csv_lines[-1] = last[:-1]

    # 5. Quitar inline markdown (**bold**, *italic*, `code`) de cada celda
    csv_lines = [_strip_inline_md_csv(line) for line in csv_lines]

    csv_text = "\n".join(csv_lines) + "\n"
    return csv_text.encode("utf-8")


def _strip_inline_md_csv(line: str) -> str:
    """Elimina marcadores de formato Markdown dentro de una línea CSV."""
    line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
    line = re.sub(r"\*(.+?)\*", r"\1", line)
    line = re.sub(r"__(.+?)__", r"\1", line)
    line = re.sub(r"_(.+?)_", r"\1", line)
    line = re.sub(r"`(.+?)`", r"\1", line)
    return line


def _unpack_rows_if_needed(lines: list) -> list:
    """
    Si el LLM generó todas las filas como campos CSV en ≤3 líneas
    (ej: '"header","row1","row2"'), las expande a una línea por fila.
    """
    if not lines or len(lines) > 5:
        return lines

    combined = "\n".join(lines)

    # Intentar parsear como CSV de una sola "superfila"
    try:
        reader = csv_mod.reader(io.StringIO(combined))
        fields = []
        for row in reader:
            fields.extend(row)

        # Si conseguimos múltiples campos y cada uno tiene comas adentro,
        # son filas CSV que el LLM empaquetó erróneamente
        if len(fields) >= 2 and sum(1 for f in fields if "," in f) >= len(fields) * 0.5:
            return [f for f in fields if f.strip()]
    except Exception:
        pass

    return lines
