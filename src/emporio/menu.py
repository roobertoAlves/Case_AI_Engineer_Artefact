"""Menu de temas do atendimento, como nos chatbots de suporte.

O menu é um atalho, não um fluxo rígido: o cliente escolhe um tema pelo número,
recebe uma pergunta de orientação e então escreve a dúvida com as próprias
palavras. O tema escolhido vai junto da primeira pergunta como contexto para o
agente. Quem preferir pode ignorar o menu e digitar direto.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Topic:
    key: str
    label: str
    follow_up: str  # pergunta que a Duda faz depois que o tema é escolhido
    examples: tuple[str, ...]


TOPICS = [
    Topic("1", "Produtos, preços e disponibilidade",
          "Qual instrumento você procura? Pode dizer modelo, tipo ou faixa de preço.",
          ("Quanto custa o Takamine GD20?", "Violões de nylon até R$ 800")),
    Topic("2", "Promoções e descontos",
          "Quer ver as promoções ativas ou saber de um produto específico?",
          ("Quais promoções estão valendo?", "A Black Friday já começou?")),
    Topic("3", "Status do meu pedido",
          "Me passa o número do pedido e o e-mail ou telefone do cadastro.",
          ("Pedido 8, anacarol.ferreira@coldmail.com",)),
    Topic("4", "Trocas, devoluções e garantia",
          "Conta o que aconteceu. Se for sobre um pedido, já manda o número e o e-mail ou telefone.",
          ("Me arrependi da compra, posso devolver?", "Meu violão veio com defeito")),
    Topic("5", "Formas de pagamento e parcelamento",
          "Qual sua dúvida sobre pagamento? Se for de um produto, diga qual.",
          ("Quanto fica o Yamaha C40 no PIX?", "Posso parcelar em 10x?")),
    Topic("6", "Entregas e frete",
          "Qual o CEP de entrega? Se já escolheu o instrumento, me diz qual também.",
          ("79800-000, para o Yamaha C40", "Quanto é o frete para Campo Grande?")),
    Topic("7", "Horário, endereço e contato",
          "O que você quer saber da loja?",
          ("Vocês estão abertos agora?", "Qual o endereço?")),
    Topic("8", "Falar com um atendente",
          "Pode me contar o motivo? Eu registro e a equipe retorna em até 24 horas úteis.",
          ("Quero fazer uma reclamação sobre meu pedido",)),
]

BY_KEY = {t.key: t for t in TOPICS}
MENU_COMMANDS = {"menu", "0", "?", "ajuda"}


def menu_text() -> str:
    lines = ["**Como posso te ajudar?** Escolha um tema pelo número ou escreva sua pergunta:", ""]
    lines += [f"{t.key}. {t.label}" for t in TOPICS]
    lines += ["", "_Digite `menu` a qualquer momento para ver as opções de novo._"]
    return "\n".join(lines)


def follow_up_text(topic: Topic) -> str:
    examples = " · ".join(f'"{e}"' for e in topic.examples)
    return f"**{topic.label}** — {topic.follow_up}\n\n_Exemplos: {examples}_"


def select_topic(user_input: str) -> Topic | None:
    """Reconhece a escolha de um tema: "3", "3.", "opção 3"."""
    text = user_input.strip().lower().removeprefix("opção").removeprefix("opcao").strip().rstrip(".)")
    return BY_KEY.get(text)


def with_topic(question: str, topic: Topic | None) -> str:
    """Anexa o tema e a pergunta de orientação, como contexto para o agente.

    A pergunta de orientação importa: o cliente costuma responder só "Dourados" a
    "Para qual cidade seria a entrega?", e sem ela o agente não entende a resposta.
    """
    if topic is None:
        return question
    return (
        f'[Tema escolhido no menu: {topic.label}. A Duda perguntou: "{topic.follow_up}"]\n'
        f"{question}"
    )
