# Plano: Mac-Sonos como central Sonos na barra de menus

## Princípios
- **Local primeiro:** SoCo, sem login. A nuvem só entra no último sprint.
- **Capacidades por aparelho:** a UI detecta o que cada player suporta, em vez de supor.
  (Play:1 não tem modo noite, realce de diálogo nem audio delay; a Playbar tem. Todos rodam S2, 86.10.)
- **Cada sprint termina em algo usável, testado no hardware e commitado.**
- **O recurso atual não pode regredir:** o stream Mac→Office, sem estalos, continua funcionando durante toda a refatoração.

## Arquitetura alvo
```
mac_sonos/
  core/      formats.py, stream.py (HTTP + captura), session.py, output.py (BlackHole/SwitchAudio)
  sonos/     discovery.py, player.py (wrapper + capacidades), events.py (subscribe), favorites.py, alarms.py
  ui/        menubar.py (rumps), panel/ (janela de controle)
  config.py  (JSON em ~/Library/Application Support/mac-sonos)
  controller.py  (estado + comandos, sem UI)
  cli.py     (list/devices/run/bench, mantidos)
```
A fronteira principal é o `Controller`, sem UI, que expõe estado e comandos. O CLI, o menu e o painel são clientes dele.

## Sprint 0: Fundação (FEITO)
- Separar o `mac_sonos.py` nos módulos acima. O CLI continua funcionando igual (`python -m mac_sonos ...`).
- Criar o `Controller` com uma thread de trabalho e uma fila de comandos, para que nada bloqueie a UI.
- Testes de fumaça para o que não precisa de hardware: config, comando do ffmpeg, formatos, máquina de estados do Controller.
- **Pronto quando:** `run Office --volume 15` toca sem estalos, igual ao HEAD anterior.
- **Risco:** quebrar o `dup2` do stdin ou o `OutputSwitch` ao mover o código.

## Sprint 1: Menu básico do streaming (FEITO)
- Ícone na barra de menus (`rumps`), com estados: parado, conectando, tocando e erro.
- Menu: ligar/desligar, cômodos marcáveis (vindos da descoberta; trocar reinicia a sessão), volume em passos por cômodo, formato num submenu avançado.
- Aviso claro quando o BlackHole não estiver carregado ou o volume do Mac estiver diferente de 100%.
- "Sair" restaura a saída de áudio do Mac. Persistir última escolha em JSON.
- **Pronto quando:** você nunca mais precisa abrir o terminal no dia a dia.
- **Risco:** o `rumps` roda no thread principal do AppKit; a sessão fica em outra thread, via fila do Controller.

## Sprint 2: Empacotamento e autostart de verdade (FEITO)
Vem cedo porque a permissão de Rede Local define se o resto funciona.
- `.app` com `Info.plist` próprio (`LSUIElement`, `NSLocalNetworkUsageDescription`, `NSBonjourServices`) via `py2app` ou bundle mínimo.
- Item de login via `SMAppService` (ou System Events), substituindo o truque do Terminal.
- Assinatura ad-hoc local.
- **Pronto quando:** um reboot sobe o app sozinho, ele pede Rede Local uma vez e toca.
- **Risco alto:** o `.app` precisa carregar PortAudio, ffmpeg e SoCo. Se o `py2app` incomodar, um launcher fino chamando o venv, com a permissão atribuída ao Python.

## Sprint 3: Agora tocando e transporte (FEITO)
- Assinar eventos (`subscribe`) de `avTransport` e `renderingControl`, com reconexão e fallback para polling.
- Faixa, artista, capa; play/pause, próxima, anterior, seek. Um bloco por grupo.
- **Pronto quando:** o estado do menu bate com o do app oficial, com atraso menor que 2 s.
- **Risco:** eventos exigem que o Mac aceite conexões de entrada do Sonos; validar depois do empacotamento.

## Sprint 4: Volume, mute e grupos (FEITO)
- Slider por cômodo, mute, volume do grupo, agrupar/desagrupar (`join`/`unjoin`), mover áudio, modo festa (`partymode`), volume em rampa (`ramp_to_volume`).
- Com grupo, o stream do Mac passa a ser do coordenador.
- Feito: +5/−5 e mudo por cômodo, volume do grupo, juntar/sair, modo festa, separar todos, reconciliação do estado do
  stream ("Interrompido no Sonos"). Slider fica para depois (menu de passos, sem como validar o NSSlider daqui).
- Fora do escopo por ora: "mover áudio" (comportamento ao tirar o coordenador não validado sem tocar música) e
  `ramp_to_volume` (a rampa nativa é lenta, 1,25 passo/s).
- **Pronto quando:** o Mac toca no Office e na TV Room juntos, com volumes independentes.

