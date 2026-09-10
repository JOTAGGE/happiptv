# Segurança do Happiptv

## Relato responsável

Não publique vulnerabilidades com credenciais, URLs privadas ou dados pessoais em issues. Use **Security → Report a vulnerability** no GitHub para abrir um Security Advisory privado com versão afetada, impacto e passos mínimos para reprodução.

## Modelo de ameaça

O Happiptv é um cliente desktop local-first. A versão atual protege contra os riscos mais prováveis no dispositivo do usuário:

- senhas Xtream e URLs M3U remotas ficam no cofre de credenciais do sistema;
- cache de catálogo e histórico de downloads não persistem URLs autenticadas;
- mensagens de erro removem URLs que possam conter tokens ou credenciais;
- PIN parental é derivado com PBKDF2-SHA256 e salt aleatório;
- exclusões de mídia são recusadas fora da pasta de downloads configurada;
- respostas de catálogo, playlists e backups têm limites de tamanho;
- importação de backup valida estrutura e quantidade de registros;
- chamadas HTTPS usam validação TLS padrão, sem `verify=False`;
- processos externos são iniciados sem shell e com argumentos separados.

## Limites importantes

- Um aplicativo distribuído ao usuário não pode ser tornado absolutamente “incopiável”. Obfuscação só aumenta o custo de engenharia reversa.
- O controle parental é uma conveniência local, não uma fronteira contra um administrador da máquina.
- Servidores IPTV, playlists, imagens, legendas e arquivos de vídeo são conteúdo não confiável. Mantenha PySide6/Qt, ffmpeg e codecs atualizados.
- HTTP continua disponível por compatibilidade com provedores. Prefira HTTPS; em HTTP, credenciais podem ser observadas na rede.
- O aplicativo não inclui DRM, telemetria, autenticação própria nem um servidor de licenças.

## Checklist de publicação

1. Execute `python -m pytest -q`, `bandit -r app -ll` e `pip-audit -r requirements.txt`.
2. Gere o executável em um ambiente limpo e reproduzível.
3. Assine o `.exe` com um certificado de code signing e timestamp confiável usando `python scripts/sign_exe.py --exe dist/Happiptv.exe --cert <cert.pfx>`.
4. Publique SHA-256 de cada artefato e verifique-o antes do upload.
5. Use GitHub Releases e proteja a branch principal com revisão e CI obrigatório.
6. Ative Dependabot, secret scanning, push protection e CodeQL nas configurações do repositório.
7. Nunca inclua `config.json`, caches, históricos, playlists, dumps ou credenciais em releases.
8. Se optar por licença comercial, faça a validação no servidor com tokens curtos e revogáveis. Não coloque uma chave-mestra no executável.
9. A licença de software foi definida explicitamente no arquivo [LICENSE](LICENSE) (Blue Lab EULA Comercial).


## Resposta a incidentes

Se uma credencial aparecer em commit, log ou release, remova o artefato público e **revogue/rotacione a credencial imediatamente**. Reescrever o histórico Git não invalida um segredo que já foi copiado.

