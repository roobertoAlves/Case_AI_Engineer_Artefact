"""Camada de dados: carrega os CSVs da loja, trata inconsistências e expõe consultas.

Os CSVs são pequenos (dezenas de linhas), então tudo é carregado em memória na
inicialização. As consultas são funções Python determinísticas, chamadas pelo
agente via function calling — o LLM nunca "inventa" preço, estoque ou status.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
CSV_PREFIX = "desafio_tecnico_ai_eng - "

# Marcas conhecidas no catálogo; usadas para detectar descrições que citam outra marca.
KNOWN_BRANDS = [
    "yamaha", "tagima", "giannini", "rozini", "shelby", "crafter", "martin", "taylor",
    "takamine", "kala", "ohana", "kalani", "fender", "gibson", "ibanez", "prs",
    "music man", "pearl", "tama", "korg", "nord", "roland",
]

STATUS_PEDIDO = {
    "pending": "aguardando confirmação de pagamento",
    "confirmed": "pagamento confirmado, em separação",
    "shipped": "enviado (em transporte)",
    "delivered": "entregue",
    "cancelled": "cancelado",
}

STATUS_PRODUTO = {
    "active": "ativo",
    "discontinued": "descontinuado",
    "coming_soon": "lançamento em breve (ainda não disponível para venda)",
}

FORMAS_PAGAMENTO = {
    "pix": "PIX",
    "debit": "cartão de débito",
    "boleto": "boleto bancário",
}


def normalize(text: str) -> str:
    """Minúsculas e sem acentos, para buscas tolerantes ("violao" == "Violão")."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower().strip()


def only_digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def _brands_in(text: str) -> set[str]:
    norm = normalize(text)
    return {b for b in KNOWN_BRANDS if re.search(rf"\b{re.escape(b)}\b", norm)}


def _payment_label(code: str) -> str:
    if code in FORMAS_PAGAMENTO:
        return FORMAS_PAGAMENTO[code]
    m = re.fullmatch(r"credit_(\d+)x", code or "")
    if m:
        return f"cartão de crédito em {m.group(1)}x"
    return code


@dataclass
class Product:
    id: int
    name: str
    category_id: int
    category: str
    price: float
    stock: int
    status: str
    specs: dict
    description: str | None
    created_at: str
    description_inconsistent: bool = False
    promotion: dict | None = None  # promoção ATIVA, se houver

    @property
    def available(self) -> bool:
        return self.status == "active" and self.stock > 0

    @property
    def final_price(self) -> float:
        if self.promotion:
            return round(self.price * (1 - self.promotion["discount_percent"] / 100), 2)
        return self.price

    def to_dict(self, detailed: bool = False) -> dict:
        d = {
            "id": self.id,
            "nome": self.name,
            "categoria": self.category,
            "preco_tabela": self.price,
            "status": STATUS_PRODUTO.get(self.status, self.status),
            "disponivel": self.available,
            "estoque": self.stock,
        }
        if self.promotion:
            d["promocao_ativa"] = {
                "nome": self.promotion["description"],
                "desconto_percentual": self.promotion["discount_percent"],
                "preco_promocional": self.final_price,
            }
        if detailed:
            d["especificacoes"] = self.specs
            d["descricao"] = self.description or (
                "descrição indisponível (cadastro em revisão) — informe apenas nome, preço e especificações"
            )
        return d


@dataclass
class Order:
    id: int
    customer_id: int
    order_date: date
    status: str
    total: float
    payment_method: str
    tracking_code: str | None
    estimated_delivery: date | None
    notes: str | None
    items: list[tuple[int, int]] = field(default_factory=list)  # (product_id, quantidade)


@dataclass
class Customer:
    id: int
    name: str
    phone: str
    email: str
    city: str


def _read_csv(name: str, data_dir: Path) -> list[dict]:
    with open(data_dir / f"{CSV_PREFIX}{name}.csv", encoding="utf-8", newline="") as f:
        return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]


def _parse_date(value: str) -> date | None:
    return date.fromisoformat(value) if value else None


