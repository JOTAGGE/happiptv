# HAPPITV — BLUE LAB EXPERIMENTAL STREAMING PLATFORM

<div align="center">
  <h3>⚡ Happitv: Plataforma Completa de Streaming & Biblioteca Offline</h3>
  <p>Design de engenharia e tecnologia experimental inspirado na <strong>Blue Lab</strong> (<a href="https://bluelabhub.vercel.app">bluelabhub.vercel.app</a>).</p>
</div>

---

## 🔬 Identidade Visual & Design System

Happitv adota a estética brutalista, laboratorial e de alto desempenho da **Blue Lab**:
- Paleta **Dark Ink** (`#0a0a0a`) com destaque em **Azul Elétrico** (`#1749e8` / `#2258ff`).
- Tipografia suíça com kickers e indicadores monoespaçados (`DM Mono` / `Consolas`).
- Ponto de status pulsante (`● Live Dot`) para streams e diagnóstico de latência de servidor.
- Layouts de alta densidade e cartões com bordas técnicas (`1px solid #1c2230`).
- Home editorial com hero de tipografia ampla, trilhos horizontais de conteúdo e catálogos em grid com capas carregadas sob demanda.

---

## 🚀 Funcionalidades Completas

### 1. Player Multimídia Completo (Ao Vivo, Filmes e Séries)
- **Controles Universais**: Play/pause, seek scrubber, volume com atalho de mudo, modo tela cheia (`F`).
- **Velocidade de Reprodução**: Alternância dinâmica entre 0.5x, 0.75x, 1.0x, 1.25x, 1.5x e 2.0x.
- **Áudio e Legendas**: Seletor de faixas de áudio e legendas embutidas em tempo real.
- **Próximo Episódio Automático**: Transição contínua com countdown inteligente ao final de episódios de séries.
- **Memória de Posição**: Salva automaticamente onde você parou para continuar depois.
- **Retorno ao Último Canal**: Botão e atalho rápido (`L`) para voltar instantaneamente ao canal ao vivo anterior.
- **Modo Cinema (`C`)**: Oculta barras de navegação e cabeçalho, deixando apenas a tela imersiva com controles auto-ocultáveis.
- **Stream Diagnostics HUD (`D`)**: Overlay flutuante com telemetria técnica: resolução, FPS, taxa de bits estimada, codecs de áudio/vídeo e latência da fonte em milissegundos.

### 2. Biblioteca Real de Streaming
- **Home (Início)**:
  - Hero Card de destaque com gradiente Blue Lab.
  - Fileira "Continuar Assistindo" com barra de progresso individual e retomada instantânea com 1 clique.
  - Fileira "Favoritos".
  - Fileira "Minha Lista" (Watchlist separada dos favoritos).
- **Séries com Arquitetura Real**:
  - Agrupamento limpo por Temporadas (Season 1, Season 2, etc.).
  - Lista de episódios com duração, sinopse, progresso e marcação de assistido (✓).
  - Botão de "Baixar Temporada Completa" para enfileirar todos os episódios de uma só vez.
- **Página de Detalhes Estilo Streaming**:
  - Pôster em alta definição, classificação indicativa, ano, notas, gêneros e sinopse completa.

### 3. Live TV com Guia EPG / XMLTV
- Guia interativo "Agora / Próximo" com horários e descrição detalhada dos programas.
- Navegação instantânea por categorias e canais favoritos.
- Preview em tempo real com indicador de latência.

### 4. Busca Global Unificada (`Ctrl+F`)
- Pesquisa simultânea e em tempo real em Canais Ao Vivo, Filmes, Séries e Biblioteca Offline.
- Tolerância avançada a acentos, palavras fora de ordem e pequenos erros de digitação.

### 5. Gerenciador de Downloads Avançado
- Fila com pausa, continuação (HTTP `Range`) e **retry automático** (até 3 tentativas com backoff progressivo).
- Velocidade em tempo real (MB/s), tamanho restante e cálculo de tempo estimado (ETA).
- Limite configurável de downloads simultâneos (1 a 6 conexões).
- Exclusão de arquivos baixados do disco rígido diretamente pelo app.

### 6. Biblioteca Offline (Identidade Forte Happitv)
- Aba exclusiva dedicada apenas ao conteúdo já baixado e disponível no disco.
- Reprodução 100% offline sem depender de conexão com internet ou servidores IPTV.
- Exibição de espaço ocupado no disco rígido.

### 7. Multi-Account & Smart Refresh
- Cadastro simultâneo de múltiplas fontes **Xtream Codes** e listas **M3U / M3U8**.
- Troca de fonte instantânea ou modo **Biblioteca Unificada** (agregação de todas as contas ativas).
- **Smart Refresh**: Atualize ou sincronize suas playlists sem perder nenhum favorito, item da lista ou progresso de reprodução.
- **Health Check**: Medição de ping/latência em ms, validade da conta (`exp_date`), conexões ativas e status do servidor.

### 8. Perfis de Usuário & Controle Parental
- Múltiplos perfis de usuário (Principal, Visitante, Perfil Infantil).
- Listas, progresso e histórico completamente separados por perfil.
- Perfil Infantil bloqueia automaticamente categorias adultas.
- Controle Parental protegido por PIN de 4 dígitos configurável.
- Ocultação personalizada de categorias indesejadas em todo o app.

### 9. Privacidade Local-First & Backup
- Credenciais e histórico permanecem exclusivamente na máquina local.
- Senhas Xtream, URLs M3U privadas e endereços autenticados de downloads ficam no cofre de credenciais do sistema e não no JSON/cache.
- **Exportar Backup JSON**: Salva contas, perfis, listas e configurações (sem senhas por padrão).
- **Importar Backup JSON**: Restauração com 1 clique.

### 10. Segurança de distribuição

- CI executa testes, análise estática com Bandit e auditoria de dependências com `pip-audit`.
- Dependabot acompanha dependências Python e GitHub Actions.
- Cache não grava URLs que contenham credenciais; mensagens de erro sensíveis são redigidas.
- Exclusão de arquivos é limitada à pasta de downloads configurada.
- Consulte [SECURITY.md](SECURITY.md) antes de publicar uma release. Assinatura do executável e proteção de branch ainda precisam ser configuradas pelo mantenedor.

---

## ⌨️ Atalhos de Teclado Desktop

| Tecla | Ação |
|---|---|
| `Espaço` | Reproduzir / Pausar |
| `F` | Alternar Tela Cheia |
| `M` | Silenciar / Ativar Som (Mute) |
| `←` / `→` | Pular -10s / +10s |
| `D` | Alternar Stream Diagnostics HUD |
| `C` | Alternar Modo Cinema |
| `Ctrl+F` | Focar na Busca Global |
| `Esc` | Sair da Tela Cheia |

---

## 📦 Execução e Testes

Requer Python 3.11+.

```powershell
# Ativar ambiente virtual
.\.venv\Scripts\Activate.ps1

# Instalar dependências
pip install -r requirements.txt

# Iniciar o Happitv
python main.py

# Rodar a suíte de testes
python -m pytest
```

---

<div align="center">
  <sub>BLUE LAB © 2026 — EXPLORAR / CONSTRUIR / TESTAR / APRENDER</sub>
</div>
