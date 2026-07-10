from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import pyodbc
import pandas as pd
import re as _re
import unicodedata as _uc

import time as _time

# ── Cache em memória com TTL ──────────────────────────────────────────────────
# Evita bater no Naja a cada abertura do dashboard.
# TTL padrão: 10 minutos. Aumentar se a clínica precisar de dados menos frescos.
_CACHE: dict = {}
_CACHE_TTL = 600  # segundos

def _cache_get(key: str):
    """Retorna o valor cacheado se ainda válido, senão None."""
    entry = _CACHE.get(key)
    if entry and (_time.time() - entry["ts"]) < _CACHE_TTL:
        return entry["val"]
    return None

def _cache_set(key: str, val):
    _CACHE[key] = {"val": val, "ts": _time.time()}

def _cache_key(*args, **kwargs) -> str:
    return str(args) + str(sorted(kwargs.items()))


# ── Classificação de modalidades ─────────────────────────────────────────────

def _norm(s):
    s = (s or '').upper()
    return ''.join(c for c in _uc.normalize('NFD', s) if _uc.category(c) != 'Mn')

def _kw_match(text, kw):
    if len(kw) > 3:
        return kw in text
    return bool(_re.search(r'(^|[\s\-/(])' + _re.escape(kw) + r'($|[\s\-/)])', text))

# ── Mapeamento nativo Naja (Subgrupo → modalidade) ────────────────────────────
# Usa os campos GrupoProduto/Subgrupo do próprio Naja — muito mais preciso
# que keyword matching no nome do produto.
_NAJA_SUBGRUPO_MAP = {
    # Ressonância
    'RESSONANCIA MAGNETICA': 'Ressonância Magnética',
    'RM': 'Ressonância Magnética',
    'RESSONANCIA': 'Ressonância Magnética',
    # Tomografia
    'TOMOGRAFIA': 'Tomografia',
    'TC': 'Tomografia',
    # Ultrassom
    'ULTRASSONOGRAFIA': 'Ultrassom',
    'ULTRASSOM': 'Ultrassom',
    'US': 'Ultrassom',
    # Mamografia
    'MAMOGRAFIA': 'Mamografia',
    'MG': 'Mamografia',
    # Densitometria
    'DENSITOMETRIA': 'Densitometria Óssea',
    'DENSITOMETRIA OSSEA': 'Densitometria Óssea',
    'DO': 'Densitometria Óssea',
    # Biópsias
    'BIOPSIAS E AGULHAMENTOS': 'Biópsias e Agulhamentos',
    'BIOPSIAS': 'Biópsias e Agulhamentos',
    'AGULHAMENTOS': 'Biópsias e Agulhamentos',
    'PUNCAO ASPIRATIVA (BAF)': 'Biópsias e Agulhamentos',
    'MARCACAO': 'Biópsias e Agulhamentos',
    'CORE BIOPSY': 'Biópsias e Agulhamentos',
    # Mamotomia
    'MAMOTOMIA': 'Mamotomia',
    'MM': 'Mamotomia',
    # Raio-X
    'RX': 'Raio-X',
    'RAIO-X': 'Raio-X',
    'RADIOGRAFIA': 'Raio-X',
}

# GrupoProduto = insumo → não é exame
_NAJA_INSUMO_GPS = {'CONTRASTE', 'MATERIAL HOSPITALAR', 'MEDICAMENTOS', 'TAXAS'}

# GrupoProduto = exame (Subgrupo define a modalidade)
_NAJA_EXAME_GPS = {'EXAMES'}

# GrupoProduto mapeado diretamente (quando o grupo JÁ é a modalidade)
_NAJA_GP_MAP = {
    'BIOPSIAS E AGULHAMENTOS': 'Biópsias e Agulhamentos',
    'MAMOTOMIA': 'Mamotomia',
    'RAIO-X': 'Raio-X',
    'RX': 'Raio-X',
}

# Fallback: keyword matching no nome do produto (para produtos sem Subgrupo mapeado)
_EXAM_GROUPS = [
    ('Ressonância Magnética',   ['RESSON','RMN','RNM','RM']),
    ('Tomografia',              ['TOMOGR','TOMOGRAF','ANGIOTC','ANGIO TC','TAC','TC']),
    ('Ultrassom',               ['ULTRA','USG','ECOGR','ECOGRAF','ECOCARDI','DOPPLER','ELASTOGRAFIA','US']),
    ('Mamografia',              ['MAMOG','MMG','MG']),
    ('Densitometria Óssea',     ['DENSIT','DENSITOM','DEXA','DXA','DMO']),
    ('Biópsias e Agulhamentos', ['BIOPSI','BIOPS','AGULHAM','PUNCAO','MARCACAO','CORE BIOPSY']),
    ('Mamotomia',               ['MAMOTOM','MAMOT']),
    ('Raio-X',                  ['RAIO-X','RAIO X','RADIOGR','RDG','RX']),
]

def _classify(produto):
    """Fallback: classifica pelo nome do produto via keyword matching."""
    p = _norm(produto)
    for grp, kws in _EXAM_GROUPS:
        if any(_kw_match(p, _norm(k)) for k in kws):
            return grp
    return None