class Store:
    """Repositório em memória com os dados operacionais da loja."""

    def __init__(self, data_dir: Path = DATA_DIR):
        self.data_dir = data_dir
        self.issues: list[str] = []  # inconsistências encontradas no tratamento
        self.categories = {
            int(r["category_id"]): r for r in _read_csv("categories", data_dir)
        }
        self.products = self._load_products()
        self._attach_promotions()
        self.customers = {
            int(r["customer_id"]): Customer(
                int(r["customer_id"]), r["name"], r["phone"], r["email"].lower(), r["city"]
            )
            for r in _read_csv("customers", data_dir)
        }
        self.orders = self._load_orders()

    # ------------------------------------------------------------------ carga
    def _load_products(self) -> dict[int, Product]:
        products: dict[int, Product] = {}
        for r in _read_csv("products", data_dir=self.data_dir):
            pid = int(r["product_id"])
            try:
                specs = json.loads(r["specs"]) if r["specs"] else {}
            except json.JSONDecodeError:
                specs = {}
                self.issues.append(f"produto {pid}: specs com JSON inválido")
            description = r["description"] or None
            inconsistent = False
            # Alguns cadastros têm descrição de OUTRO produto (ex.: "Music Man Bass 1X"
            # descrito como "Fender Jazz Bass"). Para não passar informação errada ao
            # cliente, a descrição é suprimida quando cita uma marca diferente do nome.
            name_brands, desc_brands = _brands_in(r["name"]), _brands_in(description or "")
            if desc_brands - name_brands:
                inconsistent = True
                self.issues.append(
                    f"produto {pid} ({r['name']}): descrição cita {sorted(desc_brands - name_brands)}"
                    " — descrição suprimida"
                )
                description = None
            products[pid] = Product(
                id=pid,
                name=r["name"],
                category_id=int(r["category_id"]),
                category=self.categories[int(r["category_id"])]["name"],
                price=float(r["price_brl"]),
                stock=int(r["stock_quantity"] or 0),
                status=r["status"],
                specs=specs,
                description=description,
                created_at=r["created_at"],
                description_inconsistent=inconsistent,
            )
            if r["status"] == "active" and int(r["stock_quantity"] or 0) == 0:
                self.issues.append(f"produto {pid} ({r['name']}): ativo porém sem estoque")
        return products

    def _attach_promotions(self) -> None:
        for r in _read_csv("promotions", self.data_dir):
            pid = int(r["product_id"])
            if r["is_active"] != "1":
                continue
            if pid not in self.products:
                self.issues.append(f"promoção {r['promotion_id']}: produto {pid} inexistente")
                continue
            promo = {
                "promotion_id": int(r["promotion_id"]),
                "discount_percent": float(r["discount_percent"]),
                "description": r["description"],
            }
            current = self.products[pid].promotion
            # Promoções não são cumulativas (política 6.2): fica a de maior desconto.
            if current is None or promo["discount_percent"] > current["discount_percent"]:
                if current is not None:
                    self.issues.append(f"produto {pid}: mais de uma promoção ativa")
                self.products[pid].promotion = promo

    def _load_orders(self) -> dict[int, Order]:
        orders = {
            int(r["order_id"]): Order(
                id=int(r["order_id"]),
                customer_id=int(r["customer_id"]),
                order_date=_parse_date(r["order_date"]),
                status=r["status"],
                total=float(r["total_brl"]),
                payment_method=r["payment_method"],
                tracking_code=r["tracking_code"] or None,
                estimated_delivery=_parse_date(r["estimated_delivery"]),
                notes=r["notes"] or None,
            )
            for r in _read_csv("orders", self.data_dir)
        }
        for r in _read_csv("order_items", self.data_dir):
            oid, pid = int(r["order_id"]), int(r["product_id"])
            if oid not in orders:
                self.issues.append(f"item de pedido aponta para pedido inexistente {oid}")
                continue
            orders[oid].items.append((pid, int(r["quantity"])))
        for o in orders.values():
            computed = sum(self.products[p].price * q for p, q in o.items if p in self.products)
            if abs(computed - o.total) > 0.01:
                self.issues.append(
                    f"pedido {o.id}: total registrado R$ {o.total:.2f} difere da soma dos itens "
                    f"a preço de tabela atual (R$ {computed:.2f})"
                )
        return orders

    # -------------------------------------------------------------- consultas
    def search_products(
        self,
        query: str | None = None,
        category: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        only_available: bool = True,
        limit: int = 10,
    ) -> list[Product]:
        """Busca por palavras-chave (todas devem aparecer em nome/categoria/specs).

        A descrição fica de fora de propósito: textos como "uma ponte entre o
        ukulele e o violão" fariam um ukulele aparecer numa busca por violões.
        """
        terms = _words(query)
        cat_terms = _words(category)
        results = []
        for p in self.products.values():
            haystack = normalize(
                " ".join([p.name, p.category, " ".join(map(str, p.specs.values()))])
            )
            if cat_terms and not all(_term_match(t, normalize(p.category)) for t in cat_terms):
                continue
            if terms and not all(_term_match(t, haystack) for t in terms):
                continue
            if min_price is not None and p.final_price < min_price:
                continue
            if max_price is not None and p.final_price > max_price:
                continue
            if only_available and not p.available:
                continue
            results.append(p)
        results.sort(key=lambda p: p.final_price)
        return results[:limit]

    def find_product(self, name: str) -> list[Product]:
        """Encontra produtos pelo nome, tolerando acentos e palavras parciais.

        Retorna correspondências ordenadas por quantidade de termos do nome
        consultado presentes no nome do produto (melhor primeiro).
        """
        terms = _words(name)
        scored = []
        for p in self.products.values():
            pname = normalize(p.name)
            hits = sum(1 for t in terms if _term_match(t, pname))
            if hits:
                scored.append((hits, p))
        if not scored:
            return []
        best = max(h for h, _ in scored)
        return [p for h, p in sorted(scored, key=lambda x: -x[0]) if h == best]

    def similar_available(self, product: Product, limit: int = 3) -> list[Product]:
        """Alternativas disponíveis da mesma categoria, com preço mais próximo."""
        candidates = [
            p for p in self.products.values()
            if p.category_id == product.category_id and p.available and p.id != product.id
        ]
        candidates.sort(key=lambda p: abs(p.final_price - product.final_price))
        return candidates[:limit]

    def verify_customer(self, order: Order, email_or_phone: str) -> bool:
        """Confere se o contato informado pertence ao dono do pedido (LGPD)."""
        customer = self.customers.get(order.customer_id)
        if not customer or not email_or_phone:
            return False
        given = email_or_phone.strip().lower()
        if "@" in given:
            return given == customer.email
        digits = only_digits(given)
        return len(digits) >= 8 and only_digits(customer.phone).endswith(digits[-8:])

    def order_to_dict(self, order: Order, today: date) -> dict:
        customer = self.customers.get(order.customer_id)
        items = []
        for pid, qty in order.items:
            p = self.products.get(pid)
            items.append({"produto": p.name if p else f"produto {pid}", "quantidade": qty})
        d = {
            "pedido": order.id,
            "cliente_primeiro_nome": customer.name.split()[0] if customer else None,
            "cidade_entrega": customer.city if customer else None,
            "data_pedido": order.order_date.isoformat(),
            "status": STATUS_PEDIDO.get(order.status, order.status),
            "itens": items,
            "total_pago": order.total,
            "forma_pagamento": _payment_label(order.payment_method),
            "dias_desde_o_pedido": (today - order.order_date).days,
        }
        if order.tracking_code:
            d["codigo_rastreamento"] = order.tracking_code
        if order.estimated_delivery:
            d["previsao_entrega"] = order.estimated_delivery.isoformat()
            if order.status == "delivered":
                # Não há data real de entrega nos dados; usamos a previsão como aproximação.
                d["dias_desde_entrega_aprox"] = (today - order.estimated_delivery).days
        if order.notes:
            d["observacao"] = order.notes
        return d


_SEARCH_STOPWORDS = {"de", "da", "do", "das", "dos", "e", "com", "para", "em"}


def _words(text: str | None) -> list[str]:
    return [w for w in re.findall(r"\w+", normalize(text or "")) if w not in _SEARCH_STOPWORDS]


def _stem(word: str) -> str:
    """Stemmer mínimo de plural em português: violões/violão -> viol, teclados -> teclado."""
    for suffix in ("oes", "aes", "ao"):
        if word.endswith(suffix) and len(word) > 4:
            return word[: -len(suffix)]
    if word.endswith("es") and len(word) > 5:
        return word[:-2]
    if word.endswith("s") and len(word) > 3:
        return word[:-1]
    return word


def _term_match(term: str, haystack: str) -> bool:
    """Casa o termo (radical) como prefixo de alguma palavra do texto."""
    stem = _stem(term)
    return any(_stem(w).startswith(stem) for w in re.findall(r"\w+", haystack))


if __name__ == "__main__":
    store = Store()
    print(f"{len(store.products)} produtos, {len(store.orders)} pedidos, {len(store.customers)} clientes")
    print("Inconsistências encontradas no tratamento dos dados:")
    for issue in store.issues:
        print(" -", issue)
