import requests, json, unicodedata, re

API = "http://localhost:8000"
KEY = "cde2025"
H   = {"x-api-key": KEY}

r = requests.get(f"{API}/dre/exames-stats",
    params={"data_inicio":"2026-01-01","data_fim":"2026-07-01","regime":"atend"},
    headers=H)
data = r.json()

def normalize(s):
    s = (s or "").upper()
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")

GROUPS = [
    ("RM",   ["RESSON","RMN","RNM","RM"]),
    ("TC",   ["TOMOGR","TOMOGRAF","ANGIOTC","ANGIO TC","TAC","TC"]),
    ("US",   ["ULTRA","USG","ECOGR","ECOGRAF","ECOCARDI","DOPPLER","US"]),
    ("MMG",  ["MAMOG","MMG","MG"]),
    ("DEXA", ["DENSIT","DENSITOM","DEXA","DXA","DMO"]),
    ("BIOP", ["BIOPSI","AGULHAM","MAMOTOM","PUNCAO","BIOPS"]),
    ("RX",   ["RAIO-X","RAIO X","RADIOGR","RDG","RX"]),
]

def kw_match(text, kw):
    if len(kw) > 3:
        return kw in text
    return bool(re.search(r"(^|[\s\-/(])" + re.escape(kw) + r"($|[\s\-/)])", text))

def classify(produto):
    p = normalize(produto)
    for grp, kws in GROUPS:
        if any(kw_match(p, normalize(k)) for k in kws):
            return grp
    return "Outros"

outros = {}
for row in data.get("registros", []):
    if classify(row["produto"]) == "Outros":
        p = row["produto"] or "—"
        outros[p] = outros.get(p, {"exames":0,"receita":0.0})
        outros[p]["exames"]  += row["exames"]
        outros[p]["receita"] += row["receita"]

print(f"\n{'RECEITA':>12}  EXAMES  PRODUTO")
print("-"*70)
for prod, v in sorted(outros.items(), key=lambda x: -x[1]["receita"])[:50]:
    print(f"R${v['receita']:>10,.0f}  {v['exames']:>5}x  {prod}")