def _classify_naja(grupo_produto, subgrupo, produto):
    """
    Classifica usando os campos nativos do Naja (GrupoProduto + Subgrupo).
    Regras de insumos:
      • CONTRASTE RESSONÂNCIA → soma com Ressonância Magnética
      • CONTRASTE TOMOGRAFIA  → soma com Tomografia
      • Demais contraste / Material Hospitalar / Medicamentos / Taxas → 'Outros'
    """
    gp = _norm(grupo_produto or '')
    sg = _norm(subgrupo or '')

    # 1. Contraste: atribui ao exame correspondente ou 'Outros'
    if gp == 'CONTRASTE':
        if sg == 'CONTRASTE RESSONANCIA':
            return 'Ressonância Magnética'
        elif sg == 'CONTRASTE TOMOGRAFIA':
            return 'Tomografia'
        else:
            return 'Outros'

    # 2. Materiais, medicamentos, taxas e itens não-clínicos → 'Outros'
    if gp in {'MATERIAL HOSPITALAR', 'MEDICAMENTOS', 'TAXAS',
              'AGULHAS/MAMOGUIDE/MAMOWIRE', 'MATERIAL DE ESCRITORIO'}:
        return 'Outros'

    # 3. Subgrupo mapeado diretamente → retorna modalidade
    if sg and sg in _NAJA_SUBGRUPO_MAP:
        return _NAJA_SUBGRUPO_MAP[sg]

    # 4. GrupoProduto mapeado como exame direto (ex: 'BIÓPSIAS E AGULHAMENTOS')
    if gp and gp in _NAJA_GP_MAP:
        return _NAJA_GP_MAP[gp]

    # 5. Se GrupoProduto = 'EXAMES' mas Subgrupo não mapeado → fallback keyword
    if gp in _NAJA_EXAME_GPS:
        return _classify(produto or '')

    # 6. Fallback geral: keyword matching no produto
    return _classify(produto or '')

app = FastAPI(title="API Financeira CDE")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

import os
from dotenv import load_dotenv

load_dotenv()  # carrega .env se existir

DB_SERVER   = os.getenv("DB_SERVER", "192.168.0.5")
DB_PORT     = int(os.getenv("DB_PORT", "1433"))
DB_NAME     = os.getenv("DB_NAME",   "Naja")
DB_USER     = os.getenv("DB_USER",   "Gisley_maver")
DB_PASSWORD = os.getenv("DB_PASSWORD")   # obrigatório — defina no .env
API_KEY     = os.getenv("API_KEY")       # obrigatório — defina no .env

if not DB_PASSWORD:
    raise RuntimeError("DB_PASSWORD não definido. Crie o arquivo .env (veja .env.example).")
if not API_KEY:
    raise RuntimeError("API_KEY não definido. Crie o arquivo .env (veja .env.example).")

def get_connection():
    conn_str = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={DB_SERVER},{DB_PORT};"
        f"DATABASE={DB_NAME};"
        f"UID={DB_USER};"
        f"PWD={DB_PASSWORD};"
        "TrustServerCertificate=yes;"
    )
    return pyodbc.connect(conn_str)