## Sprint 5: Som e entradas (FEITO)
- EQ (graves, agudos, loudness); na Playbar: modo noite, realce de diálogo, sub e surround se existirem.
- Botão "áudio da TV" (`switch_to_tv`) e line-in onde houver. A UI esconde o que o aparelho não suporta.
- Feito: EQ, modo noite, realce de diálogo, surround e TV nas soundbars. Line-in ficou de fora (a API não revela quais
  aparelhos têm entrada; nenhum dos seus tem). O botão TV foi coberto por teste unitário, não executado no aparelho real.

## Sprint 6: Favoritos, playlists, rádio, fila (FEITO)
- `get_sonos_favorites` e `get_sonos_playlists` com tocar em um clique; fila atual; rádio por URL.
- **Risco:** favoritos de serviços exigem DIDL correto; alguns só tocam via nuvem. Testar favorito por favorito.
- Feito: 22 dos 26 favoritos da casa são tocáveis localmente (18 rádios, 3 faixas, 1 playlist); os 4 sem URI (hub do
  Sonos Radio, artista) ficam de fora. Testado nos aparelhos, com o cômodo mudo: rádio, faixa, playlist do Spotify,
  playlist do Sonos, URL, e play/remover/limpar na fila. Limites: fila lida até 100 faixas; URLs de playlist (.m3u/.pls)
  não são suportadas; "Descubra o Sonos Radio" e artistas só pela nuvem (sprint 10).

## Sprint 7: Sleep timer e alarmes (FEITO)
- Sleep timer por grupo (`get_sleep_timer`/`set_sleep_timer`); alarmes via `soco.alarms`.
- Alarme roda no próprio Sonos, mas não serve para o Mac como fonte.
- Feito: soneca nativa por grupo; para o stream do Mac, timer do app com fade de 30 s, parada limpa e volume restaurado
  (`Controller.stop(volume=)`); alarmes (listar, criar, ativar, horário, repetição, volume, remover). Não feito: dias da
  semana personalizados na criação (aparecem se criados no app do Sonos), som de alarme que não seja o toque padrão, e
  persistir o timer do Mac entre reinícios do app.

## Sprint 8: Cenas e atalhos (FEITO, teclas de mídia sem teste com teclas reais)
- Cenas em JSON (cômodos, volumes, EQ, fonte, stream do Mac). Teclas de mídia e atalhos globais (exigem permissão de Acessibilidade); modo liga/desliga.
- Feito: cenas (capturar, plano puro, executor com esperas, menu, JSON editável); teclas de mídia via event tap, opt-in,
  com decisão testável. Escolha de desenho: volume só é desviado enquanto o Mac transmite (o volume do Mac é o nível do
  sinal), e play/próxima/anterior só agem sobre grupos com algo real tocando, senão vão para o macOS. Não feito:
  atalhos globais com combinações próprias, e teste das teclas físicas (a permissão de Acessibilidade é do usuário).
  `ungroup_all` e regrupamento por cena foram testados só com dublês no nível de cena (os comandos de grupo individuais
  foram testados no sprint 4).

## Sprint 9 (opcional): Painel
- Janela única (`pywebview`). Só se o menu ficar apertado; decidir depois do sprint 6.

## Sprint 10 (opcional): Nuvem e busca
- Integração no Sonos Developer, OAuth local, token no Keychain; busca e `play_artist`/`play_album`.
- Só se os favoritos não cobrirem o que falta.

## Sprint 11 (opcional): Captura sem BlackHole (Core Audio process tap)
Motivação: hoje a captura abre o BlackHole como dispositivo de entrada, então o macOS mostra o indicador
laranja de microfone enquanto há stream. Um process tap captura o áudio do sistema direto.
- Protótipo pequeno primeiro: confirmar que `CATapDescription` / `AudioHardwareCreateProcessTap` funcionam via PyObjC
  (macOS 14.2+) e que dá para silenciar a saída local (mute behavior) enquanto o Sonos toca.
- Ganhos: some a dependência do BlackHole, do volume do Mac a 100% e da troca de saída; o app deixa de mexer no
  dispositivo de saída.
- Custos: pede a permissão "Gravação de áudio do sistema" e mostra o indicador roxo do Control Center, não o laranja;
  API pouco documentada.
- Alternativa descartada por ora: ScreenCaptureKit (áudio apenas), que exige Gravação de Tela.
- Se o protótipo falhar, manter o BlackHole e documentar o indicador como inevitável.

