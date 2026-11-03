import pytest

from emporio import cli
from emporio.llm import Turn
from emporio.menu import BY_KEY, select_topic, with_topic
from emporio.sessions import SessionStore


@pytest.mark.parametrize("text, key", [("3", "3"), (" 3. ", "3"), ("opção 4", "4"), ("8)", "8")])
def test_select_topic(text, key):
    assert select_topic(text).key == key


@pytest.mark.parametrize("text", ["9", "quanto custa?", "", "10"])
def test_select_topic_invalido(text):
    assert select_topic(text) is None


def test_with_topic():
    assert with_topic("Oi", None) == "Oi"
    msg = with_topic("Pedido 8", BY_KEY["3"])
    assert msg.startswith("[Tema escolhido no menu: Status do meu pedido. A Duda perguntou:")
    assert BY_KEY["3"].follow_up in msg
    assert msg.endswith("]\nPedido 8")


class RecordingProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self):
        self.history, self.received = [], []

    def add_user_message(self, text):
        self.received.append(text)
        self.history.append({"role": "user", "content": text})

    def step(self, system, context, tools):
        self.history.append({"role": "assistant", "content": "ok"})
        return Turn("ok")

    def add_tool_results(self, results):
        pass


def run_cli(monkeypatch, tmp_path, inputs):
    provider = RecordingProvider()
    answers = iter(inputs)
    monkeypatch.setattr(cli, "make_provider", lambda model=None: provider)
    monkeypatch.setattr(cli, "SessionStore", lambda: SessionStore(tmp_path / "c.db"))
    monkeypatch.setattr(cli.Console, "input", lambda self, prompt="": next(answers))
    assert cli.main([]) == 0
    return provider.received


def test_fluxo_menu_tema_e_pergunta(monkeypatch, tmp_path):
    received = run_cli(monkeypatch, tmp_path, ["3", "Pedido 8, anacarol.ferreira@coldmail.com", "sair"])
    assert received == [with_topic("Pedido 8, anacarol.ferreira@coldmail.com", BY_KEY["3"])]


def test_pergunta_direta_sem_menu(monkeypatch, tmp_path):
    assert run_cli(monkeypatch, tmp_path, ["Qual o endereço?", "sair"]) == ["Qual o endereço?"]


def test_numero_depois_de_resposta_nao_e_tema(monkeypatch, tmp_path):
    # depois que a Duda respondeu, "2" é resposta à conversa, não escolha do menu
    received = run_cli(monkeypatch, tmp_path, ["Quero ukuleles", "2", "sair"])
    assert received == ["Quero ukuleles", "2"]


def test_comando_menu_reativa_escolha(monkeypatch, tmp_path):
    received = run_cli(monkeypatch, tmp_path, ["Oi", "menu", "7", "Abre sábado?", "sair"])
    assert received[-1] == with_topic("Abre sábado?", BY_KEY["7"])
