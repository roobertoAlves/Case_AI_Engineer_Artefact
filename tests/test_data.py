from datetime import date

import pytest

from emporio.data import Store, normalize


@pytest.fixture(scope="module")
def store():
    return Store()


def test_normalize_remove_acentos():
    assert normalize("Violão Elétrico") == "violao eletrico"


def test_busca_violoes_ate_1000_so_traz_violoes_disponiveis(store):
    results = store.search_products("violão", max_price=1000)
    assert results
    assert all(p.category == "Violões" for p in results)
    assert all(p.available and p.final_price <= 1000 for p in results)
    # Giannini GF-3D (id 96) está ativo mas sem estoque
    assert 96 not in {p.id for p in results}


def test_busca_ordenada_por_preco(store):
    prices = [p.final_price for p in store.search_products(category="ukulele", limit=50)]
    assert prices == sorted(prices)


def test_find_product_por_nome_parcial(store):
    [p] = store.find_product("takamine gd20")
    assert p.id == 95 and p.price == 2199


def test_promocao_ativa_aplicada(store):
    p = store.products[90]  # Crafter HT-100, 18% Desconto Exclusivo ativo
    assert p.promotion["discount_percent"] == 18
    assert p.final_price == pytest.approx(2399 * 0.82)


def test_promocao_inativa_ignorada(store):
    assert store.products[93].promotion is None  # Martin D-28: só promoções vencidas


def test_descricao_inconsistente_suprimida(store):
    p = store.products[135]  # "Music Man Bass 1X" com descrição de Fender Jazz Bass
    assert p.description is None and p.description_inconsistent
    assert "revisão" in p.to_dict(detailed=True)["descricao"]


def test_verificacao_de_cliente(store):
    order = store.orders[8]  # cliente 2: Ana Carolina, (67) 99654-7821
    assert store.verify_customer(order, "anacarol.ferreira@coldmail.com")
    assert store.verify_customer(order, "67 99654-7821")
    assert not store.verify_customer(order, "outra@pessoa.com")
    assert not store.verify_customer(order, "")


def test_order_to_dict_nao_expoe_dados_sensiveis(store):
    d = store.order_to_dict(store.orders[1], today=date(2026, 11, 3))
    assert d["status"] == "entregue"
    assert d["itens"][0]["produto"].startswith("Martin D-28")
    flat = str(d)
    assert "@" not in flat and "98432" not in flat


def test_auditoria_detecta_totais_divergentes(store):
    assert any("pedido 3" in i for i in store.issues)
