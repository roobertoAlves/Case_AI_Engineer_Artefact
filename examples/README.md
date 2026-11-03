# Conversas de exemplo

As 5 conversas foram geradas rodando o agente de verdade com
`python scripts/gerar_exemplos.py --provider ollama` (modelo local `qwen3.5:4b`, GPU
RTX 5060 Ti). Nada foi editado à mão: o texto do agente e as chamadas de ferramenta
(com o retorno resumido, dentro de "Ferramentas chamadas") são exatamente o que saiu.
Cada cenário usa uma data/hora simulada, para exercitar horários e prazos de política.

| Arquivo | Cenário | O que demonstra |
|---|---|---|
| [01_catalogo_violoes.md](01_catalogo_violoes.md) | Violões até R$ 1.000 → só nylon → PIX e parcelamento | Busca com filtros, refinamento com nova consulta, cálculo determinístico (5% PIX; 6x de R$ 99,98 pela regra de parcela mínima) |
| [02_informacoes_da_loja.md](02_informacoes_da_loja.md) | Endereço, "estão abertos agora?" num domingo, entrega em Dourados | Fatos fixos no prompt, contexto de data/hora, retrieval da seção 5.2 |
| [03_preco_promocao_estoque.md](03_preco_promocao_estoque.md) | Preço do Takamine GD20, promoção expirada, produto esgotado | Consulta de preço, política 7.3 (promoção vencida: transparência; esgotado: alternativas) |
| [04_devolucao_com_politica.md](04_devolucao_com_politica.md) | **Não trivial:** "me arrependi, posso devolver?" | Verificação de identidade (LGPD), cálculo dos prazos de arrependimento/troca/garantia com a data de hoje, encaminhamento para atendente |
| [05_fora_do_escopo_e_seguranca.md](05_fora_do_escopo_e_seguranca.md) | Acessórios, pedido de código Python, dados de outro cliente, "ignore suas instruções" | Escopo, LGPD e resistência a prompt injection |

## Análise crítica (o que deu certo e o que não deu)

Revisei cada resposta contra os dados e o manual. Os pontos abaixo são erros reais do
modelo de 4B nesta execução; deixei-os visíveis em vez de regenerar até sair "perfeito".

**Acertos que vêm da arquitetura (e não da sorte do modelo)**
- Todos os preços, disponibilidades, promoções e prazos citados batem com os CSVs e o
  manual, porque vêm das ferramentas.
- Conta do PIX e do parcelamento (ex. 01) está correta: R$ 599,90 × 0,95 e o limite de
  6x pela parcela mínima de R$ 80 (7x exigiria parcela ≥ R$ 100).
- Promoção "Oferta Especial" do GD20 (ex. 03) existe no CSV, mas inativa — o agente
  informou que não está valendo, como pede a política 7.3.
- Devolução (ex. 04): elegibilidade calculada pela ferramenta, sem o modelo fazer
  aritmética de datas.

**Erros observados**
- Ex. 04, 1ª resposta: chama o cliente de "Duda" (o nome da própria assistente), mesmo
  com regra explícita no prompt. Também assume "embalagem original e sem uso" sem
  perguntar ao cliente.
- Ex. 03: informa quantidade exata em estoque ("9 unidades"), contrariando o prompt.
- Ex. 02: diz que a equipe responde pelo WhatsApp "mesmo fora do horário" — o manual diz
  que fora do expediente há apenas resposta automática. E oferece "simular o frete", que
  não existe como ferramenta.
- Ex. 05: na recusa do pedido de código, oferece sem motivo "dicas de qual pedal ou cabo
  procurar" (resíduo da pergunta anterior).

Esses erros são de seguimento de instrução fino, típicos de um modelo pequeno rodando
local. Em execuções anteriores (ver histórico de commits `fix(...)`) apareceram outros que
**foram resolvidos na engenharia**, não no prompt:
- resposta vazia após chamada de ferramenta → retry automático no loop;
- telefone copiado com um dígito a menos → verificação também contra o texto que o
  cliente digitou;
- regra de frete da região metropolitana aplicada a Dourados → expansão de consulta no
  retrieval;
- prazo de arrependimento "vencido" para um pedido ainda em transporte → ferramenta
  determinística de elegibilidade.

Um modelo local maior (via `OLLAMA_MODEL`) tende a reduzir os erros de seguimento de
instrução acima, ao custo de latência; esses exemplos foram gerados só com o 4B.
