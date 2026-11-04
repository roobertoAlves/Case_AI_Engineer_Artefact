"""Monta um HTML com README + documentação dos exemplos e imprime em PDF (Chrome headless).

Uso: python scripts/gerar_pdf.py   (requer Google Chrome e `pip install markdown`)
Saída: docs/Documentacao_Agente_Emporio_da_Musica.pdf
"""

import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

import markdown

REPO = Path(__file__).resolve().parents[1]
OUT_PDF = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "docs" / "Documentacao_Agente_Emporio_da_Musica.pdf"
HTML = Path(tempfile.gettempdir()) / "emporio_documentacao.html"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

EXAMPLES = sorted((REPO / "examples").glob("0*.md"))


def md_to_html(text: str) -> str:
    # bloco mermaid -> <pre class="mermaid"> (renderizado pelo mermaid.js)
    text = re.sub(r"```mermaid\n(.*?)```", lambda m: f'<pre class="mermaid">{m.group(1)}</pre>', text, flags=re.S)
    # <details> abertos no PDF, e markdown dentro deles processado
    text = text.replace("<details><summary>", '<details open markdown="1"><summary>')
    # Markdown "de chat" (saída do modelo) abre listas sem linha em branco antes;
    # o python-markdown exige a linha em branco, senão junta tudo num parágrafo.
    lines, out = text.split("\n"), []
    for line in lines:
        is_item = re.match(r"^\s*([-*•]|\d+[.)])\s+", line)
        if is_item and out and out[-1].strip() and not re.match(r"^\s*([-*•]|\d+[.)])\s+|^\|", out[-1]):
            out.append("")
        out.append(line)
    text = "\n".join(out)
    return markdown.markdown(text, extensions=["tables", "fenced_code", "md_in_html", "sane_lists"])


def fix_links(html: str) -> str:
    # links relativos não fazem sentido no PDF: vira texto
    return re.sub(r'<a href="(?!https?://|#)[^"]*">(.*?)</a>', r"<code>\1</code>", html)


readme = (REPO / "README.md").read_text(encoding="utf-8")
examples_readme = (REPO / "examples" / "README.md").read_text(encoding="utf-8")

parts = [
    '<section class="cover">'
    "<h1>Empório da Música — Agente de Atendimento</h1>"
    "<p class='sub'>Desafio Técnico AI Engineer · Artefact</p>"
    "<p class='sub'>README e documentação do projeto</p>"
    f"<p class='meta'>Gerado em {date.today():%d/%m/%Y} a partir do repositório</p>"
    "</section>",
    '<section class="doc">' + md_to_html(readme) + "</section>",
    '<section class="doc break">' + md_to_html(examples_readme.replace("# Conversas de exemplo", "# Apêndice A — Conversas de exemplo", 1)) + "</section>",
]
for i, path in enumerate(EXAMPLES, 1):
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"^# ", f"# A.{i} — ", text, count=1, flags=re.M)
    parts.append('<section class="doc break example">' + md_to_html(text) + "</section>")

CSS = """
@page { size: A4; margin: 18mm 16mm 18mm 16mm; }
:root { --ink:#1d2330; --muted:#5b6475; --accent:#1a4f72; --rule:#d9dee7; --code:#f3f5f8; }
body { font-family: "Segoe UI", Arial, sans-serif; color: var(--ink); font-size: 10.5pt; line-height: 1.45; }
h1, h2, h3 { color: var(--accent); line-height: 1.2; break-after: avoid; }
h1 { font-size: 20pt; border-bottom: 2px solid #e8a33d; padding-bottom: 4px; margin-top: 0; }
h2 { font-size: 14pt; margin-top: 18pt; border-bottom: 1px solid var(--rule); padding-bottom: 2px; }
h3 { font-size: 11.5pt; margin-top: 12pt; }
p, li { orphans: 3; widows: 3; }
code { font-family: Consolas, "Courier New", monospace; font-size: 9pt; background: var(--code); padding: 0 3px; border-radius: 3px; }
pre { background: var(--code); padding: 8px 10px; border-radius: 4px; font-size: 8.5pt; white-space: pre-wrap; word-break: break-word; break-inside: avoid; }
pre code { background: none; padding: 0; }
pre.mermaid { background: #fff; text-align: center; }
table { border-collapse: collapse; width: 100%; font-size: 9pt; margin: 8px 0; break-inside: auto; }
th, td { border: 1px solid var(--rule); padding: 4px 6px; vertical-align: top; text-align: left; }
th { background: #eef2f7; }
tr { break-inside: avoid; }
blockquote { border-left: 3px solid #e8a33d; margin: 8px 0; padding: 2px 10px; color: var(--muted); }
hr { border: none; border-top: 1px solid var(--rule); margin: 10px 0; }
details { background: #fafbfc; border: 1px solid var(--rule); border-radius: 4px; padding: 4px 8px; margin: 6px 0; font-size: 8.5pt; }
details code { word-break: break-all; }
summary { font-weight: 600; color: var(--muted); }
.break { break-before: page; }
.cover { height: 250mm; display: flex; flex-direction: column; justify-content: center; text-align: center; }
.cover h1 { font-size: 26pt; border: none; }
.cover .sub { font-size: 13pt; color: var(--muted); margin: 4px 0; }
.cover .meta { margin-top: 30px; color: var(--muted); font-size: 9.5pt; }
.example strong { color: var(--accent); }
"""

HTML.write_text(
    "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
    "<title>Empório da Música — Agente de Atendimento</title>"
    f"<style>{CSS}</style>"
    "<script src='https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js'></script>"
    "<script>mermaid.initialize({startOnLoad:true, theme:'neutral'});</script>"
    "</head><body>" + fix_links("\n".join(parts)) + "</body></html>",
    encoding="utf-8",
)

subprocess.run(
    [CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
     "--virtual-time-budget=15000", f"--print-to-pdf={OUT_PDF}", HTML.as_uri()],
    check=True, capture_output=True,
)
print(f"PDF: {OUT_PDF} ({OUT_PDF.stat().st_size // 1024} KB)")
