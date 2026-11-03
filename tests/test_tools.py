import json
from datetime import date, datetime

import pytest

from emporio.agent import Agent
from emporio.data import Store
from emporio.llm import ToolCall, Turn
from emporio.policies import get_retriever
from emporio.prompts import TZ, store_is_open
from emporio.tools import Toolbox


@pytest.fixture(scope="module")
def store():
    return Store()


def toolbox(store, today=date(2026, 11, 3)):
    return Toolbox(store, get_retriever(), today=lambda: today)


def run(tb, name, **args):
    return json.loads(tb.run(name, args))


def test_simular_pagamento_pix_sem_promocao(store):
    out = run(toolbox(store), "simular_pagamento", produto_ids=[95])  # Takamine GD20, R$ 2199
    assert out["pix_a_vista"] == pytest.approx(2199 * 0.95)
    assert out["cartao_credito_max_parcelas_sem_juros"] == 12
    assert out["permite_combinar_formas_pagamento"] is True


def test_simular_pagamento_promocao_nao_acumula_pix(store):
    out = run(toolbox(store), "simular_pagamento", produto_ids=[90])  # 18% off ativo
    assert out["pix_a_vista"] == out["total"] == pytest.approx(2399 * 0.82)


@pytest.mark.parametrize(
    "pid, parcelas",
    [
        (119, 3),  # R$ 159,90: até 3x (parcela >= R$ 50)
        (127, 4),  # R$ 323,10 (promo 10%): 4x de 80,77 (parcela >= R$ 80)
        (83, 5),  # R$ 429,90: 5x de 85,98
        (81, 6),  # R$ 599,90: 6x de 99,98 (7x exigiria parcela >= R$ 100)
        (82, 8),  # R$ 849,00: 8x de 106,12
    ],
)
def test_regras_de_parcelamento(store, pid, parcelas):
    out = run(toolbox(store), "simular_pagamento", produto_ids=[pid])
    assert out["cartao_credito_max_parcelas_sem_juros"] == parcelas


def test_consultar_pedido_exige_contato_correto(store):
    tb = toolbox(store)
    assert run(tb, "consultar_pedido", numero_pedido=8, email_ou_telefone="x@y.com")["encontrado"] is False
    assert run(tb, "consultar_pedido", numero_pedido=999, email_ou_telefone="x@y.com")["encontrado"] is False
    ok = run(tb, "consultar_pedido", numero_pedido=8, email_ou_telefone="(67) 99654-7821")
    assert ok["pedido"]["status"] == "enviado (em transporte)"


def test_devolucao_pedido_em_transporte(store):
    out = run(toolbox(store), "verificar_troca_devolucao", numero_pedido=8,
              email_ou_telefone="anacarol.ferreira@coldmail.com")
    assert "só começam a contar no recebimento" in out["situacao"]


def test_devolucao_dentro_do_prazo(store):
    # pedido 7: comprado 03/02/2026, entrega prevista 17/02/2026
    tb = toolbox(store, today=date(2026, 2, 20))
    out = run(tb, "verificar_troca_devolucao", numero_pedido=7, email_ou_telefone="leticia.rocha@jmail.com")
    assert out["opcoes"]["arrependimento_7_dias_apos_recebimento"]["elegivel"] is True
    assert out["opcoes"]["troca_por_defeito_na_loja_30_dias_apos_compra"]["elegivel"] is True


def test_devolucao_fora_do_prazo(store):
    out = run(toolbox(store), "verificar_troca_devolucao", numero_pedido=7,
              email_ou_telefone="leticia.rocha@jmail.com")
    assert out["opcoes"]["arrependimento_7_dias_apos_recebimento"]["elegivel"] is False
    assert out["opcoes"]["garantia_legal_90_dias_apos_recebimento"]["elegivel"] is False


def test_produto_esgotado_traz_alternativas(store):
    out = run(toolbox(store), "detalhes_produto", nome="Giannini GF-3D")
    [p] = out["produtos"]
    assert p["disponivel"] is False and p["alternativas_disponiveis"]


def test_ferramenta_com_argumento_invalido_nao_quebra(store):
    out = run(toolbox(store), "detalhes_produto", nome_errado="x")
    assert "erro" in out


def test_horario_de_funcionamento():
    assert store_is_open(datetime(2026, 11, 3, 10, 0, tzinfo=TZ))  # terça
    assert not store_is_open(datetime(2026, 11, 7, 14, 0, tzinfo=TZ))  # sábado à tarde
    assert not store_is_open(datetime(2026, 11, 8, 10, 0, tzinfo=TZ))  # domingo


class FakeProvider:
    """Provedor roteirizado: primeiro pede uma ferramenta, depois responde."""

    name = "fake"

    def __init__(self):
        self.history, self.calls = [], 0

    def add_user_message(self, text):
        self.history.append({"role": "user", "content": text})

    def step(self, system, context, tools):
        self.calls += 1
        if self.calls == 1:
            return Turn("", [ToolCall("1", "detalhes_produto", {"nome": "Takamine GD20"})])
        return Turn("O Takamine GD20 custa R$ 2.199,00.")

    def add_tool_results(self, results):
        self.history.append({"role": "tool", "content": results[0][1]})


def test_loop_do_agente_executa_ferramenta_e_responde():
    reply = Agent(provider=FakeProvider()).reply("Quanto custa o Takamine GD20?")
    assert reply.text.startswith("O Takamine")
    assert reply.tool_calls[0].name == "detalhes_produto"
    assert '"preco_tabela": 2199.0' in reply.tool_calls[0].output


class EmptyThenAnswerProvider(FakeProvider):
    def step(self, system, context, tools):
        self.calls += 1
        text = "" if self.calls == 1 else "Resposta final."
        self.history.append({"role": "assistant", "content": text})
        return Turn(text)


def test_agente_tenta_de_novo_quando_modelo_responde_vazio():
    provider = EmptyThenAnswerProvider()
    reply = Agent(provider=provider).reply("Oi")
    assert reply.text == "Resposta final."
    assert provider.calls == 2
    assert [m["content"] for m in provider.history] == ["Oi", "Resposta final."]


def test_verificacao_usa_contato_digitado_pelo_cliente(store):
    tb = toolbox(store)
    tb.remember_contacts("Pedido 7, meu telefone é (67) 99789-0123")
    # o modelo copiou o telefone com um dígito a menos
    out = run(tb, "consultar_pedido", numero_pedido=7, email_ou_telefone="(67) 9978-0123")
    assert out["encontrado"] is True


def test_contato_digitado_nao_libera_pedido_de_outra_pessoa(store):
    tb = toolbox(store)
    tb.remember_contacts("meu telefone é (67) 99789-0123")  # cliente do pedido 7
    out = run(tb, "consultar_pedido", numero_pedido=3, email_ou_telefone="(67) 99789-0123")
    assert out["encontrado"] is False