def verificar_chave(x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Chave de API inválida.")

@app.get("/")
def status():
    return {"status": "ok", "mensagem": "API Financeira CDE rodando."}

@app.get("/despesas")
def get_despesas(
    data_inicio: str = Query(...),
    data_fim: str = Query(...),
    x_api_key: str = Header(...)
):
    verificar_chave(x_api_key)
    try:
        conn = get_connection()
        query = """
            SELECT ENT_Num, CONVERT(varchar, ENT_Data, 23) AS ENT_Data,
                   ENT_NumNF, FOR_Nome, PRO_Cod, PRO_NomeCompleto
            FROM dbo.VW_EntradasFechadasClassDespesa
            WHERE ENT_Data >= ? AND ENT_Data < ?
            ORDER BY ENT_Data
        """
        df = pd.read_sql(query, conn, params=[data_inicio, data_fim])
        conn.close()
        return df.fillna("").to_dict(orient="records")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/unidades")
def get_unidades(x_api_key: str = Header(...)):
    verificar_chave(x_api_key)
    try:
        conn = get_connection()
        df = pd.read_sql("SELECT * FROM dbo.vw_ListaUnidades", conn)
        conn.close()
        return df.to_dict(orient="records")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/convenios")
def get_convenios(x_api_key: str = Header(...)):
    verificar_chave(x_api_key)
    try:
        conn = get_connection()
        df = pd.read_sql("SELECT * FROM dbo.vw_ListaConvenios", conn)
        conn.close()
        return df.to_dict(orient="records")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── DRE ─────────────────────────────────────────────────────────────────────

@app.get("/dre/receitas")
def dre_receitas(
    data_inicio: str = Query(...),
    data_fim: str = Query(...),
    empresa: str = Query(default=None),
    x_api_key: str = Header(...)
):
    """Receitas agrupadas por empresa, grupo e subgrupo de produto."""
    verificar_chave(x_api_key)
    try:
        conn = get_connection()
        params = [data_inicio, data_fim]
        ef = ""
        if empresa and empresa.upper() != "CONSOLIDADO":
            ef = "AND EmpresaAtendimento = ?"
            params.append(empresa)
        df = pd.read_sql(f"""
            SELECT EmpresaAtendimento AS empresa, GrupoProduto AS grupo,
                   Subgrupo AS subgrupo, SUM(ValorTotal) AS total, COUNT(*) AS qtd_itens
            FROM dbo.vw_ItensConta
            WHERE DataAtendimento >= ? AND DataAtendimento < ?
              AND Status IN ('F','L','A','P') {ef}
            GROUP BY EmpresaAtendimento, GrupoProduto, Subgrupo
            ORDER BY EmpresaAtendimento, SUM(ValorTotal) DESC
        """, conn, params=params)
        conn.close()
        df = df.fillna(0)
        return {"periodo": {"inicio": data_inicio, "fim": data_fim},
                "empresa": empresa or "CONSOLIDADO",
                "total_receita": float(df["total"].sum()),
                "grupos": df.to_dict(orient="records")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/dre/despesas")
def dre_despesas(
    data_inicio: str = Query(...),
    data_fim: str = Query(...),
    empresa: str = Query(default=None),
    x_api_key: str = Header(...)
):
    """Despesas agrupadas por empresa e conta de classificação."""
    verificar_chave(x_api_key)
    try:
        conn = get_connection()
        params = [data_inicio, data_fim]
        ef = ""
        if empresa and empresa.upper() != "CONSOLIDADO":
            ef = "AND EmpresaNota = ?"
            params.append(empresa)
        df = pd.read_sql(f"""
            SELECT EmpresaNota AS empresa, ContaClassificacao AS categoria,
                   SUM(ValClassificacao) AS total, COUNT(*) AS qtd_notas
            FROM dbo.vw_ContasComClassificacao
            WHERE DataNota >= ? AND DataNota < ?
              AND Status = 'A' {ef}
            GROUP BY EmpresaNota, ContaClassificacao
            ORDER BY EmpresaNota, SUM(ValClassificacao) DESC
        """, conn, params=params)
        conn.close()
        df = df.fillna(0)
        return {"periodo": {"inicio": data_inicio, "fim": data_fim},
                "empresa": empresa or "CONSOLIDADO",
                "total_despesas": float(df["total"].sum()),
                "categorias": df.to_dict(orient="records")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/dre/resumo")
def dre_resumo(
    data_inicio: str = Query(...),
    data_fim: str = Query(...),
    empresa: str = Query(default=None),
    x_api_key: str = Header(...)
):
    """DRE consolidada: receitas, despesas e resultado."""
    verificar_chave(x_api_key)
    ck = _cache_key("dre_resumo", data_inicio, data_fim, empresa)
    cached = _cache_get(ck)
    if cached is not None:
        return cached
    try:
        conn = get_connection()
        pr = [data_inicio, data_fim]
        pd_ = [data_inicio, data_fim]
        efr = efd = ""
        if empresa and empresa.upper() != "CONSOLIDADO":
            efr = "AND EmpresaAtendimento = ?"
            efd = "AND EmpresaNota = ?"
            pr.append(empresa); pd_.append(empresa)

        df_conv = pd.read_sql(f"""
            SELECT Convenio, SUM(ValorTotal) AS total
            FROM dbo.vw_ItensConta
            WHERE DataAtendimento >= ? AND DataAtendimento < ?
              AND Status IN ('F','L','A','P') {efr}
            GROUP BY Convenio ORDER BY SUM(ValorTotal) DESC
        """, conn, params=pr)

        df_grupo = pd.read_sql(f"""
            SELECT
                GrupoProduto AS grupo,
                SUM(ValorTotal) AS total,
                SUM(Qtde) AS volume
            FROM dbo.vw_ItensConta
            WHERE DataAtendimento >= ? AND DataAtendimento < ?
              AND Status IN ('F','L','A','P') {efr}
            GROUP BY GrupoProduto ORDER BY SUM(ValorTotal) DESC
        """, conn, params=pr)

        # Receita bruta: VlTotServ de vw_ConsultaItensDasContas (mesma base dos exames-stats)
        # VlTotServ = valor gross do serviço (antes de abatimentos); ValorTotal = já líquido
        efr_v = efr.replace('EmpresaAtendimento', '[Empresa]')
        df_rec_bruta = pd.read_sql(f"""
            SELECT SUM(ISNULL([VlTotServ], 0)) AS total
            FROM dbo.vw_ConsultaItensDasContas
            WHERE [Data do Atendimento] >= ? AND [Data do Atendimento] < ?
              AND [Status da Conta] IN ('F','L','A','P') {efr_v}
        """, conn, params=pr)

        df_desp = pd.read_sql(f"""
            SELECT
                LEFT(LTRIM(RTRIM(CodContaClassificacao)), 2) AS cod_prefix,
                LTRIM(RTRIM(CodContaClassificacao)) AS cod_rubrica,
                ContaClassificacao AS categoria,
                SUM(ValClassificacao) AS total
            FROM dbo.vw_ContasComClassificacao
            WHERE DataNota >= ? AND DataNota < ?
              AND Status = 'A' {efd}
            GROUP BY LEFT(LTRIM(RTRIM(CodContaClassificacao)), 2), LTRIM(RTRIM(CodContaClassificacao)), ContaClassificacao
            ORDER BY LTRIM(RTRIM(CodContaClassificacao)), ContaClassificacao
        """, conn, params=pd_)

        # Conta exames distintos (atendimentos únicos) para ticket médio correto
        df_exames = pd.read_sql(f"""
            SELECT COUNT(DISTINCT CAST([Atendimento] AS NVARCHAR(100))) AS total_exames
            FROM dbo.vw_ConsultaItensDasContas
            WHERE [Data do Atendimento] >= ? AND [Data do Atendimento] < ?
              AND [Status da Conta] IN ('F','L','A','P') {efr.replace('EmpresaAtendimento','[Empresa]')}
        """, conn, params=pr)
        conn.close()

        df_conv  = df_conv.fillna(0)
        df_grupo = df_grupo.fillna(0)
        df_desp  = df_desp.fillna(0)
        df_rec_bruta = df_rec_bruta.fillna(0)

        receita        = float(df_rec_bruta["total"].iloc[0]) if not df_rec_bruta.empty else float(df_grupo["total"].sum())
        total_exames   = int(df_exames["total_exames"].iloc[0]) if not df_exames.empty else 0
        volume         = float(df_grupo["volume"].sum())   # mantido para compatibilidade
        despesas       = float(df_desp["total"].sum())
        resultado      = receita - despesas
        margem         = (resultado / receita * 100) if receita > 0 else 0
        ticket         = round(receita / total_exames, 2) if total_exames > 0 else 0

        result = {
            "periodo": {"inicio": data_inicio, "fim": data_fim},
            "empresa": empresa or "CONSOLIDADO",
            "receita_bruta": receita,
            "total_exames": total_exames,
            "volume_total": volume,
            "ticket_medio": ticket,
            "total_despesas": despesas,
            "resultado": resultado,
            "margem_pct": round(margem, 2),
            "receitas_por_grupo": df_grupo.to_dict(orient="records"),
            "receitas_por_convenio": df_conv.to_dict(orient="records"),
            "despesas_por_categoria": df_desp.to_dict(orient="records")
        }
        _cache_set(ck, result)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/dre/tendencia")
def dre_tendencia(
    data_inicio: str = Query(..., description="YYYY-MM-DD — início da série"),
    data_fim: str = Query(..., description="YYYY-MM-DD — fim da série (exclusive)"),
    empresa: str = Query(default=None),
    x_api_key: str = Header(...)
):
    """
    Série temporal mensal: receita, volume, despesas, resultado e margem.
    Ideal para gráficos de tendência de 12 meses.
    """
    verificar_chave(x_api_key)
    ck = _cache_key("dre_tendencia", data_inicio, data_fim, empresa)
    cached = _cache_get(ck)
    if cached is not None:
        return cached
    try:
        conn = get_connection()
        pr = [data_inicio, data_fim]
        pd_ = [data_inicio, data_fim]
        efr = efd = ""
        if empresa and empresa.upper() != "CONSOLIDADO":
            efr = "AND EmpresaAtendimento = ?"
            efd = "AND EmpresaNota = ?"
            pr.append(empresa); pd_.append(empresa)

        df_rec = pd.read_sql(f"""
            SELECT
                FORMAT(DataAtendimento, 'yyyy-MM') AS ym,
                SUM(ValorTotal)  AS receita,
                SUM(Qtde)        AS volume,
                COUNT(*)         AS qtd_itens
            FROM dbo.vw_ItensConta
            WHERE DataAtendimento >= ? AND DataAtendimento < ?
              AND Status IN ('F','L','A','P') {efr}
            GROUP BY FORMAT(DataAtendimento, 'yyyy-MM')
            ORDER BY ym
        """, conn, params=pr)

        df_desp = pd.read_sql(f"""
            SELECT
                FORMAT(DataNota, 'yyyy-MM') AS ym,
                SUM(ValClassificacao) AS despesas
            FROM dbo.vw_ContasComClassificacao
            WHERE DataNota >= ? AND DataNota < ?
              AND Status = 'A' {efd}
            GROUP BY FORMAT(DataNota, 'yyyy-MM')
            ORDER BY ym
        """, conn, params=pd_)

        # Exames distintos por mês — base correta para ticket médio mensal
        efr_atend = efr.replace("EmpresaAtendimento", "[Empresa]")
        df_atend = pd.read_sql(f"""
            SELECT
                FORMAT([Data do Atendimento], 'yyyy-MM') AS ym,
                COUNT(DISTINCT CAST([Atendimento] AS NVARCHAR(100))) AS exames
            FROM dbo.vw_ConsultaItensDasContas
            WHERE [Data do Atendimento] >= ? AND [Data do Atendimento] < ?
              AND [Status da Conta] IN ('F','L','A','P') {efr_atend}
            GROUP BY FORMAT([Data do Atendimento], 'yyyy-MM')
        """, conn, params=pr)
        conn.close()

        df_rec   = df_rec.fillna(0)
        df_desp  = df_desp.fillna(0)
        df_atend = df_atend.fillna(0)

        merged = df_rec.merge(df_desp, on="ym", how="outer").fillna(0)
        merged = merged.merge(df_atend, on="ym", how="left").fillna(0)
        merged = merged.sort_values("ym")
        merged["resultado"] = merged["receita"] - merged["despesas"]
        merged["margem_pct"] = merged.apply(
            lambda r: round(r["resultado"] / r["receita"] * 100, 2) if r["receita"] > 0 else 0, axis=1
        )
        # Ticket médio correto: receita ÷ exames distintos (não linhas)
        merged["ticket_medio"] = merged.apply(
            lambda r: round(r["receita"] / r["exames"], 2) if r["exames"] > 0 else 0, axis=1
        )

        result = {
            "periodo": {"inicio": data_inicio, "fim": data_fim},
            "empresa": empresa or "CONSOLIDADO",
            "meses": merged.to_dict(orient="records")
        }
        _cache_set(ck, result)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/dre/receitas-analitico")
def dre_receitas_analitico(
    data_inicio: str = Query(..., description="YYYY-MM-DD"),
    data_fim: str = Query(..., description="YYYY-MM-DD (exclusive)"),
    empresa: str = Query(default=None),
    regime: str = Query(default="atend", description="atend = competência | fat = faturamento"),
    busca: str = Query(default=None, description="Filtro livre: paciente, produto, convênio"),
    limit: int = Query(default=100, le=2000),
    offset: int = Query(default=0),
    x_api_key: str = Header(...)
):
    """
    Dados analíticos completos de receita (nível de item).
    Inclui Paciente, VlTotServ, Data de Faturamento.
    Suporta regime de competência (atend) ou faturamento (fat).
    """
    verificar_chave(x_api_key)
    try:
        conn = get_connection()
        date_col = "[Data de Faturamento]" if regime == "fat" else "[Data do Atendimento]"
        params = [data_inicio, data_fim]
        empresa_filter = ""
        busca_filter = ""
        if empresa and empresa.upper() != "CONSOLIDADO":
            empresa_filter = "AND [Empresa] = ?"
            params.append(empresa)
        if busca:
            busca_filter = "AND (LOWER([Paciente]) LIKE LOWER(?) OR LOWER([Produto]) LIKE LOWER(?) OR LOWER([Convênio]) LIKE LOWER(?))"
            like = f"%{busca}%"
            params += [like, like, like]

        count_params = params[:]
        count_sql = f"""
            SELECT COUNT(*) AS total
            FROM dbo.vw_ConsultaItensDasContas
            WHERE {date_col} >= ? AND {date_col} < ?
              AND [Status da Conta] IN ('F','L','A','P')
              {empresa_filter} {busca_filter}
        """
        df_count = pd.read_sql(count_sql, conn, params=count_params)
        total = int(df_count["total"].iloc[0])

        params_pag = params + [offset, limit]
        query = f"""
            SELECT
                [Data do Atendimento]  AS data_atendimento,
                [Data de Faturamento]  AS data_faturamento,
                [Empresa]              AS empresa,
                [Paciente]             AS paciente,
                [Prontuário]           AS prontuario,
                [Produto]              AS produto,
                [Grupo de Produto]     AS grupo,
                [Subgrupo]             AS subgrupo,
                [Convênio]             AS convenio,
                [Plano Convênio]       AS plano,
                [NomeMedSolicitante]   AS medico,
                [Qtde]                 AS qtde,
                [ValTotal]             AS val_total,
                [VlTotServ]            AS vl_tot_serv,
                [Status da Conta]      AS status,
                [Atendimento]          AS atendimento,
                [AccessionNumber]      AS accession
            FROM dbo.vw_ConsultaItensDasContas
            WHERE {date_col} >= ? AND {date_col} < ?
              AND [Status da Conta] IN ('F','L','A','P')
              {empresa_filter} {busca_filter}
            ORDER BY {date_col} DESC
            OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
        """
        df = pd.read_sql(query, conn, params=params_pag)
        conn.close()
        df = df.fillna("")
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "regime": regime,
            "registros": df.to_dict(orient="records")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/dre/medicos")
def dre_medicos(
    data_inicio: str = Query(...),
    data_fim: str = Query(...),
    empresa: str = Query(default=None),
    x_api_key: str = Header(...)
):
    """Receita agrupada por médico solicitante com ticket médio."""
    verificar_chave(x_api_key)
    ck = _cache_key("dre_medicos", data_inicio, data_fim, empresa)
    cached = _cache_get(ck)
    if cached is not None:
        return cached
    try:
        conn = get_connection()
        params = [data_inicio, data_fim]
        ef = ""
        if empresa and empresa.upper() != "CONSOLIDADO":
            ef = "AND [Empresa] = ?"
            params.append(empresa)
        df = pd.read_sql(f"""
            SELECT
                CAST([NomeMedSolicitante] AS NVARCHAR(500)) AS medico,
                SUM([VlTotServ])       AS receita,
                SUM([Qtde])            AS volume,
                COUNT(DISTINCT CAST([Atendimento] AS NVARCHAR(100))) AS atendimentos
            FROM dbo.vw_ConsultaItensDasContas
            WHERE [Data do Atendimento] >= ? AND [Data do Atendimento] < ?
              AND [Status da Conta] IN ('F','L','A','P') {ef}
            GROUP BY CAST([NomeMedSolicitante] AS NVARCHAR(500))
            ORDER BY SUM([VlTotServ]) DESC
        """, conn, params=params)

        # Classificação por médico × modalidade (para stacked bar no dashboard)
        df_items = pd.read_sql(f"""
            SELECT
                CAST([NomeMedSolicitante] AS NVARCHAR(500)) AS medico,
                CAST([Produto]            AS NVARCHAR(500)) AS produto,
                CAST([Grupo de Produto]   AS NVARCHAR(500)) AS grupo_produto,
                CAST([Subgrupo]           AS NVARCHAR(500)) AS subgrupo,
                ISNULL([VlTotServ], 0)                      AS receita
            FROM dbo.vw_ConsultaItensDasContas
            WHERE [Data do Atendimento] >= ? AND [Data do Atendimento] < ?
              AND [Status da Conta] IN ('F','L','A','P') {ef}
        """, conn, params=params)
        conn.close()

        df = df.fillna(0)
        df["ticket_medio"] = df.apply(
            lambda r: round(r["receita"] / r["atendimentos"], 2) if r["atendimentos"] > 0 else 0, axis=1
        )

        # Monta dict medico → modalidade → receita (usa classificação nativa do Naja)
        df_items["produto"]       = df_items["produto"].fillna("").astype(str)
        df_items["medico"]        = df_items["medico"].fillna("").astype(str)
        df_items["grupo_produto"] = df_items["grupo_produto"].fillna("").astype(str)
        df_items["subgrupo"]      = df_items["subgrupo"].fillna("").astype(str)
        df_items["receita"]       = pd.to_numeric(df_items["receita"], errors="coerce").fillna(0)
        df_items["modalidade"]    = df_items.apply(
            lambda r: _classify_naja(r["grupo_produto"], r["subgrupo"], r["produto"]), axis=1
        )
        df_items = df_items[df_items["modalidade"].notna()]

        by_med_mod = (
            df_items.groupby(["medico", "modalidade"])["receita"]
            .sum().reset_index()
        )
        by_med_group: dict = {}
        for _, row in by_med_mod.iterrows():
            m = str(row["medico"])
            if m not in by_med_group:
                by_med_group[m] = {}
            by_med_group[m][str(row["modalidade"])] = float(row["receita"])

        return {
            "periodo": {"inicio": data_inicio, "fim": data_fim},
            "empresa": empresa or "CONSOLIDADO",
            "total_receita": float(df["receita"].sum()),
            "medicos": df.to_dict(orient="records"),
            "byMedGroup": by_med_group,
        }
        _cache_set(ck, result)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/dre/exames-stats")
def dre_exames_stats(
    data_inicio: str = Query(...),
    data_fim: str = Query(...),
    empresa: str = Query(default=None),
    regime: str = Query(default="atend", description="atend = competência | fat = faturamento"),
    x_api_key: str = Header(...)
):
    """
    Estatísticas de exames — metodologia Naja BI:
    • Conta LINHAS de faturamento por modalidade (= QTD Naja BI)
    • Valor = VlTotServ puro das linhas de exame (sem atribuição de insumos)
    • Insumos (contraste, materiais, taxas) ficam separados → não inflam receita por setor
    • Resultado: QTD e VALOR por setor batem com Naja BI
    """
    verificar_chave(x_api_key)
    ck = _cache_key("dre_exames_stats", data_inicio, data_fim, empresa, regime)
    cached = _cache_get(ck)
    if cached is not None:
        return cached
    import traceback as tb
    try:
        conn = get_connection()
        date_col = "[Data de Faturamento]" if regime == "fat" else "[Data do Atendimento]"
        params = [data_inicio, data_fim]
        ef = ""
        if empresa and empresa.upper() != "CONSOLIDADO":
            ef = "AND [Empresa] = ?"
            params.append(empresa)

        df = pd.read_sql(f"""
            SELECT
                CAST([Atendimento]      AS NVARCHAR(100)) AS atendimento,
                CAST([Produto]          AS NVARCHAR(500)) AS produto,
                CAST([Convênio]         AS NVARCHAR(500)) AS convenio,
                CAST([Grupo de Produto] AS NVARCHAR(500)) AS grupo_produto,
                CAST([Subgrupo]         AS NVARCHAR(500)) AS subgrupo,
                ISNULL([VlTotServ], 0)                    AS receita
            FROM dbo.vw_ConsultaItensDasContas
            WHERE {date_col} >= ? AND {date_col} < ?
              AND [Status da Conta] IN ('F','L','A','P') {ef}
        """, conn, params=params)
        conn.close()

        if df.empty:
            return {"periodo": {"inicio": data_inicio, "fim": data_fim},
                    "empresa": empresa or "CONSOLIDADO", "regime": regime,
                    "total_exames": 0, "total_receita": 0.0, "ticket_medio": 0.0,
                    "registros": []}

        df["produto"]       = df["produto"].fillna("").astype(str)
        df["convenio"]      = df["convenio"].fillna("").astype(str)
        df["atendimento"]   = df["atendimento"].fillna("").astype(str)
        df["grupo_produto"] = df["grupo_produto"].fillna("").astype(str)
        df["subgrupo"]      = df["subgrupo"].fillna("").astype(str)
        df["receita"]       = pd.to_numeric(df["receita"], errors="coerce").fillna(0)

        # Classifica cada item usando GrupoProduto + Subgrupo do Naja
        df["modalidade"] = df.apply(
            lambda r: _classify_naja(r["grupo_produto"], r["subgrupo"], r["produto"]), axis=1
        )

        # Apenas linhas de exame (modalidade não nula)
        # Insumos (CONTRASTE, MATERIAL HOSPITALAR, TAXAS, MEDICAMENTOS) ficam excluídos
        exam_df = df[df["modalidade"].notna()].copy()

        if exam_df.empty:
            return {"periodo": {"inicio": data_inicio, "fim": data_fim},
                    "empresa": empresa or "CONSOLIDADO", "regime": regime,
                    "total_exames": 0, "total_receita": 0.0, "ticket_medio": 0.0,
                    "registros": []}

        # Agrega por (produto × convênio × modalidade)
        # exames  = COUNT de linhas (= QTD Naja BI, não atendimentos únicos)
        # receita = SUM VlTotServ puro (= VALOR Naja BI por setor)
        agg = (
            exam_df.groupby(["produto", "convenio", "modalidade"])
            .agg(
                exames  =("atendimento", "count"),
                receita =("receita",     "sum"),
            )
            .reset_index()
        )
        agg["receita_insumos"] = 0.0
        agg["receita_exame"]   = agg["receita"]
        agg = agg.sort_values("receita", ascending=False)

        # Total: exclui 'Outros' (materiais, meds, taxas) — conta só linhas de exame
        exam_only     = agg[agg["modalidade"] != "Outros"]
        total_exames  = int(exam_only["exames"].sum())
        total_receita = float(exam_only["receita"].sum())
        ticket_medio  = round(total_receita / total_exames, 2) if total_exames > 0 else 0

        result = {
            "periodo": {"inicio": data_inicio, "fim": data_fim},
            "empresa": empresa or "CONSOLIDADO",
            "regime": regime,
            "total_exames": total_exames,
            "total_receita": total_receita,
            "ticket_medio": ticket_medio,
            "registros": agg.to_dict(orient="records"),
        }
        _cache_set(ck, result)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{str(e)} ||| {tb.format_exc()}")


@app.get("/dre/empresas")
def dre_empresas(
    data_inicio: str = Query(...),
    data_fim: str = Query(...),
    x_api_key: str = Header(...)
):
    """Lista empresas com movimento no período."""
    verificar_chave(x_api_key)
    try:
        conn = get_connection()
        df = pd.read_sql("""
            SELECT DISTINCT EmpresaAtendimento AS empresa
            FROM dbo.vw_ItensConta
            WHERE DataAtendimento >= ? AND DataAtendimento < ?
            ORDER BY EmpresaAtendimento
        """, conn, params=[data_inicio, data_fim])
        conn.close()
        return {"empresas": ["CONSOLIDADO"] + df["empresa"].tolist()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Schema ───────────────────────────────────────────────────────────────────

@app.get("/schema/views")
def get_views(x_api_key: str = Header(...)):
    verificar_chave(x_api_key)
    try:
        conn = get_connection()
        df = pd.read_sql("SELECT TABLE_NAME as view_name FROM INFORMATION_SCHEMA.VIEWS ORDER BY TABLE_NAME", conn)
        conn.close()
        return df.to_dict(orient="records")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/schema/colunas/{view_name}")
def get_colunas(view_name: str, x_api_key: str = Header(...)):
    verificar_chave(x_api_key)
    try:
        conn = get_connection()
        df = pd.read_sql(f"SELECT TOP 0 * FROM dbo.[{view_name}]", conn)
        conn.close()
        return {"view": view_name, "colunas": list(df.columns)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/schema/amostra/{view_name}")
def get_amostra(view_name: str, rows: int = Query(default=10, le=100), x_api_key: str = Header(...)):
    verificar_chave(x_api_key)
    try:
        conn = get_connection()
        df = pd.read_sql(f"SELECT TOP {rows} * FROM dbo.[{view_name}]", conn)
        conn.close()
        df = df.fillna("")
        return {"view": view_name, "total_colunas": len(df.columns), "registros": df.to_dict(orient="records")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/schema/buscar")
def buscar_views(termo: str = Query(...), x_api_key: str = Header(...)):
    verificar_chave(x_api_key)
    try:
        conn = get_connection()
        df = pd.read_sql("""
            SELECT TABLE_NAME as view_name FROM INFORMATION_SCHEMA.VIEWS
            WHERE LOWER(TABLE_NAME) LIKE LOWER(?) ORDER BY TABLE_NAME
        """, conn, params=[f"%{termo}%"])
        conn.close()
        return df.to_dict(orient="records")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/query/view")
def query_view(
    view_name: str = Query(...),
    data_inicio: str = Query(default=None),
    data_fim: str = Query(default=None),
    data_col: str = Query(default=None),
    limit: int = Query(default=100, le=1000),
    x_api_key: str = Header(...)
):
    verificar_chave(x_api_key)
    try:
        conn = get_connection()
        if data_inicio and data_fim and data_col:
            query = f"SELECT TOP {limit} * FROM dbo.[{view_name}] WHERE [{data_col}] >= ? AND [{data_col}] < ? ORDER BY [{data_col}]"
            df = pd.read_sql(query, conn, params=[data_inicio, data_fim])
        else:
            df = pd.read_sql(f"SELECT TOP {limit} * FROM dbo.[{view_name}]", conn)
        conn.close()
        df = df.fillna("")
        return {"view": view_name, "total": len(df), "colunas": list(df.columns), "registros": df.to_dict(orient="records")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/dic-fornecedores")
def get_dic_fornecedores(
    anos: int = Query(default=2, description="Quantos anos de histórico"),
    x_api_key: str = Header(...)
):
    """
    Dicionário histórico de fornecedores Naja → classificação DRE.
    Retorna {FOR_Nome: {c: PRO_NomeCompleto, cod: PRO_Cod, g: grupo_inferido, freq: n}}
    para os últimos N anos. Usado pelo dashboard para preencher descrições vazias.
    """
    verificar_chave(x_api_key)
    try:
        conn = get_connection()
        df = pd.read_sql("""
            SELECT DISTINCT FOR_Nome, PRO_NomeCompleto, PRO_Cod
            FROM dbo.VW_EntradasFechadasClassDespesa
            WHERE FOR_Nome IS NOT NULL AND FOR_Nome != ''
              AND PRO_NomeCompleto IS NOT NULL AND PRO_NomeCompleto != ''
        """, conn)
        conn.close()
        df = df.fillna("")

        # Por FOR_Nome, mantém a classificação mais frequente
        result = {}
        for _, row in df.iterrows():
            forn = str(row["FOR_Nome"]).strip()
            if not forn or forn in result:
                continue  # já tem a mais frequente (ORDER BY freq DESC)
            result[forn] = {
                "c": str(row["PRO_NomeCompleto"]).strip(),
                "cod": str(row["PRO_Cod"]).strip(),
                }
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/notas-despesa")
def get_notas_despesa(
    data_inicio: str = Query(..., description="YYYY-MM-DD"),
    data_fim: str = Query(..., description="YYYY-MM-DD (exclusive)"),
    x_api_key: str = Header(...)
):
    """Notas de despesa do Naja — ENT_Num, fornecedor, classe e valor para match com extrato."""
    verificar_chave(x_api_key)
    try:
        conn = get_connection()
        # SELECT * para capturar todas as colunas disponíveis (incluindo valor, se existir)
        df = pd.read_sql("""
            SELECT *
            FROM dbo.VW_EntradasFechadasClassDespesa
            WHERE ENT_Data >= ? AND ENT_Data < ?
            ORDER BY ENT_Data
        """, conn, params=[data_inicio, data_fim])
        conn.close()
        for col in df.columns:
            try:
                if hasattr(df[col], 'dt'):
                    df[col] = df[col].astype(str)
            except Exception:
                pass
        df = df.fillna("")
        return {"total": len(df), "colunas": list(df.columns), "registros": df.to_dict(orient="records")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/notas-mes")
def get_notas_mes(
    mes: str = Query(..., description="YYYY-MM — ex: 2026-01"),
    x_api_key: str = Header(...)
):
    """Todas as notas de despesa de um mês — para pre-fetch no dashboard."""
    verificar_chave(x_api_key)
    try:
        import calendar
        ano, m = int(mes[:4]), int(mes[5:7])
        data_inicio = f"{ano}-{m:02d}-01"
        if m == 12:
            data_fim = f"{ano+1}-01-01"
        else:
            data_fim = f"{ano}-{m+1:02d}-01"

        conn = get_connection()
        df = pd.read_sql("""
            SELECT *
            FROM dbo.VW_EntradasFechadasClassDespesa
            WHERE ENT_Data >= ? AND ENT_Data < ?
            ORDER BY ENT_Data, ENT_Num
        """, conn, params=[data_inicio, data_fim])
        conn.close()
        for col in df.columns:
            try:
                df[col] = df[col].astype(str)
            except Exception:
                pass
        df = df.fillna("")
        return {
            "mes": mes,
            "total": len(df),
            "colunas": list(df.columns),
            "registros": df.to_dict(orient="records")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/notas-periodo")
def get_notas_periodo(
    inicio: str = Query(..., description="YYYY-MM — mês inicial, ex: 2026-01"),
    fim: str = Query(..., description="YYYY-MM — mês final inclusive, ex: 2026-06"),
    x_api_key: str = Header(...)
):
    """Todas as notas de despesa de um período — para baking estático no dashboard."""
    verificar_chave(x_api_key)
    try:
        ano_i, m_i = int(inicio[:4]), int(inicio[5:7])
        ano_f, m_f = int(fim[:4]), int(fim[5:7])
        data_inicio = f"{ano_i}-{m_i:02d}-01"
        if m_f == 12:
            data_fim = f"{ano_f+1}-01-01"
        else:
            data_fim = f"{ano_f}-{m_f+1:02d}-01"

        conn = get_connection()
        conn = get_connection()
        df = pd.read_sql("""
            SELECT *
            FROM dbo.VW_EntradasFechadasClassDespesa
            WHERE ENT_Data >= ? AND ENT_Data < ?
            ORDER BY ENT_Data, ENT_Num
        """, conn, params=[data_inicio, data_fim])
        conn.close()
        for col in df.columns:
            try:
                df[col] = df[col].astype(str)
            except Exception:
                pass
        df = df.fillna("")
        return {
            "inicio": inicio,
            "fim": fim,
            "total": len(df),
            "colunas": list(df.columns),
            "registros": df.to_dict(orient="records")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
