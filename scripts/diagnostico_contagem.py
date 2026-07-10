"""
diagnostico_contagem.py
========================
1. Prova que Naja BI conta LINHAS de faturamento enquanto
   nosso dashboard conta ATENDIMENTOS ÚNICOS (patient visits)
2. Investiga a diferença de VALOR DO entre Naja BI (R$91.941) e nosso sistema
3. Lista todos os valores de 'Grupo de Produto' e 'Subgrupo' do Naja

Ajuste DATA_INICIO, DATA_FIM e EMPRESA conforme necessário.
"""

import pyodbc
import pandas as pd
from datetime import datetime

# ─── Conexão ────────────────────────────────────────────────────────────────
DB_SERVER   = "192.168.0.5"
DB_PORT     = 1433
DB_NAME     = "Naja"
DB_USER     = "Gisley_maver"
DB_PASSWORD = "Mudar@123"

DATA_INICIO = "2026-02-01"
DATA_FIM    = "2026-03-01"
EMPRESA     = "CDE CAMPINAS"   # None = CONSOLIDADO

def get_conn():
    return pyodbc.connect(
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={DB_SERVER},{DB_PORT};DATABASE={DB_NAME};"
        f"UID={DB_USER};PWD={DB_PASSWORD};TrustServerCertificate=yes;"
    )

# ─── Filtro de empresa ───────────────────────────────────────────────────────
emp_filter = ""
params     = [DATA_INICIO, DATA_FIM]
if EMPRESA:
    emp_filter = "AND [Empresa] = ?"
    params.append(EMPRESA)

conn = get_conn()

# ─── Tabela 1: linhas vs atendimentos por Grupo de Produto ──────────────────
df = pd.read_sql(f"""
    SELECT
        CAST([Grupo de Produto] AS NVARCHAR(500))                        AS grupo,
        COUNT(*)                                                          AS linhas,
        COUNT(DISTINCT CAST([Atendimento] AS NVARCHAR(100)))             AS atendimentos,
        CAST(COUNT(*) AS FLOAT)
            / NULLIF(COUNT(DISTINCT CAST([Atendimento] AS NVARCHAR(100))), 0)
                                                                          AS la_ratio,
        SUM(ISNULL([VlTotServ], 0))                                       AS receita
    FROM dbo.vw_ConsultaItensDasContas
    WHERE [Data do Atendimento] >= ? AND [Data do Atendimento] < ?
      AND [Status da Conta] IN ('F','L','A') {emp_filter}
    GROUP BY CAST([Grupo de Produto] AS NVARCHAR(500))
    ORDER BY COUNT(DISTINCT CAST([Atendimento] AS NVARCHAR(100))) DESC
""", conn, params=params)

# ─── Tabela 2: Grupo × Subgrupo ─────────────────────────────────────────────
df_sub = pd.read_sql(f"""
    SELECT
        CAST([Grupo de Produto] AS NVARCHAR(500))            AS grupo,
        CAST([Subgrupo]         AS NVARCHAR(500))            AS subgrupo,
        COUNT(DISTINCT CAST([Atendimento] AS NVARCHAR(100))) AS atendimentos,
        SUM(ISNULL([VlTotServ], 0))                          AS receita
    FROM dbo.vw_ConsultaItensDasContas
    WHERE [Data do Atendimento] >= ? AND [Data do Atendimento] < ?
      AND [Status da Conta] IN ('F','L','A') {emp_filter}
    GROUP BY CAST([Grupo de Produto] AS NVARCHAR(500)), CAST([Subgrupo] AS NVARCHAR(500))
    ORDER BY COUNT(DISTINCT CAST([Atendimento] AS NVARCHAR(100))) DESC
""", conn, params=params)

# ─── Tabela 3: DENSITOMETRIA — quebra por Status da Conta ───────────────────
# Naja BI referência: QTD. DO = 601, VALOR DO = 91.941,69
params_do = [DATA_INICIO, DATA_FIM]
ef_do = ""
if EMPRESA:
    ef_do = "AND [Empresa] = ?"
    params_do.append(EMPRESA)

df_do_status = pd.read_sql(f"""
    SELECT
        CAST([Status da Conta] AS NVARCHAR(20))              AS status,
        COUNT(*)                                              AS linhas,
        COUNT(DISTINCT CAST([Atendimento] AS NVARCHAR(100))) AS atendimentos,
        SUM(ISNULL([VlTotServ], 0))                          AS vlTotServ
    FROM dbo.vw_ConsultaItensDasContas
    WHERE [Data do Atendimento] >= ? AND [Data do Atendimento] < ?
      AND CAST([Subgrupo] AS NVARCHAR(500)) = 'DENSITOMETRIA'
      {ef_do}
    GROUP BY CAST([Status da Conta] AS NVARCHAR(20))
    ORDER BY COUNT(*) DESC
""", conn, params=params_do)

