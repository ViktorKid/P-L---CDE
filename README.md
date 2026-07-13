# DRE Financeiro — Clínica CDE

Dashboard financeiro em tempo real integrado ao ERP Naja (SQL Server).  
Desenvolvido para a controladoria da Clínica CDE Campinas.

---

## Arquitetura

```
Naja ERP (SQL Server 2017)  ←  192.168.0.5
        │ pyodbc
        ▼
api/main.py  (FastAPI · porta 8000 · cache TTL 10 min)
        │ HTTP + x-api-key
        ▼
ngrok tunnel  →  dashboard_cde_v3.html  (browser)
```

---

## Estrutura do projeto

```
C:\CDE-DRE\                          ← repositório git (fora do OneDrive)
├── api/
│   ├── main.py                      ← API FastAPI (1 000+ linhas)
│   ├── .env                         ← credenciais reais (gitignored)
│   └── .env.example                 ← template para novos ambientes
├── dashboard/
│   └── dashboard_cde_v3.html        ← dashboard completo (HTML/JS puro)
├── scripts/
│   ├── diagnostico_contagem.py      ← diagnóstico de contagem de exames
│   └── ver_outros.py                ← análise de itens "Outros"
├── commit.bat                       ← commita e sobe para GitHub (1 clique)
└── README.md

Arquivos de trabalho (OneDrive — NÃO inicializar git aqui):
  Acesso Claude SQL\main.py          ← API em produção (Claude edita aqui)
  DRE\dashboard_cde_v3.html          ← dashboard em produção (Claude edita aqui)
```

---

## Configuração inicial

### 1. Instalar dependências Python

```bash
pip install fastapi uvicorn pyodbc pandas python-dotenv
```

### 2. Credenciais (.env)

```bash
cd C:\CDE-DRE\api
copy .env.example .env
# Editar .env com senha real
```

Conteúdo do `.env`:
```
DB_SERVER=192.168.0.5
DB_PORT=1433
DB_NAME=Naja
DB_USER=Gisley_maver
DB_PASSWORD=SENHA_AQUI
API_KEY=cde2025
```

### 3. Iniciar API

```bat
cd /d "C:\Users\victor.ferreira\Clinica CDE\Financeiro - Documentos\03. CONTROLADORIA\31. IMPLEMENTAÇÕES E AUTOMAÇÕES\Acesso Claude SQL"
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 4. Expor via ngrok

```bash
ngrok http 8000
```

URL atual de produção: `https://evil-outline-ample.ngrok-free.dev`  
Header obrigatório: `x-api-key: cde2025`

Ao trocar a URL ngrok, atualizar linha no dashboard:
```javascript
const API = "https://NOVO-SUBDOMINIO.ngrok-free.dev";
```

### 5. Abrir o dashboard

Abrir `DRE\dashboard_cde_v3.html` diretamente no browser (duplo clique).

---

## Funcionalidades do dashboard

### Aba DRE
- Tabela DRE estruturada: Receita Bruta → Abatimentos → Receita Líquida → COGS → Lucro Bruto → SG&A → EBITDA → Depreciação → EBIT → Resultado Financeiro → IR/CSLL → Lucro Líquido
- Ordem de rubricas conforme Forecast Excel 2026
- **KPI cards de margem**: Margem Bruta %, EBITDA %, Margem Líquida % — com comparação vs orçado e variação em pp
- **Real vs Orçado 2026**: tabela comparativa com Var R$ e Var % — aparece automaticamente para períodos de 2026
- Gráficos de série mensal: Receita vs Despesas, Margem % mensal

### Aba Exames
- Receita por modalidade: RM, TC, US, MG, DO, Biópsias, Mamotomia, RX
- Contagem de atendimentos e ticket médio
- Drill-down por modalidade e produto

### Aba Receitas Analítico
- Lançamentos individuais com filtros de convênio, médico e busca
- Paginação (60 por página)

### Aba Despesas
- Despesas por categoria e convênio
- Percentual da receita

### Aba Médicos
- Receita por médico solicitante

### Exportar Snapshot
- Botão **📸 Exportar Snapshot** gera HTML autocontido com dados embutidos
- Pode ser enviado por e-mail sem acesso ao ngrok/Naja

---

## Endpoints da API

| Endpoint | Descrição |
|---|---|
| `GET /dre/resumo` | Resumo DRE completo do período |
| `GET /dre/tendencia` | Série mensal receita/despesas/margem |
| `GET /dre/exames-stats` | Receita e volume por modalidade |
| `GET /dre/medicos` | Receita por médico solicitante |
| `GET /dre/receitas-analitico` | Lançamentos individuais (paginado) |
| `GET /dre/empresas` | Lista de empresas/unidades disponíveis |
| `GET /notas-despesa` | Despesas por nota fiscal |
| `GET /notas-periodo` | Todas as notas de um período (para snapshot) |
| `GET /schema/views` | Views disponíveis no Naja |

Todos exigem header `x-api-key: cde2025`.

---

## Fontes de dados

| Dado | View Naja | Campo | Filtro de data |
|---|---|---|---|
| Receitas | `vw_ConsultaItensDasContas` | `VlTotServ` | `Data do Atendimento` |
| Despesas | `vw_ContasComClassificacao` | `ValClassificacao` | `DataNota` |

> ⚠️ **Divergência de base temporal**: receitas usam data do atendimento; despesas usam data da nota fiscal. Isso pode causar diferenças entre abas para o mesmo período — é esperado e está documentado em cada aba.

---

## Reclassificações de contas (Naja → DRE)

| Conta Naja | Nome | Comportamento padrão Naja | Tratamento no dashboard |
|---|---|---|---|
| 390204 | Aluguel de Bens Imóveis (Produção) | Cai em IR/CSLL (prefixo 39) | Reclassificado para COGS (prefixo 32, subgrupo 3203) |
| 390203 | Financiamento p/ Aquisição de Bens | Cai em IR/CSLL (prefixo 39) | **Excluído da DRE** (item de balanço, não de resultado) |

---

## Orçado 2026

Os valores do orçado estão embutidos no dashboard (`const FORECAST_2026`) — extraídos do Excel de Forecast. Para atualizar o orçado (ex: revisão orçamentária), editar o objeto `FORECAST_2026` em `dashboard_cde_v3.html` linha ~463.

---

## Cache

TTL de 10 minutos em memória. Reiniciar uvicorn para forçar dados frescos.

---

## Workflow de versionamento (Git)

O repositório Git fica em `C:\CDE-DRE\` (fora do OneDrive — necessário para o git funcionar).  
Os arquivos de trabalho ficam no OneDrive (editados pelo Claude).

**Para commitar mudanças**: dar duplo clique em `C:\CDE-DRE\commit.bat`  
O script copia os arquivos atualizados e faz push automaticamente.

```
Claude edita → OneDrive → commit.bat copia + push → GitHub
```
