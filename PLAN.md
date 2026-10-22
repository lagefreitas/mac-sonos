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

## Sprint 4: Volume, mute e grupos
- Slider por cômodo, mute, volume do grupo, agrupar/desagrupar (`join`/`unjoin`), mover áudio, modo festa (`partymode`), volume em rampa (`ramp_to_volume`).
- Com grupo, o stream do Mac passa a ser do coordenador.
- **Pronto quando:** o Mac toca no Office e na TV Room juntos, com volumes independentes.

## Sprint 5: Som e entradas
- EQ (graves, agudos, loudness); na Playbar: modo noite, realce de diálogo, sub e surround se existirem.
- Botão "áudio da TV" (`switch_to_tv`) e line-in onde houver. A UI esconde o que o aparelho não suporta.

## Sprint 6: Favoritos, playlists, rádio, fila
- `get_sonos_favorites` e `get_sonos_playlists` com tocar em um clique; fila atual; rádio por URL.
- **Risco:** favoritos de serviços exigem DIDL correto; alguns só tocam via nuvem. Testar favorito por favorito.

## Sprint 7: Sleep timer e alarmes
- Sleep timer por grupo (`get_sleep_timer`/`set_sleep_timer`); alarmes via `soco.alarms`.
- Alarme roda no próprio Sonos, mas não serve para o Mac como fonte.

## Sprint 8: Cenas e atalhos
- Cenas em JSON (cômodos, volumes, EQ, fonte, stream do Mac). Teclas de mídia e atalhos globais (exigem permissão de Acessibilidade); modo liga/desliga.

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

## Ordem
**0 → 1 → 2** é crítica: o empacotamento define se a Rede Local funciona para os eventos do sprint 3. Depois, 3 a 8 podem ser reordenados pelo que for mais usado.

## Decisões em aberto
1. Painel (sprint 9) ou só menu?
2. Nuvem (sprint 10) ou os favoritos bastam?
3. Manter o nome `mac-sonos`?
4. Quais recursos faltam no app oficial? Esses sobem de prioridade.