# ─── Tabela 4: Colunas de valor disponíveis para DENSITOMETRIA ──────────────
# Tenta selecionar colunas de valor alternativas para comparar com Naja BI
try:
    df_do_cols = pd.read_sql(f"""
        SELECT TOP 5
            CAST([Status da Conta]  AS NVARCHAR(20))  AS status,
            CAST([Atendimento]      AS NVARCHAR(100)) AS atendimento,
            ISNULL([VlTotServ], 0)                    AS VlTotServ,
            ISNULL([ValTotal], 0)                     AS ValTotal,
            ISNULL([VlRecBruto], 0)                   AS VlRecBruto,
            ISNULL([VlFaturado], 0)                   AS VlFaturado
        FROM dbo.vw_ConsultaItensDasContas
        WHERE [Data do Atendimento] >= ? AND [Data do Atendimento] < ?
          AND CAST([Subgrupo] AS NVARCHAR(500)) = 'DENSITOMETRIA'
          AND [Status da Conta] IN ('F','L','A')
          {ef_do}
    """, conn, params=params_do)
    has_multi_cols = True
except Exception:
    has_multi_cols = False

# ─── Tabela 5: Colunas disponíveis na view ───────────────────────────────────
try:
    df_cols = pd.read_sql("""
        SELECT COLUMN_NAME, DATA_TYPE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = 'vw_ConsultaItensDasContas'
        ORDER BY ORDINAL_POSITION
    """, conn)
    cols_ok = True
except Exception:
    cols_ok = False

conn.close()

# ─── Impressão ───────────────────────────────────────────────────────────────
SEP = "=" * 80
sep = "-" * 80
W   = 34

print(f"\n{SEP}")
print(f"  DIAGNÓSTICO DE CONTAGEM — {DATA_INICIO[:7]}  |  Empresa: {EMPRESA or 'CONSOLIDADO'}")
print(SEP)

# TABELA 1
print(f"\n{'TABELA 1: Linhas vs Atendimentos por Grupo':^80}")
print(f"{'Grupo de Produto (Naja)':<{W}} {'Linhas':>8}  {'Atend.':>7}  {'L/A':>5}  {'Receita':>14}")
print(sep)
for _, r in df.iterrows():
    g = str(r['grupo'] or '(sem grupo)')[:W]
    print(f"{g:<{W}} {int(r['linhas']):>8,}  {int(r['atendimentos']):>7,}  {r['la_ratio']:>5.2f}  R${r['receita']:>12,.0f}")

total_l = df['linhas'].sum()
total_a = df['atendimentos'].sum()
total_r = df['receita'].sum()
print(sep)
print(f"{'TOTAL':<{W}} {int(total_l):>8,}  {int(total_a):>7,}  {total_l/total_a:>5.2f}  R${total_r:>12,.0f}")

print(f"""
┌─ INTERPRETAÇÃO ──────────────────────────────────────────────────────────┐
│  Naja BI exibe LINHAS de faturamento . . . . . . . . . {int(total_l):>8,}     │
│  Nosso dashboard exibe ATENDIMENTOS ÚNICOS (pacientes) {int(total_a):>8,}     │
│  Razão média: {total_l/total_a:.2f} linhas por atendimento                          │
│                                                                          │
│  1 paciente de Densitometria gera ~{df[df['grupo'].str.upper().str.contains('EXAME', na=False)]['la_ratio'].mean():.2f} linhas no Naja (exame + laudo)  │
│  Naja BI conta LINHAS; nosso dashboard conta PACIENTES ÚNICOS            │
└──────────────────────────────────────────────────────────────────────────┘
""")

# TABELA 2
print(f"\n{'TABELA 2: Grupos × Subgrupos disponíveis no Naja':^80}")
print(f"{'Grupo de Produto':<32} {'Subgrupo':<32} {'Atend.':>7}  {'Receita':>14}")
print(sep)
for _, r in df_sub.iterrows():
    g = str(r['grupo']    or '—')[:32]
    s = str(r['subgrupo'] or '—')[:32]
    print(f"{g:<32} {s:<32} {int(r['atendimentos']):>7,}  R${r['receita']:>12,.0f}")

# TABELA 3 — Densitometria por Status
print(f"\n{'TABELA 3: DENSITOMETRIA — Quebra por Status da Conta':^80}")
print(f"  (Naja BI referência: QTD.DO = 601, VALOR DO = R$ 91.941,69)")
print(f"  Nosso filtro atual: Status IN ('F','L','A')")
print(f"{'Status':<20} {'Linhas':>8}  {'Atend.':>7}  {'VlTotServ':>14}")
print(sep)
total_do_linhas = 0
total_do_atend  = 0
total_do_valor  = 0
for _, r in df_do_status.iterrows():
    st   = str(r['status'] or '—')[:20]
    mark = " ← incluído" if str(r['status']).strip().upper() in ('F','L','A') else " ← EXCLUÍDO pelo filtro"
    print(f"{st:<20} {int(r['linhas']):>8,}  {int(r['atendimentos']):>7,}  R${r['vlTotServ']:>12,.0f}{mark}")
    total_do_linhas += int(r['linhas'])
    total_do_atend  += int(r['atendimentos'])
    total_do_valor  += float(r['vlTotServ'])