**Resultado do protótipo (branch `sprint-11-process-tap`, macOS 27, PyObjC 12.2):** VIÁVEL.
- Funciona: tap global estéreo (`CATapDescription` + `AudioHardwareCreateProcessTap`) exposto por um dispositivo agregado,
  lido por IOProc (ctypes) ou pelo PortAudio, dentro do pacote assinado com identidade própria. Captura contínua: 47.923
  quadros/s medidos em 20 s contra 48.000 esperados. Com `CATapMuted` a saída local fica muda enquanto o tap ouve, então
  o Mac fica em silêncio sem trocar a saída (adeus BlackHole, `SwitchAudioSource` e volume do Mac a 100%).
- Armadilhas: as constantes `kAudioAggregateDevice*Key` vêm como `bytes` do PyObjC e derrubam o CoreAudio (converter para
  `str`); tap privado exige agregado privado (senão 0 buffers); o agregado só expõe entradas ao PortAudio com tap e
  agregado públicos; o identificador do IOProc não passa pelo PyObjC (usar ctypes).
- Permissão: o macOS segura o áudio (0 chamadas) até o app ter "Gravação de áudio do sistema"; o estado inicial é
  "indeterminado" e o pedido de permissão só tem API privada (`TCCAccessRequest`/`TCCAccessPreflight`, TCC.framework).
  Depois de concedida, tudo flui. Exige `NSAudioCaptureUsageDescription` (já traduzida em pt/en/es/fr nesta branch).
- Trabalho para integrar (não feito): backend de captura selecionável (BlackHole ou tap) no `Controller`/`stream.py`;
  remover a troca de saída quando o tap estiver em uso; fluxo de permissão no menu (estado, pedido, orientação);
  tap criado só enquanto há stream (como a captura de hoje, por conexão); limpeza do agregado ao sair ou cair (o tap
  some com o processo, mas um agregado público não); testes com dublês; decidir o que fazer se a API privada de
  permissão mudar (sem ela o usuário adiciona o app à mão em Ajustes).
- Não verificado: o indicador exato que o macOS mostra durante a captura.

## Sprint 12: Localização da interface (en, es, fr; pt-BR é a base) — FEITO
Hoje todos os textos da UI estão em português, espalhados em `ui/menubar.py` e `ui/model.py`.
- Centralizar as strings: um catálogo por idioma (`mac_sonos/ui/locales/{pt,en,es,fr}.json` ou `gettext`) e uma
  função `t("chave", **vars)`; nada de texto de UI fora dele. Plurais e variáveis (`{rooms}`, `{pct}`) tratados no
  catálogo, não por concatenação.
- Idioma pelo sistema (`NSLocale.preferredLanguages`), com opção "Idioma" no menu (Automático, Português, English,
  Español, Français) salva na config; fallback para inglês e depois para chaves ausentes.
- Cobrir: itens de menu, estados ("Parado", "Interrompido no Sonos"...), avisos, diálogos (`rumps.alert`),
  descrições de permissão do `Info.plist` (`InfoPlist.strings` por idioma: Microfone e Rede Local) e README.
- Testes: todo idioma tem exatamente as mesmas chaves e as mesmas variáveis do pt (teste automático); nenhuma
  string de UI fora do catálogo (teste que varre `ui/`).
- Feito: `i18n.py` (`t`, `tn`, fallback en→pt, detecção do idioma do sistema), `locales/pt.json` com todos os textos da
  UI, erros localizáveis, menu "Idioma" (escondido enquanto só houver pt), testes de paridade/uso/sem texto no código.
  Traduções en/es/fr (122 chaves cada, terminologia dos apps da Sonos), `InfoPlist.strings` por idioma no pacote,
  larguras revisadas (maior item: 37 caracteres em es/fr contra 33 em pt) e testes de qualidade (valores iguais ao pt só
  se listados de propósito, sem vazamento de chave crua em nenhum menu de nenhum idioma). O README fica só em inglês:
  é documentação para quem desenvolve, não parte da interface. Observação: "Automático" segue o *primeiro* idioma do
  macOS; com inglês antes de português, o menu abre em inglês.
- Recomendação: antecipar a infraestrutura (catálogo + `t()`) antes de crescer mais a UI, com o pt como único idioma;
  traduzir en/es/fr no fim. Cada sprint seguinte já nasce usando `t()`.
- Risco: o menu muda de largura com o idioma (es/fr são mais longos); revisar textos de barra e abreviações.

## Ordem
**0 → 1 → 2** é crítica: o empacotamento define se a Rede Local funciona para os eventos do sprint 3. Depois, 3 a 8 podem ser reordenados pelo que for mais usado.

## Decisões em aberto
1. Painel (sprint 9) ou só menu?
2. Nuvem (sprint 10) ou os favoritos bastam?
3. Manter o nome `mac-sonos`?
4. Quais recursos faltam no app oficial? Esses sobem de prioridade.
