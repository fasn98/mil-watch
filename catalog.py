"""Categorias de aeronaves militares a partir do código de tipo ICAO.

Aproximado: alguns tipos têm usos múltiplos (ex.: A332 pode ser reabastecedor ou VIP;
GLF5/CL60 podem ser VIP ou inteligência). Ajuste o dicionário conforme aprender com os dados.
"""

CATEGORY_LABELS = {
    "tanker": "Reabastecedor", "aew": "Alerta aéreo/radar", "isr": "Reconhecimento/ISR",
    "maritime": "Patrulha marítima", "airlift": "Transporte pesado", "bomber": "Bombardeiro",
    "fighter": "Caça/ataque", "trainer": "Treinamento", "helo": "Helicóptero/tiltrotor",
    "vip": "Transporte executivo/VIP", "utility": "Utilitário", "other": "Outros",
}

# Peso de cada categoria no Índice de Atividade Anômala (o que costuma indicar preparação).
CATEGORY_WEIGHTS = {
    "tanker": 3.0, "aew": 3.0, "isr": 3.0, "bomber": 4.0, "airlift": 2.0, "maritime": 2.0,
    "fighter": 1.5, "vip": 1.0, "helo": 0.5, "utility": 0.5, "trainer": 0.2, "other": 0.5,
}

# Categorias "pesadas": onde mudança brusca de rota ou órbita tem significado.
HEAVY = {"tanker", "aew", "isr", "maritime", "airlift", "bomber", "vip"}
ORBIT_RELEVANT = {"tanker", "aew", "isr", "maritime", "bomber"}

_TYPES = {
    "tanker": "K35R K35E K35A KC46 K46 KC10 DC10 A332 IL78 B762 KC30",
    "aew": "E3TF E3CF E3 E767 E737 E2 E2C E2D E6 A50 KJ2K",
    "isr": "R135 C135 E8 U2 Q4 RQ4 GLHK Q9 MQ9 Q1 E11 EP3",
    "maritime": "P8 P3 P1 ATL2 CN35M",
    "airlift": "C17 C5 C5M C130 C30J A400 C27J C295 CN35 C160 IL76 Y20 Y9 Y8 AN12 AN26 C2 E390 C390 A310",
    "bomber": "B52 B1 B2 TU95 T160 TU22 H6",
    "fighter": "F16 F15 F18 F18S F35 F22 EUFI RFAL GRIF JS39 TOR F5 MIR2 SU27 SU30 SU35 SU34 MG29 J10 J11 F4 A10 AV8B E18",
    "trainer": "T38 TEX2 T6 HAWK T45 M346 PC21 PC9 PC7 TUCA L39 K8 T50 T7",
    "helo": "H60 H47 H53 H64 V22 EC35 EC45 NH90 LYNX AS32 EH10 UH1 AS65 A139 A149 H1 S61 S70",
    "vip": "B752 B737 B738 B739 GLF4 GLF5 GLF6 GLEX G280 C560 LJ35 CL60 FA7X F900 F2TH A319 A320 A321 B744 B748 E135 E145 E35L",
    "utility": "B350 BE20 PC12 DHC6 C208 BE9L DH8A DH8B BE40 C441",
}
TYPE_CATEGORY = {t: cat for cat, types in _TYPES.items() for t in types.split()}


def category_of(icao_type):
    return TYPE_CATEGORY.get((icao_type or "").strip().upper(), "other")
