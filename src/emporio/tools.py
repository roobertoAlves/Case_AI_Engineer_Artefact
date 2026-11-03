"""Ferramentas (function calling) disponíveis ao agente.

Cada ferramenta é uma função Python determinística sobre `Store`/`PolicyRetriever`.
As definições ficam num formato neutro (nome, descrição, JSON Schema) e cada
provedor de LLM converte para o seu formato.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from emporio.data import Store, normalize, only_digits
from emporio.policies import PolicyRetriever

HANDOFF_LOG = Path(__file__).resolve().parents[2] / ".sessions" / "handoffs.jsonl"

PIX_DISCOUNT = 0.05

# Política 5.1 (região metropolitana de Campo Grande)
METRO_FREE_SHIPPING_ABOVE = 500.0
METRO_FLAT_FEE = 35.0

# Faixas de CEP (Correios) das cidades de MS que aparecem na base de clientes.
# O manual não lista os municípios da "região metropolitana"; assume-se o município
# de Campo Grande. Fonte das faixas: tabela de faixas de CEP dos Correios.
CEP_RANGES = [
    ("Campo Grande", 79000000, 79124999, True),
    ("Corumbá", 79300000, 79329999, False),
    ("Três Lagoas", 79600000, 79649999, False),
    ("Dourados", 79800000, 79849999, False),
    ("Ponta Porã", 79900000, 79924999, False),
]
MS_CEP_RANGE = (79000000, 79999999)
OUTSIDE_METRO_OPTIONS = [
    {"modalidade": "SEDEX (Correios)", "prazo": "2 a 5 dias úteis"},
    {"modalidade": "Jadlog (.package)", "prazo": "3 a 8 dias úteis"},
    {"modalidade": "PAC (Correios)", "prazo": "5 a 12 dias úteis"},
]
# (parcelas máximas da faixa, parcela mínima) — política 3.1
INSTALLMENT_RULES = [(3, 50.0), (6, 80.0), (12, 100.0)]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON Schema
    handler: Callable[..., Any]


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


class Toolbox:
    def __init__(self, store: Store, retriever: PolicyRetriever, today: Callable[[], date] = date.today):
        self.store = store
        self.retriever = retriever
        self.today = today
        self.specs = self._build_specs()
        self.by_name = {s.name: s for s in self.specs}
        # E-mails/telefones que o cliente digitou nesta conversa, extraídos literalmente.
        # Modelos pequenos às vezes copiam o contato errado (ex.: pulam um dígito) ao
        # montar a chamada; verificar também contra o texto original evita esse ruído.
        self.user_contacts: list[str] = []

    def remember_contacts(self, user_message: str) -> None:
        self.user_contacts += re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", user_message)
        self.user_contacts += re.findall(r"\(?\d{2}\)?[\s-]?\d{4,5}-?\d{4}", user_message)

    def _verify(self, order, email_ou_telefone: str) -> bool:
        candidates = [email_ou_telefone, *self.user_contacts]
        return any(self.store.verify_customer(order, c) for c in candidates if c)

    def run(self, name: str, args: dict) -> str:
        """Executa uma ferramenta e devolve JSON (string). Erros viram JSON de erro."""
        spec = self.by_name.get(name)
        if spec is None:
            return json.dumps({"erro": f"ferramenta desconhecida: {name}"}, ensure_ascii=False)
        try:
            result = spec.handler(**(args or {}))
        except TypeError as exc:
            result = {"erro": f"argumentos inválidos para {name}: {exc}"}
        except Exception as exc:  # noqa: BLE001 — o agente precisa receber o erro, não quebrar
            result = {"erro": f"falha ao executar {name}: {exc}"}
        return json.dumps(result, ensure_ascii=False, default=str)

    # ------------------------------------------------------------ ferramentas
    def buscar_produtos(
        self,
        consulta: str | None = None,
        categoria: str | None = None,
        preco_min: float | None = None,
        preco_max: float | None = None,
        incluir_indisponiveis: bool = False,
    ) -> dict:
        found = self.store.search_products(
            query=consulta,
            category=categoria,
            min_price=preco_min,
            max_price=preco_max,
            only_available=not incluir_indisponiveis,
            limit=100,
        )
        # Poucas opções por vez: é chat. O total informa se vale a pena refinar a busca.
        shown = found[:6]
        return {
            "total_encontrado": len(found),
            "exibindo": len(shown),
            "ordenacao": "menor preço primeiro (preço final, já com promoção ativa)",
            "dica": "se total_encontrado > exibindo, informe o total e ofereça refinar (tipo de corda, elétrico, faixa de preço)",
            "produtos": [p.to_dict() for p in shown],
        }

    def detalhes_produto(self, nome: str) -> dict:
        matches = self.store.find_product(nome)
        if not matches:
            return {"encontrado": False, "mensagem": f"nenhum produto do catálogo corresponde a '{nome}'"}
        if len(matches) > 3:
            return {
                "encontrado": False,
                "mensagem": "nome ambíguo; peça ao cliente para especificar",
                "candidatos": [p.name for p in matches[:8]],
            }
        out = []
        for p in matches:
            d = p.to_dict(detailed=True)
            if not p.available:
                d["alternativas_disponiveis"] = [a.to_dict() for a in self.store.similar_available(p)]
            out.append(d)
        return {"encontrado": True, "produtos": out}

    def listar_promocoes_ativas(self) -> dict:
        promos = [p for p in self.store.products.values() if p.promotion]
        return {
            "promocoes_ativas": [p.to_dict() for p in sorted(promos, key=lambda p: p.final_price)],
            "regra": "promoções não são cumulativas; o desconto de 5% no PIX não se aplica sobre preço promocional",
        }

    def consultar_pedido(self, numero_pedido: int, email_ou_telefone: str) -> dict:
        order = self.store.orders.get(int(numero_pedido))
        # Mesma resposta para "não existe" e "não confere": não revela quais pedidos existem.
        if order is None or not self._verify(order, email_ou_telefone):
            return {
                "encontrado": False,
                "mensagem": "não foi possível localizar um pedido com esse número e esse e-mail/telefone",
            }
        return {"encontrado": True, "pedido": self.store.order_to_dict(order, self.today())}

    def verificar_troca_devolucao(self, numero_pedido: int, email_ou_telefone: str) -> dict:
        """Aplica os prazos das políticas 4.1–4.3 e 8.1 ao pedido, de forma determinística."""
        found = self.consultar_pedido(numero_pedido, email_ou_telefone)
        if not found["encontrado"]:
            return found
        order = self.store.orders[int(numero_pedido)]
        today = self.today()
        days_since_purchase = (today - order.order_date).days
        result: dict = {"pedido": found["pedido"], "data_de_hoje": today.isoformat()}

        if order.status == "cancelled":
            result["situacao"] = "pedido cancelado: não há troca ou devolução a fazer"
            return result
        if order.status in ("pending", "confirmed"):
            result["situacao"] = (
                "pedido ainda não foi enviado. O manual não trata cancelamento antes do envio: "
                "ofereça encaminhar para um atendente"
            )
            return result
        if order.status == "shipped":
            result["situacao"] = (
                "pedido em transporte, ainda não recebido. Os prazos de arrependimento e troca por "
                "preferência (7 dias) só começam a contar no recebimento; o direito continua garantido"
            )
            result["observacao"] = (
                "a previsão de entrega já passou; ofereça encaminhar para um atendente verificar o envio"
                if order.estimated_delivery and order.estimated_delivery < today else None
            )
            return result

        # Entregue. Não há data real de recebimento nos dados: usa-se a previsão de entrega.
        received = order.estimated_delivery or order.order_date
        days_since_receipt = (today - received).days
        result["recebimento_aproximado"] = received.isoformat()
        result["dias_desde_recebimento"] = days_since_receipt
        result["dias_desde_compra"] = days_since_purchase
        result["opcoes"] = {
            "arrependimento_7_dias_apos_recebimento": {
                "elegivel": days_since_receipt <= 7,
                "regra": "sem justificativa, embalagem original, sem uso; reembolso em até 10 dias úteis; frete por conta da loja",
            },
            "troca_por_preferencia_7_dias": {
                "elegivel": days_since_receipt <= 7,
                "regra": "produto em perfeito estado, sujeito a disponibilidade do novo modelo",
            },
            "troca_por_defeito_na_loja_30_dias_apos_compra": {
                "elegivel": days_since_purchase <= 30,
                "regra": "defeito de fabricação; mau uso, quedas e umidade não são cobertos",
            },
            "garantia_legal_90_dias_apos_recebimento": {
                "elegivel": days_since_receipt <= 90,
                "regra": "defeitos de fabricação (CDC)",
            },
            "garantia_do_fabricante": {
                "elegivel": "depende do fabricante (6 meses a 2 anos, ver certificado de garantia)",
                "regra": "a loja pode intermediar o acionamento mediante solicitação",
            },
        }
        result["aviso"] = (
            "datas de recebimento são aproximadas pela previsão de entrega; em caso de dúvida "
            "sobre o prazo, ofereça encaminhar para um atendente confirmar"
        )
        return result

    def consultar_politicas(self, consulta: str) -> dict:
        hits = self.retriever.search(consulta, k=3)
        if not hits:
            return {"trechos": [], "mensagem": "nenhum trecho do manual corresponde à consulta"}
        return {"trechos": [c.render() for _, c in hits]}

    def simular_pagamento(self, produto_ids: list[int], quantidades: list[int] | None = None) -> dict:
        quantidades = quantidades or [1] * len(produto_ids)
        if len(quantidades) != len(produto_ids):
            return {"erro": "produto_ids e quantidades devem ter o mesmo tamanho"}
        itens, total, pix_total = [], 0.0, 0.0
        for pid, qty in zip(produto_ids, quantidades):
            p = self.store.products.get(int(pid))
            if p is None:
                return {"erro": f"produto {pid} não existe"}
            subtotal = p.final_price * qty
            total += subtotal
            # PIX: 5% sobre preço de tabela, mas não sobre item já promocional (política 6.2)
            pix_total += subtotal if p.promotion else subtotal * (1 - PIX_DISCOUNT)
            itens.append({"produto": p.name, "quantidade": qty, "preco_unitario": p.final_price,
                          "em_promocao": bool(p.promotion), "disponivel": p.available})
        max_parcelas = 1
        for max_n, min_parcela in INSTALLMENT_RULES:
            n = min(max_n, math.floor(total / min_parcela + 1e-9))
            max_parcelas = max(max_parcelas, n)
        return {
            "itens": itens,
            "total": round(total, 2),
            "pix_a_vista": round(pix_total, 2),
            "debito_ou_boleto_a_vista": round(total, 2),
            "cartao_credito_max_parcelas_sem_juros": max_parcelas,
            "valor_parcela_no_maximo": round(total / max_parcelas, 2),
            "permite_combinar_formas_pagamento": total > 2000,
            "observacao": "boleto compensa em até 3 dias úteis; frete não incluído",
        }

    def calcular_frete(
        self,
        cep: str | None = None,
        cidade: str | None = None,
        produto_ids: list[int] | None = None,
        quantidades: list[int] | None = None,
    ) -> dict:
        """Aplica a política 5 ao destino (por CEP ou cidade) e, se informados, aos produtos."""
        destino = _resolve_destination(cep, cidade)
        if "erro" in destino:
            return destino

        produto_ids = produto_ids or []
        quantidades = quantidades or [1] * len(produto_ids)
        if len(quantidades) != len(produto_ids):
            return {"erro": "produto_ids e quantidades devem ter o mesmo tamanho"}
        itens, total, especiais = [], 0.0, []
        for pid, qty in zip(produto_ids, quantidades):
            p = self.store.products.get(int(pid))
            if p is None:
                return {"erro": f"produto {pid} não existe"}
            total += p.final_price * qty
            itens.append({"produto": p.name, "quantidade": qty})
            if _is_large_instrument(p):
                especiais.append(p.name)

        result: dict = {"destino": destino}
        if itens:
            result["itens"] = itens
            result["valor_dos_produtos"] = round(total, 2)

        if destino["regiao"] == "metropolitana de Campo Grande":
            entrega = {"modalidade": "motoboy próprio (a loja liga antes de entregar)", "prazo": "1 a 3 dias úteis"}
            if itens:
                entrega["valor_frete"] = 0.0 if total > METRO_FREE_SHIPPING_ABOVE else METRO_FLAT_FEE
            else:
                entrega["valor_frete"] = (
                    f"grátis para pedidos acima de R$ {METRO_FREE_SHIPPING_ABOVE:.2f}; "
                    f"abaixo disso, taxa fixa de R$ {METRO_FLAT_FEE:.2f}"
                )
            result["entrega"] = entrega
        else:
            result["entrega"] = {
                "modalidades": OUTSIDE_METRO_OPTIONS,
                "seguro_e_rastreamento": "incluídos em todas as modalidades",
                "valor_frete": None,
                "motivo_sem_valor": (
                    "o valor é calculado no fechamento com base no CEP, peso e dimensões do produto; "
                    "o sistema de atendimento não tem peso/dimensões nem a tabela das transportadoras"
                ),
            }
        if especiais:
            result["frete_especial"] = {
                "produtos": especiais,
                "regra": "instrumento de grande porte: pode exigir cotação individual com a equipe",
            }
        return result

    def encaminhar_para_atendente(self, motivo: str, resumo: str, contato_cliente: str | None = None) -> dict:
        HANDOFF_LOG.parent.mkdir(exist_ok=True)
        record = {"quando": datetime.now().isoformat(timespec="seconds"), "motivo": motivo,
                  "resumo": resumo, "contato_cliente": contato_cliente}
        with open(HANDOFF_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {"registrado": True, "prazo_retorno": "até 24 horas úteis"}

    # ---------------------------------------------------------- definições
    def _build_specs(self) -> list[ToolSpec]:
        categorias = ", ".join(c["name"] for c in self.store.categories.values())
        return [
            ToolSpec(
                "buscar_produtos",
                "Busca instrumentos no catálogo com filtros. Use para perguntas abertas como "
                "'quais violões até R$1000?' ou 'tem ukulele elétrico?'. Retorna preço, estoque e "
                f"promoção ativa. Categorias: {categorias}. Por padrão só retorna itens disponíveis.",
                _schema({
                    "consulta": {"type": "string", "description": "palavras-chave (ex.: 'nylon', 'elétrico', 'Yamaha', '7 cordas')"},
                    "categoria": {"type": "string", "description": "nome (ou parte) da categoria"},
                    "preco_min": {"type": "number", "description": "preço mínimo em R$"},
                    "preco_max": {"type": "number", "description": "preço máximo em R$"},
                    "incluir_indisponiveis": {"type": "boolean", "description": "incluir esgotados/descontinuados"},
                }),
                self.buscar_produtos,
            ),
            ToolSpec(
                "detalhes_produto",
                "Consulta um produto específico pelo nome (preço, estoque, status, especificações, "
                "promoção). Se estiver indisponível, já retorna alternativas disponíveis.",
                _schema({"nome": {"type": "string", "description": "nome ou modelo, ex.: 'Takamine GD20'"}}, ["nome"]),
                self.detalhes_produto,
            ),
            ToolSpec(
                "listar_promocoes_ativas",
                "Lista os produtos com promoção vigente agora. Use quando perguntarem sobre "
                "promoções, ofertas, descontos ou uma campanha específica (ex.: Black Friday).",
                _schema({}),
                self.listar_promocoes_ativas,
            ),
            ToolSpec(
                "consultar_pedido",
                "Consulta status, itens, rastreio e previsão de entrega de um pedido. Exige o número "
                "do pedido E o e-mail ou telefone do cadastro (verificação de identidade, LGPD). "
                "Nunca chame sem os dois dados informados pelo cliente.",
                _schema({
                    "numero_pedido": {"type": "integer"},
                    "email_ou_telefone": {"type": "string", "description": "e-mail ou telefone informado pelo cliente"},
                }, ["numero_pedido", "email_ou_telefone"]),
                self.consultar_pedido,
            ),
            ToolSpec(
                "verificar_troca_devolucao",
                "Para pedidos de devolução, arrependimento, troca ou garantia de um pedido: verifica "
                "o pedido e calcula, com a data de hoje, quais opções da política ainda estão no "
                "prazo. Exige número do pedido E e-mail ou telefone do cadastro.",
                _schema({
                    "numero_pedido": {"type": "integer"},
                    "email_ou_telefone": {"type": "string"},
                }, ["numero_pedido", "email_ou_telefone"]),
                self.verificar_troca_devolucao,
            ),
            ToolSpec(
                "consultar_politicas",
                "Busca no manual oficial da loja: horários, endereço/contato, formas de pagamento e "
                "parcelamento, trocas e devoluções, arrependimento, frete e entregas, rastreamento, "
                "regras de promoções, garantia e privacidade (LGPD). Use sempre que a resposta "
                "depender de uma regra da loja.",
                _schema({"consulta": {"type": "string", "description": "a dúvida em linguagem natural"}}, ["consulta"]),
                self.consultar_politicas,
            ),
            ToolSpec(
                "simular_pagamento",
                "Calcula valores exatos de pagamento para um ou mais produtos (pelos ids retornados "
                "nas buscas): total, valor no PIX (5% de desconto quando aplicável), parcelamento "
                "máximo sem juros e valor da parcela. Use em vez de fazer contas de cabeça.",
                _schema({
                    "produto_ids": {"type": "array", "items": {"type": "integer"}},
                    "quantidades": {"type": "array", "items": {"type": "integer"}, "description": "opcional; padrão 1 de cada"},
                }, ["produto_ids"]),
                self.simular_pagamento,
            ),
            ToolSpec(
                "calcular_frete",
                "Calcula frete e prazo de entrega pela política da loja a partir do CEP (ou da "
                "cidade) de destino e, opcionalmente, dos produtos (ids das buscas). Região "
                "metropolitana de Campo Grande: valor exato. Outras cidades: modalidades e prazos "
                "(o valor depende de peso/dimensões e é informado no fechamento).",
                _schema({
                    "cep": {"type": "string", "description": "CEP de destino, ex.: '79800-000'"},
                    "cidade": {"type": "string", "description": "use se o cliente não souber o CEP"},
                    "produto_ids": {"type": "array", "items": {"type": "integer"}},
                    "quantidades": {"type": "array", "items": {"type": "integer"}},
                }),
                self.calcular_frete,
            ),
            ToolSpec(
                "encaminhar_para_atendente",
                "Registra uma reclamação ou caso que precisa de um atendente humano (ex.: produto com "
                "defeito, devolução a ser processada, dúvida que o manual não cobre, cliente insatisfeito).",
                _schema({
                    "motivo": {"type": "string", "enum": ["reclamacao", "devolucao_ou_troca", "duvida_nao_coberta", "outro"]},
                    "resumo": {"type": "string", "description": "resumo objetivo do caso"},
                    "contato_cliente": {"type": "string", "description": "contato informado pelo cliente, se houver"},
                }, ["motivo", "resumo"]),
                self.encaminhar_para_atendente,
            ),
        ]


def _resolve_destination(cep: str | None, cidade: str | None) -> dict:
    """Identifica a cidade e a região de entrega a partir do CEP ou do nome da cidade."""
    if cep:
        digits = only_digits(cep)
        if len(digits) != 8:
            return {"erro": f"CEP inválido: '{cep}'. Peça ao cliente os 8 dígitos (ex.: 79800-000)"}
        number = int(digits)
        city = next((c for c in CEP_RANGES if c[1] <= number <= c[2]), None)
        in_ms = MS_CEP_RANGE[0] <= number <= MS_CEP_RANGE[1]
        dest = {"cep": f"{digits[:5]}-{digits[5:]}", "cidade": city[0] if city else None,
                "uf": "MS" if in_ms else "fora de MS"}
        is_metro = bool(city and city[3])
    elif cidade:
        city = next((c for c in CEP_RANGES if normalize(c[0]) == normalize(cidade)), None)
        dest = {"cidade": city[0] if city else cidade, "uf": "MS" if city else "não identificada"}
        is_metro = bool(city and city[3])
    else:
        return {"erro": "informe o CEP ou a cidade de entrega"}
    dest["regiao"] = "metropolitana de Campo Grande" if is_metro else "outras cidades"
    return dest


def _is_large_instrument(p) -> bool:
    """Política 5.2: baterias acústicas, pianos digitais e contrabaixos podem ter frete especial."""
    name = normalize(p.name)
    return (
        (p.category == "Baterias e Percussão" and "acustica" in name)
        or "piano" in name
        or (p.category == "Cordas Orquestrais" and "contrabaixo" in name)
    )
