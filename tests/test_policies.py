import pytest

from emporio.policies import get_retriever


@pytest.fixture(scope="module")
def retriever():
    return get_retriever()


def test_chunks_por_secao(retriever):
    sections = {c.section for c in retriever.chunks}
    assert {"2", "3", "3.1", "4.1", "4.4", "5.1", "6.2", "8.1"} <= sections
    assert all("Página" not in c.text for c in retriever.chunks)


@pytest.mark.parametrize(
    "query, expected",
    [
        ("me arrependi da compra, posso devolver?", "4.1"),
        ("qual o horário de funcionamento no sábado?", "2"),
        ("posso parcelar em 10x?", "3.1"),
        ("o frete é grátis em Campo Grande?", "5.1"),
        ("desconto do pix vale junto com promoção?", "6.2"),
        ("meu violão veio com defeito", "4.2"),
        ("qual o endereço e telefone da loja", "1.2"),
        ("quanto tempo de garantia", "8.1"),
    ],
)
def test_top1_retrieval(retriever, query, expected):
    top = retriever.search(query, k=3)
    assert expected in [c.section for _, c in top[:2]], [c.section for _, c in top]


def test_email_corrigido(retriever):
    text = " ".join(c.text for c in retriever.chunks)
    assert "contato@emporiodamusica.com.br" in text
    assert "empóriodamusica" not in text
