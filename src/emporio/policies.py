"""Retrieval sobre o manual de políticas da loja (data/políticas_da_loja.pdf).

Estratégia: o PDF é curto (8 páginas, ~2,5 mil palavras) e bem estruturado em
seções numeradas. Em vez de chunking por tamanho fixo + embeddings, cada
subseção (ex.: "4.1 Direito de Arrependimento") vira um chunk íntegro, e a busca
é BM25 com um stemming simples por prefixo. Isso mantém regras completas juntas
(nada de uma regra cortada ao meio) e dispensa modelo de embeddings/vetor DB.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pypdf import PdfReader

from emporio.data import DATA_DIR, normalize

POLICY_PDF = DATA_DIR / "políticas_da_loja.pdf"

# Correções de inconsistências encontradas no próprio manual (ver README).
TEXT_CORRECTIONS = {
    # O e-mail aparece com acento na tabela 1.2 e sem acento na capa; domínio com
    # acento é quase certamente erro de digitação.
    "contato@empóriodamusica.com.br": "contato@emporiodamusica.com.br",
}

STOPWORDS = set(
    normalize(
        "a o as os de da do das dos e em no na nos nas um uma para por com sem que se "
        "ao aos à às é ser como mais ou meu minha qual quais quanto posso pode eu voces "
        "voce vocês você tem ter sobre isso esse essa este esta"
    ).split()
)

# Expansão de consulta: cobre lacunas de vocabulário entre o cliente e o manual
# (ex.: "quanto tempo demora" x "prazo", "Dourados" x "outras cidades").
QUERY_EXPANSIONS = {
    "demora": "prazo", "tempo": "prazo", "quando": "prazo", "chega": "entrega prazo",
    "dourados": "outras cidades", "tres": "outras cidades", "corumba": "outras cidades",
    "ponta": "outras cidades", "interior": "outras cidades", "correios": "outras cidades",
    "cep": "outras cidades frete", "envio": "entrega frete", "mandam": "entrega frete",
    "abre": "horario funcionamento", "fecha": "horario funcionamento", "aberto": "horario funcionamento",
    "aberta": "horario funcionamento", "domingo": "horario funcionamento", "feriado": "horario funcionamento",
    "pagar": "pagamento", "cartao": "pagamento credito", "vezes": "parcelamento",
    "arrependi": "arrependimento devolucao", "desistir": "arrependimento devolucao",
    "quebrado": "defeito", "estragou": "defeito", "dados": "privacidade",
    "onde": "endereco", "telefone": "empresa", "whatsapp": "empresa",
}

_HEADING = re.compile(r"^(\d+)\.(\d+)?\s+(\S.*)$")


@dataclass
class PolicyChunk:
    section: str  # ex.: "4.1"
    title: str  # ex.: "Política de Trocas e Devoluções > Direito de Arrependimento"
    text: str

    def render(self) -> str:
        return f"[Seção {self.section} — {self.title}]\n{self.text}"


def _clean_page(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line or line.startswith("Empório da Música Manual de Políticas") or re.fullmatch(
            r"Página \d+", line
        ):
            continue
        for wrong, right in TEXT_CORRECTIONS.items():
            line = line.replace(wrong, right)
        lines.append(line)
    return lines


def load_chunks(pdf_path: Path = POLICY_PDF) -> list[PolicyChunk]:
    reader = PdfReader(pdf_path)
    lines: list[str] = []
    for page in reader.pages[1:]:  # página 0 é a capa
        lines.extend(_clean_page(page.extract_text(extraction_mode="layout")))

    chunks: list[PolicyChunk] = []
    parent_title = ""
    section, title, body = None, "", []

    def flush():
        if section and body:
            chunks.append(PolicyChunk(section, title, _join_lines(body)))

    for line in lines:
        m = _HEADING.match(line)
        # Títulos são curtos e não terminam em pontuação; evita confundir com itens de lista.
        if m and len(line) < 80 and not line.endswith((".", ",", ";", ":")):
            flush()
            major, minor, heading = m.groups()
            if minor is None:
                parent_title = heading
                section, title = major, heading
            else:
                section, title = f"{major}.{minor}", f"{parent_title} > {heading}"
            body = []
        elif section:
            body.append(line)
    flush()
    return chunks


def _join_lines(lines: list[str]) -> str:
    """Junta linhas quebradas pelo layout, preservando itens de lista e tabelas."""
    out: list[str] = []
    for line in lines:
        starts_new = line.startswith(("•", "-")) or re.match(r"^\d+\.\s", line)
        if out and not starts_new and out[-1] and not out[-1].endswith((".", ":")) and line[:1].islower():
            out[-1] += " " + line
        else:
            out.append(line)
    return "\n".join(out)


def _tokens(text: str) -> list[str]:
    words = re.findall(r"\w+", normalize(text))
    # Stemming por prefixo de 5 letras: devolver/devolução -> "devol", parcelar/parcelamento -> "parce".
    return [w[:5] for w in words if w not in STOPWORDS and (len(w) > 1 or w.isdigit())]


class PolicyRetriever:
    """BM25 clássico (k1=1.5, b=0.75) sobre os chunks de política."""

    def __init__(self, chunks: list[PolicyChunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        # O título entra duas vezes: termos do título são o sinal mais forte do assunto.
        self.docs = [Counter(_tokens(f"{c.title} {c.title} {c.text}")) for c in chunks]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg_len = sum(self.lengths) / len(self.lengths)
        df = Counter(t for d in self.docs for t in d)
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, k: int = 3) -> list[tuple[float, PolicyChunk]]:
        words = re.findall(r"\w+", normalize(query))
        expanded = " ".join([query, *(QUERY_EXPANSIONS[w] for w in words if w in QUERY_EXPANSIONS)])
        q = set(_tokens(expanded))
        scored = []
        for doc, length, chunk in zip(self.docs, self.lengths, self.chunks):
            score = 0.0
            for t in q:
                tf = doc.get(t, 0)
                if tf:
                    denom = tf + self.k1 * (1 - self.b + self.b * length / self.avg_len)
                    score += self.idf[t] * tf * (self.k1 + 1) / denom
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda x: -x[0])
        return scored[:k]

    def sections(self) -> list[str]:
        return [f"{c.section} {c.title}" for c in self.chunks]


@lru_cache(maxsize=1)
def get_retriever() -> PolicyRetriever:
    return PolicyRetriever(load_chunks())


if __name__ == "__main__":
    import sys

    retriever = get_retriever()
    if len(sys.argv) > 1:
        for score, chunk in retriever.search(" ".join(sys.argv[1:])):
            print(f"--- {score:.2f}\n{chunk.render()}\n")
    else:
        print("\n".join(retriever.sections()))
