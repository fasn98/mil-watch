# mil-watch v2

Monitor global de aeronaves militares em dados públicos de ADS-B, com foco em análise sóbria: o que está voando, onde, se algo saiu do padrão e para onde provavelmente vai.

Só biblioteca padrão do Python 3.9+. Dois serviços: o **coletor** e o **painel web**.

## O que faz

| Função | Como |
|---|---|
| Coleta global | Endpoint `/v2/mil` do adsb.lol (reserva: airplanes.live), a cada 90 s |
| Regiões | 23 regiões aproximadas (Mar Negro, Oriente Médio, Mar do Sul da China, Brasil…) |
| País / OTAN | Pela faixa ICAO do transponder |
| Categoria | Pelo tipo: reabastecedor, radar, ISR, patrulha, transporte, bombardeiro, caça… |
| Eventos | Mudança brusca de rota (≥60°, acima de 15.000 ft, só aeronaves grandes), órbita (reabastecedor, radar e ISR "em estação"), código de emergência, descida rápida, sinal perdido em região sensível |
| Previsão | Projeção de 15/30/60 min e destino provável (bases na direção do voo + pousos aprendidos daquele avião e callsign) |
| Índice de Atividade Anômala | Aeronave-horas ponderadas das últimas 24 h vs. as mesmas janelas dos últimos 28 dias, por região. Ativa após 14 dias de coleta |
| Alertas Telegram | Entrada no Brasil (como na v1), emergências, anomalia regional, resumo diário às 8h |
| Ormuz (AIS) | Travessias diárias do Estreito de Ormuz e navios no Golfo, via aisstream.io (chave grátis). Linha no resumo diário |
| Painel | Abas Agora (mapa ao vivo e previsão), Regiões (índice), Eventos, Histórico e Ormuz, protegidas por senha |

## Instalar / atualizar na VPS

```bash
curl -fsSL https://raw.githubusercontent.com/fasn98/mil-watch/main/install.sh -o install.sh && sudo bash install.sh
```

Mantém `.env` e histórico, faz backup do banco antes e migra o banco da v1 automaticamente. Para remover: `bash install.sh --uninstall`.

## Configuração (`/opt/mil-watch-br/.env`)

Veja `.env.example`. As principais:

- `BRAZIL_ALERT_SCOPE`: quem dispara o alerta de entrada no Brasil (`US`, `NATO`, `FOREIGN`, `ALL`).
- `ALERT_EVENTS`: quais eventos vão para o Telegram. O padrão é só `emergencia,anomalia`, para não virar spam. Órbitas e mudanças de rota ficam no painel.
- `ALERT_IGNORE_REGIONS`: regiões cujos eventos ficam só no painel (padrão: `América do Norte,Alasca`, por causa dos treinos domésticos dos EUA).
- `AISSTREAM_API_KEY`: liga o módulo de Ormuz. Crie a chave em https://aisstream.io (login com GitHub), depois `systemctl restart mil-watch-br-ais`.
- `ALERT_EVENT_REGIONS`: ex. `Mar Negro,Oriente Médio e Golfo` para receber eventos só dessas regiões.
- `RAW_RETENTION_DAYS`: pontos brutos ocupam ~40 MB/dia; depois do prazo fica uma rota resumida por passagem.

Depois de editar: `systemctl restart mil-watch-br mil-watch-br-dashboard`.

## Limitações (importante)

- **Operações reais voam com transponder desligado.** O que aparece é sobretudo apoio: reabastecimento, transporte, radar e reconhecimento.
- **Visão desequilibrada:** aeronaves da OTAN transmitem muito mais que as da Rússia e da China.
- **Exercício e preparação parecem iguais nos dados.** O índice mede atividade fora do padrão, não intenção, e não deve ser apresentado como "probabilidade de ataque".
- Cobertura de receptores incompleta (oceanos, Amazônia, África); "sinal perdido" muitas vezes é só falta de receptor.
- Regiões, faixas de país, categorias e bases são aproximadas. Tipos ambíguos (A332, GLF5, CL60) podem cair na categoria errada; ajuste em `catalog.py`.
- AIS: depende de receptores em terra na região; muitos navios desligam o AIS em zona de conflito e navios de guerra quase nunca transmitem. Use as travessias como tendência.
- O painel usa HTTP com senha, sem HTTPS. Não reaproveite a senha.

## Desenvolvimento

```bash
python3 -m unittest -v          # 36 testes, offline (o de AIS usa o pacote websockets)
python3 build_installer.py      # gera install.sh com os módulos embutidos
```

Arquivos: `geo.py` (regiões, países, bases), `catalog.py` (categorias), `store.py` (banco e migração), `analysis.py` (órbita, rota, previsão, índice), `mil_watch.py` (coletor), `dashboard.py` (painel), `ais_watch.py` (navios em Ormuz).
