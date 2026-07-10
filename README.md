# DRE Financeiro — Clínica CDE

Dashboard financeiro em tempo real integrado ao ERP Naja (SQL Server), desenvolvido para a controladoria da Clínica CDE.

## Arquitetura

```
Naja ERP (SQL Server 2017)
        ↓ pyodbc
api/main.py  (FastAPI, porta 8000)
        ↓ HTTP + API key
dashboard/dashboard_cde_v3.html  (HTML/JS puro, abre no browser)
```

## Estrutura

```
├── api/
│   ├── main.py          — API FastAPI com todos os endpoints DRE
│   ├── .env             — credenciais locais (não vai ao git)
│   └── .env.example     — template de credenciais
├── dashboard/
│   └── dashboard_cde_v3.html — dashboard completo (sem dependências externas)
├── scripts/
│   ├── diagnostico_contagem.py — diagnóstico de contagem de exames
│   └── ver_outros.py           — análise de itens "Outros"
└── README.md
```

## Setup

### 1. Instalar dependências

```bash
pip install fastapi uvicorn pyodbc pandas python-dotenv
```

### 2. Configurar credenciais

```bash
cd api/
cp .env.example .env
# Editar .env com as credenciais reais do banco Naja
```

### 3. Iniciar API

```bash
cd api/
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Ou via BAT (Windows):
```bat
@echo off
cd /d "C:\caminho\para\api"
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 4. Expor via ngrok (para acesso externo)

```bash
ngrok http 8000
```

Substitua a URL em `dashboard_cde_v3.html`:
```javascript
const API = "https://SEU-SUBDOMINIO.ngrok-free.dev";
```

### 5. Abrir dashboard

Abrir `dashboard/dashboard_cde_v3.html` diretamente no browser.

## Endpoints principais

| Endpoint | Descrição |
|---|---|
| `GET /dre/resumo` | Resumo DRE do período |
| `GET /dre/tendencia` | Série mensal receita/despesas/margem |
| `GET /dre/exames-stats` | Receita por modalidade (RM, TC, US…) |
| `GET /dre/medicos` | Receita por médico solicitante |
| `GET /dre/receitas-analitico` | Lançamentos individuais de receita |
| `GET /notas-despesa` | Despesas por nota fiscal |
| `GET /schema/views` | Lista views disponíveis no Naja |

Todos os endpoints exigem header `x-api-key`.

## Fontes de dados

| Dado | View Naja | Campo valor | Filtro de data |
|---|---|---|---|
| Receitas | `vw_ConsultaItensDasContas` | `VlTotServ` | `Data do Atendimento` |
| Despesas | `vw_ContasComClassificacao` | `ValClassificacao` | `DataNota` |

> ⚠️ Receitas usam data do atendimento; despesas usam data da nota fiscal.  
> Divergências entre meses podem ocorrer por essa diferença de base temporal.

## Cache

A API usa cache em memória com TTL de 10 minutos. Para forçar dados frescos,
reinicie o processo uvicorn.

## Exportar Snapshot

O dashboard tem botão **📸 Exportar Snapshot** que gera um HTML autocontido
com todos os dados embutidos — pode ser enviado por e-mail ou salvo localmente
sem precisar de acesso ao ngrok/Naja.