print(sep)
print(f"{'TOTAL (todos status)':<20} {total_do_linhas:>8,}  {total_do_atend:>7,}  R${total_do_valor:>12,.0f}")
do_fil = df_do_status[df_do_status['status'].str.strip().str.upper().isin(['F','L','A'])]
print(f"{'COM FILTRO (F,L,A)':<20} {int(do_fil['linhas'].sum()):>8,}  {int(do_fil['atendimentos'].sum()):>7,}  R${float(do_fil['vlTotServ'].sum()):>12,.0f}")
print(f"\n  → Naja BI mostra 601 linhas / R$91.941. Diferença de valor: R${91941.69 - float(do_fil['vlTotServ'].sum()):,.0f}")
print(f"  → A diferença de quantidade (601 - {int(do_fil['linhas'].sum())}) explica parte do valor?")

# TABELA 4 — colunas de valor alternativas
if has_multi_cols:
    print(f"\n{'TABELA 4: Colunas de Valor Alternativas (5 primeiros registros DO)':^80}")
    print(df_do_cols.to_string(index=False))
else:
    print(f"\n[Tabela 4 ignorada — colunas ValTotal/VlRecBruto não existem na view]")

# TABELA 5 — colunas disponíveis
if cols_ok:
    valor_cols = df_cols[df_cols['COLUMN_NAME'].str.lower().str.contains('vl|val|tot|rec|fat|bru', regex=True)]
    print(f"\n{'TABELA 5: Colunas de valor na view vw_ConsultaItensDasContas':^80}")
    print(f"  (Use isso para identificar qual coluna o Naja BI usa)")
    print(valor_cols.to_string(index=False))

# ─── Tabela 6: Atendimentos com múltiplas linhas de DENSITOMETRIA ─────────────
# Busca item a item (sem STRING_AGG — não suportado no SQL Server 2017)
conn2 = get_conn()
params_do2 = [DATA_INICIO, DATA_FIM]
ef_do2 = ""
if EMPRESA:
    ef_do2 = "AND [Empresa] = ?"
    params_do2.append(EMPRESA)

df_raw = pd.read_sql(f"""
    SELECT
        CAST([Atendimento]     AS NVARCHAR(100)) AS atendimento,
        CAST([Paciente]        AS NVARCHAR(500)) AS paciente,
        CAST([Empresa]         AS NVARCHAR(200)) AS empresa,
        CAST([Status da Conta] AS NVARCHAR(10))  AS status,
        CAST([Produto]         AS NVARCHAR(500)) AS produto,
        ISNULL([VlTotServ], 0)                   AS valor,
        CAST([Data do Atendimento] AS DATE)       AS data_atend
    FROM dbo.vw_ConsultaItensDasContas
    WHERE [Data do Atendimento] >= ? AND [Data do Atendimento] < ?
      AND CAST([Subgrupo] AS NVARCHAR(500)) = 'DENSITOMETRIA'
      AND [Status da Conta] IN ('F','L','A','P')
      {ef_do2}
""", conn2, params=params_do2)
conn2.close()

# Agrega em Python: conta linhas por atendimento e concatena produtos
df_raw["produto"] = df_raw["produto"].fillna("").astype(str)
df_raw["paciente"] = df_raw["paciente"].fillna("").astype(str)
grp = df_raw.groupby(["atendimento","paciente","empresa","status","data_atend"]).agg(
    linhas_do=("produto","count"),
    valor_total=("valor","sum"),
    produtos=("produto", lambda x: " | ".join(x))
).reset_index()
df_multi = grp[grp["linhas_do"] > 1].sort_values("linhas_do", ascending=False)

print(f"\n{'TABELA 6: Atendimentos com 2+ linhas de DENSITOMETRIA':^80}")
print(f"  → Paciente que fez lombar + fêmur em 2 itens, ou mesmo exame 2x no mês")
if df_multi.empty:
    print("  Nenhum encontrado — diferença tem outra causa.")
else:
    total_linhas_extra = int(df_multi['linhas_do'].sum()) - len(df_multi)
    print(f"  {len(df_multi)} atendimentos com múltiplas linhas DO → {total_linhas_extra} linhas 'extras'")
    print(f"  (Naja BI conta cada linha; nosso dashboard conta cada atendimento)")
    print()
    print(f"{'Atendimento':<14} {'Paciente':<30} {'St':<3} {'L':>3}  {'Valor':>12}  {'Data':<12}  Produtos")
    print(sep)
    for _, r in df_multi.iterrows():
        pac  = str(r['paciente'])[:30]
        atd  = str(r['atendimento'])[:14]
        prds = str(r['produtos'])[:50]
        print(f"{atd:<14} {pac:<30} {str(r['status']):<3} {int(r['linhas_do']):>3}  R${float(r['valor_total']):>10,.0f}  {str(r['data_atend']):<12}  {prds}")

print(f"\n{SEP}")
print(f"  Gerado em {datetime.now().strftime('%d/%m/%Y às %H:%M')}  |  diagnostico_contagem.py")
print(SEP)
