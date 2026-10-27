# OmaDot

Asistente de voz para Omarchy. Su mascota, **Omi** (el logo de Omarchy con ojos), vive en la barra.
Pulsa `Super` dos veces para abrir el micrófono, hablas, y Omi responde en voz; una tarjeta bajo la barra muestra lo
que dijiste, la respuesta mientras llega y lo que la respuesta trae: archivos que puedes arrastrar a
cualquier app, un borrador que no se envía sin tu OK, un cambio de código, un plan, un recordatorio.
Suelta un archivo sobre Omi y él lo guarda donde toca.

## Cómo funciona

```
mic ─▶ PipeWire echo-cancel ─▶ Silero VAD + Smart Turn v3 ─▶ faster-whisper (large-v3-turbo, CUDA)
                                                                       │
altavoz ◀─ Kokoro (servidor local :8880, por frase) ◀─ Hermes Agent, perfil `omadot` (/v1/runs)
```

- `src/omadot/__init__.py`: el daemon (pipeline de Pipecat), los comandos (`toggle`, `retry`, `set`,
  `status` y los del socket) y el arranque de la tarjeta.
- `src/omadot/ipc.py`: el socket de comandos (`$XDG_RUNTIME_DIR/omadot/sock`, una línea JSON por
  petición) por el que llegan `drop`, `ask`, `approve`, `progress`… desde la barra, la tarjeta o la
  terminal.
- `src/omadot/payload.py`: el protocolo de respuesta. Hermes termina con una línea `@@omi {json}`
  (archivos, url, borrador, diff, recordatorio, enfoque, pregunta…) que nunca se lee en voz alta y que
  la tarjeta dibuja. Las etiquetas `MEDIA:/ruta` de Hermes y las rutas absolutas existentes también
  se vuelven archivos arrastrables.
- `src/omadot/fastlane.py`: el carril rápido. `omp --mode rpc` (oh-my-pi) mantiene a Claude Haiku listo;
  cada turno corre en paralelo por Haiku y por Hermes y gana el primero que responde. Haiku solo contesta
  lo que sabe sin herramientas (saludos, hora, cuentas, traducciones); todo lo demás lo escala a Hermes.
- `src/omadot/context.py`: lo que Omi ve además de tus palabras: la ventana activa (`hyprctl`), una
  captura de ella (`grim`) cuando dices «esto», el historial del portapapeles de Omarchy cuando dices
  «copié», el texto seleccionado (`wl-paste -p`) cuando pides reescribir, el reproductor (`playerctl`).
- `src/omadot/commands.py`: qué hace el daemon con cada comando (un archivo soltado o un botón son un
  turno; `approve` responde al permiso que pidió Hermes), con cada tarjeta (agenda el recordatorio,
  arranca el enfoque) y con cada segundo (suenan recordatorios, la píldora cuenta, la tarjeta se
  cierra sola).
- `src/omadot/services.py`: recordatorios persistentes (`~/.local/state/omadot/reminders.json`),
  temporizador de enfoque y progreso de tareas largas.
- `src/omadot/hermes.py`: cliente de la API de runs de Hermes (streaming SSE, `stop` para interrumpir).
- `src/omadot/agent.py`: pasa cada turno a Hermes; si el primer token tarda más de 0,6 s dice una
  frase de relleno ("A ver.") para que Omi reaccione en ~0,8 s. Si el gateway no responde, reenvía
  el turno cada 5 s (hasta 6 veces). Si Hermes falla o no contesta nada, Omi lo dice en voz alta y
  queda en estado `error`.
- `src/omadot/kokoro.py`: voz con el servidor Kokoro local; limpia markdown/links/emoji antes.
- `src/omadot/settings.py`: ajustes en `~/.config/omadot/settings.json` (voz, velocidad, responder
  en voz, transcripción, dormir).
- `src/omadot/state.py`: publica `{"state","you","omi","card","pill","buttons"}` en
  `$XDG_RUNTIME_DIR/omadot/state` (`idle`, `muted`, `sleeping`, `listening`, `thinking`, `working`,
  `speaking`, `done`, `confused`, `error`, `catch`, `notify`; sin archivo = apagado). `card` es lo
  que la respuesta trae, `pill` el texto junto a Omi en la barra y `buttons` los botones de la tarjeta
  con los argumentos de `omadot` que ejecutan.
- `src/omadot/omarchy-plugin/`: plugin `ale.omadot` de la barra de Omarchy: Omi animado (clic = abrir/cerrar
  mic, soltar un archivo = guardarlo), la píldora (enfoque, progreso) y el panel de ajustes (clic
  derecho). `OmadotBin.js` dice qué `omadot` ejecuta; `omadot setup plugin` lo escribe.
- `src/omadot/card/shell.qml`: la tarjeta de invocación, una instancia propia de Quickshell que lanza el daemon
  (le pasa su binario en `OMADOT_BIN`). Dibuja cada tipo de contenido de la respuesta, acepta archivos
  soltados y crece hasta el 70 % de la pantalla (luego hace scroll).
- `src/omadot/install.py`: `omadot setup …`, que instala cada pieza apuntando al binario que lo ejecuta: el
  plugin, los servicios de systemd de usuario, la cancelación de eco, los servidores de voz y el perfil de
  Hermes.
- `src/omadot/speech/`: los servidores de voz OpenAI-compatibles, cada uno en su propio venv: Kokoro
  (`kokoro_server.py` + `lexicon.json` de pronunciaciones) y OmniVoice/Qwen3-TTS (`tts_server.py`).
- `src/omadot/pipewire/omadot-echo-cancel.conf`: el módulo de cancelación de eco de PipeWire.

## Requisitos

- **Omarchy** (Hyprland + su barra de Quickshell) con PipeWire. Sin Omarchy no hay barra ni tarjeta.
- **GPU NVIDIA con driver para CUDA 12.** faster-whisper `large-v3-turbo` y Kokoro corren en la GPU; aquí
  (RTX 5070 Ti) el daemon ocupa ~2,3 GB de VRAM y Kokoro ~0,9 GB. No hay modo CPU.
- **Solo español.** Whisper transcribe en español fijo y las voces de Kokoro son las tres en español.
- [uv](https://docs.astral.sh/uv/). Python 3.12 lo baja uv; cuBLAS/cuDNN 12 vienen como wheels.
- **Disco:** ~3 GB OmaDot, ~7 GB el venv de Kokoro (PyTorch con CUDA) y ~0,3 GB el modelo.
- [Hermes Agent](https://hermes-agent.nousresearch.com) instalado, con un proveedor de modelos y sus
  credenciales (las tuyas: `hermes setup` o `hermes model`). OmaDot habla con su gateway en `127.0.0.1:8642`.
- Opcional: [oh-my-pi](https://omp.sh) (`omp`) autenticado con una cuenta de Claude, para el carril rápido
  (`omp usage` muestra las cuentas; `omadot --fast-profile NOMBRE` elige un perfil de omp). Viene apagado:
  se enciende con `omadot set fast on` (o solo para una corrida, `omadot --fast`); si `omp` no está o no
  arranca, todos los turnos van a Hermes.
- Opcional: OmniVoice como segundo motor de voz (`omadot setup omni`, puerto 8882, ~2,5 GB de VRAM).

## Instalación

```bash
uv tool install --python 3.12 git+https://github.com/AlejandroFloresArroyo/omadot
```

Deja `omadot` en `~/.local/bin`. Luego, cada pieza con su comando (todos se pueden repetir):

```bash
omadot setup echo-cancel   # ~/.config/pipewire/pipewire.conf.d; luego: systemctl --user restart pipewire pipewire-pulse
omadot setup kokoro        # venv propio con PyTorch CUDA, descarga Kokoro-82M, servicio omadot-tts-kokoro en :8880
omadot setup hermes        # perfil omadot del gateway (--dry-run muestra los comandos sin correrlos)
omadot setup plugin        # copia el plugin a ~/.config/omarchy/plugins/ale.omadot y pone a Omi en la barra
omadot setup service       # servicio omadot.service, habilitado y arrancado
omadot setup binds         # imprime los atajos; pégalos en ~/.config/hypr/bindings.lua
```

`omadot setup hermes` clona tu perfil `default` (y con él tus credenciales) como `omadot`, le da su propia
`API_SERVER_KEY`, enciende el API server del gateway si estaba apagado y aplica los ajustes de la fila
«Perfil de Hermes» de abajo. No toca el modelo, el `SOUL.md` ni los MCP del perfil. Si te lo pide, elige el
modelo con `hermes -p omadot model`; después `hermes gateway restart` (la primera vez,
`hermes gateway install`) para que el gateway sirva `/p/omadot/`.

`omadot setup binds` imprime algo así (con la ruta de tu instalación):

```lua
o.bind("SUPER + Super_L", "OmaDot", "/home/tú/.local/share/uv/tools/omadot/bin/omadot tap", { release = true })
o.bind("SUPER + SHIFT + PERIOD", "OmaDot: micrófono", "/home/tú/.local/share/uv/tools/omadot/bin/omadot mute")
```

Cada vez que sueltas `Super` sin otra tecla, Hyprland corre `omadot tap`; dos en menos de 0,4 s abren o
cierran el micrófono. `Super` con otra tecla (`Super + 2`, o una sin atajo) no cuenta. El bind lleva
`SUPER +` delante: al soltar `Super_L` el modificador sigue activo y `"Super_L"` a secas nunca dispara.

`--no-enable` escribe los archivos sin habilitar ni arrancar nada (ni tocar la barra en marcha).

**Actualizar:** `uv tool upgrade omadot` y `omadot setup plugin` (el plugin es una copia).

**Quitar:** `systemctl --user disable --now omadot omadot-tts-kokoro`, borra `~/.config/systemd/user/omadot*.service`,
`~/.config/omarchy/plugins/ale.omadot`, `~/.config/pipewire/pipewire.conf.d/omadot-echo-cancel.conf` y
`~/.local/share/omadot`; luego `uv tool uninstall omadot`. El perfil de Hermes se borra con
`hermes profile delete omadot`.

**Desde un clon (desarrollo):** `uv sync` y los mismos comandos con `uv run omadot setup …`: el servicio y el
plugin apuntan a `.venv/bin/omadot` del clon y la tarjeta se lee de `src/omadot/card` tal cual. El plugin es
una copia: tras editar su QML, otra vez `uv run omadot setup plugin`.

**Si venías de la instalación anterior** (`systemctl --user link …/contrib/omadot.service` y el symlink
`ale.omadot → …/omarchy-plugin`): esos archivos ya no existen. En el clon, `uv sync`, `uv run omadot setup plugin`
y `uv run omadot setup service` reemplazan los symlinks por archivos; si usabas OmniVoice,
`uv run omadot setup omni`. Si ya tienes tu propio Kokoro en `:8880`, no corras `omadot setup kokoro`.

## Uso

- `Super` dos veces (o clic en Omi en la barra): abre/cierra el micrófono. Con el mic abierto, habla cuando quieras;
  hablarle mientras responde lo interrumpe.
- Clic derecho en Omi: ajustes. Voz (Dora, Alex, Santa) y velocidad (0,8–1,4×), responder en voz o
  solo por escrito en la tarjeta, mostrar la transcripción y dormir tras 10 min. Cada cambio se
  aplica al momento. El panel también muestra si Hermes está conectado, el perfil y el modelo, y el
  atajo actual.
- Si nadie habla en 10 min con el micrófono abierto, Omi se duerme: cierra el micrófono y en la barra
  se le apaga el logo. `Super` dos veces lo despierta.
- Si Whisper no saca texto de lo que oyó (una tos, ruido), Omi no llama a Hermes y la tarjeta dice
  "No te entendí.".
- Si el gateway de Hermes no responde, Omi lo dice una vez y reenvía la pregunta cada 5 s durante
  ~30 s; la tarjeta muestra "Reintento n de 6", **Reintentar ahora** y **Ajustes**. Si no vuelve,
  dice "No pude hablar con Hermes" y se queda en ámbar; **Reintentar ahora** repite la última
  pregunta. Cerrar el micrófono deja de insistir.
- Si los nodos de cancelación de eco no existen, el daemon usa el micrófono y los altavoces por
  defecto y lo avisa en el log.
- **Archivos hacia afuera.** «Dame el archivo de finanzas»: Omi lo muestra en la tarjeta y lo
  arrastras a cualquier app (es un arrastre Wayland con la ruta: Mail lo adjunta, la terminal pega la
  ruta). Doble clic lo abre.
- **Archivos hacia adentro.** Arrastra un archivo hasta Omi en la barra (o hasta la tarjeta): Omi lo
  mira (`catch`), lo mueve a la carpeta que toca bajo `~/Documents`, dice qué es y pregunta qué
  sigue. El micrófono queda abierto para que respondas.
- **Contexto.** «¿Qué es esto?» captura la ventana activa y se la pasa a Hermes; «abre el enlace que
  copié» lleva el portapapeles; «hazlo más formal» lleva el texto seleccionado y la tarjeta ofrece
  **Reemplazar** (escribe el texto donde estaba la selección) y **Copiar**.
- **Apps y comandos.** «Abre Herdr en omadot»: Hermes lo lanza con sus herramientas; mientras corre
  una herramienta, Omi está `working`.
- **Web.** Si la respuesta trae una página, **Ver al lado** abre un Chromium flotante pequeño bajo
  la tarjeta (Hyprland lo coloca y redimensiona) y **Abrir en Chromium** la abre normal.
- **Correo, borradores y permisos.** Omi redacta pero no envía: **Enviar** es un botón o un «sí,
  mándalo». Si Hermes pide permiso para una herramienta (`approval.request`), la tarjeta lo pregunta
  y los botones responden con `omadot approve once|deny`.
- **Recordatorios.** «Recuérdame pagar la factura el 4 a las 9»: Hermes manda `reminder` con la
  fecha ISO, el daemon lo agenda y a la hora Omi avisa (`notify`, campana en la barra, `notify-send`)
  con **Hecho** y **Posponer 1 h**. `omadot reminders` los lista.
- **Enfoque.** «Enfoque 25 minutos»: la cuenta atrás vive en la píldora junto a Omi; al acabar
  pregunta si otros 25. `omadot focus 25` desde la terminal.
- **Tareas largas.** Un script puede reportar `omadot progress ID PCT [ETIQUETA] [DETALLE] [CARPETA]`:
  la píldora muestra el avance y al llegar a 100 Omi avisa con **Abrir carpeta**.
- **La tarjeta solo aparece cuando la llamas.** En reposo no hay nada flotando: Omi queda en la barra.
  Al pulsar `Super` dos veces (o cuando suena un recordatorio o sueltas un archivo sobre Omi) la tarjeta
  aparece bajo la barra: Omi grande al centro, la respuesta completa (hace scroll a partir del 70 % de la
  pantalla) y una barra de texto abajo; escribe y pulsa Enter para hablarle sin voz (`omadot ask`).
  Los colores salen del tema de Omarchy (`~/.local/state/omarchy/current/theme/colors.toml`).
- **La tarjeta se cierra sola.** Si Omi solo habló, a los 2,5 s; si trajo archivos o botones, a los
  15 s, o antes con `Super` dos veces o el siguiente turno. «No te entendí» dura 2,5 s.
- **Micrófono apagado.** `Super + Shift + .` (o `omadot mute`) lo apaga del todo: Omi lo muestra con
  el punto gris. `Super` dos veces lo vuelve a encender.
- **Motor de voz.** `omadot set tts kokoro|omni`. Kokoro responde en ~0,1 s por frase; OmniVoice en
  ~0,7 s pero suena más natural (`omadot setup omni` lo instala); su voz se describe con atributos en inglés:
  `omadot set omni_voice "female, young adult, low pitch"` (género, edad, tono, `whisper`, acento).
- **Carril rápido.** Lo trivial («hola», «¿qué hora es?», «17 por 23», «traduce…») lo responde Haiku en
  ~0,7 s; si necesita archivos, apps, memoria o una acción, Haiku dice `ESCALATE` y Hermes, que ya iba
  en paralelo, sigue sin perder tiempo. Es opcional y viene apagado: `omadot set fast on` lo enciende
  (también el panel de ajustes), `omadot set fast off` lo apaga y `omadot --fast` lo enciende solo para esa corrida.
- **Repetir y hablar.** `omadot repeat` vuelve a decir la última respuesta; `omadot say "texto"` hace
  que Omi lo diga (para scripts: avisos leídos en voz alta).
- Desde la terminal: `omadot ask "qué hora es"`, `omadot drop ~/Descargas/factura.pdf`,
  `omadot approve deny`, `omadot remind 2026-11-04T09:00 pagar la factura`, `omadot dismiss`,
  `omadot open https://omarchy.org mini`.
- Logs y latencia por turno: `journalctl --user -u omadot -f`.
- A mano, sin servicio: `omadot` (`uv run omadot` desde un clon; `--open` mic abierto, `--profile`,
  `--no-filler`, `--no-card`, `--no-context` para no adjuntar ventana/portapapeles/capturas, `--sleep-after SEGUNDOS`).
- Desde la terminal: `omadot set voice em_alex`, `omadot set speed 1.2`, `omadot set speak off`,
  `omadot status` (JSON), `omadot retry`.

## Tests

```bash
uv run pytest -q
```

## Decisiones

| Tema | Decisión | Por qué |
|---|---|---|
| Mascota | Omi: el logo de Omarchy con ojos de bloque, sin cara kawaii | Elegido en el diseño (opción A, versión sobria) |
| Atajo | `Super` dos veces (soltado solo, < 0,4 s entre pulsaciones) como toggle, fin de turno automático | Pedido: invocarlo sin un acorde; antes era `Super + .` |
| Idioma | Sigue el del usuario/sistema | Las instrucciones de voz piden responder en el idioma de quien habla |
| Voz a texto | faster-whisper `large-v3-turbo` | ~0,1 s por frase en la 5070 Ti; voxtype es push-to-talk sin API de parciales |
| Agente | Hermes, perfil `omadot`, `openai-codex/gpt-6-luna` | No había credencial de Claude en Hermes; luna fue el más rápido medido (1,9–2,5 s al primer token) |
| Fin de turno | Smart Turn v3 con tope de 1 s de silencio | Con el tope por defecto (3 s) frases cortas en español esperaban 3 s |
| Eco | Módulo `echo-cancel` de PipeWire solo para OmaDot | Tus dispositivos por defecto no cambian |
| Respuestas | Instrucciones de voz por turno (1–3 frases, sin markdown) | No toca el `SOUL` ni la memoria del perfil |
| Barra | Plugin de la barra de Omarchy (no Waybar) | La barra de Omarchy es Quickshell |
| Tarjeta | Instancia propia de Quickshell | La barra solo abre popups de paneles abiertos con clic |
| Color de voz | Azul fijo `#7aa2f7` | El acento del tema puede ser gris y desaparece a 16 px |
| Errores | Hermes caído o respuesta vacía: Omi lo dice y queda en ámbar `#e0af68` hasta el siguiente turno | El diseño pedía avisar; sin esto, Omi se quedaba callado y "pensando" |
| Turno sin texto | No se responde; la tarjeta dice "No te entendí." | Responder a ruido sería intrusivo; quedarse en "pensando" confundía |
| Eco ausente | Si faltan `omadot_aec_source`/`omadot_aec_sink`, dispositivos por defecto | Una fuente inexistente deja a Omi sordo |
| Ajustes | Panel anclado a Omi con clic derecho; el clic izquierdo sigue abriendo el micrófono | El diseño tenía clic = panel, pero el clic ya abría el mic desde la primera versión |
| Voces | Las tres voces en español del Kokoro local (`ef_dora`, `em_alex`, `em_santa`), no "Cálida/Neutra/Viva" | Esos nombres del diseño no correspondían a voces reales |
| Atajo e idioma en ajustes | Solo lectura: el atajo se lee de `bindings.lua` y se cambia ahí | Reescribir tu configuración de Hyprland desde un panel es arriesgado |
| Carril rápido | Opcional, apagado por defecto (`omadot set fast on`) | Usa tu cuenta de Claude a través de `omp` (Anthropic restringe que productos de terceros usen el login de claude.ai), el texto de cada turno también va a Anthropic, y los turnos de Haiku no quedan en la sesión de Hermes |
| Modelo de Hermes en ajustes | Solo lectura (perfil, modelo y proveedor) | El modelo vive en la configuración del perfil de Hermes, no en OmaDot |
| Sin voz | La respuesta se ve en la tarjeta aunque la transcripción esté oculta | Si no, Omi no respondería de ninguna forma |
| Dormir | Tras 10 min sin voz con el mic abierto (o en error), cierra el mic; no mientras responde | Que el micrófono no quede abierto toda la tarde por olvido |
| Reintentos | Solo si Hermes nunca recibió el turno (conexión o 5xx), cada 5 s, 6 veces | Reenviar un run que ya empezó podría repetir herramientas que ya corrieron |
| Respuesta estructurada | Una línea `@@omi {json}` al final de la respuesta, nunca hablada; solo las claves que la tarjeta conoce | Deja al modelo decidir qué mostrar sin un parser por intención; claves desconocidas se descartan |
| Comandos con argumentos | Socket Unix con JSON, además de las señales | Una señal no lleva la ruta de un archivo ni el texto de un botón |
| Soltar archivos | Omi los mueve bajo `~/Documents/<carpeta por contenido>`, sin preguntar, con **Deshacer** en la respuesta | Es lo que pedía el diseño («lo guarda donde toca»); mover no destruye nada |
| Permisos | Los `approval.request` de Hermes se muestran en la tarjeta con Sí/No; además el protocolo pide `ask` antes de enviar, borrar o pagar | Dos capas: la real de Hermes y la del modelo |
| La tarjeta se queda | 2,5 s si solo habló, 15 s con archivos o botones, o hasta `Super` dos veces | Antes se cerraba al callar y no daba tiempo a arrastrar nada; 4/30 s se sentían largos |
| Tarjeta solo al invocar | Oculta en reposo; aparece con `Super` dos veces, un recordatorio o un archivo soltado, y se va al terminar | Pedido: no quería a Omi siempre flotando en pantalla; la barra ya lo muestra |
| Doble toque en el daemon | Hyprland corre `omadot tap` al soltar `Super` (`SUPER + Super_L`, `release`); el daemon mide los 0,4 s | La config Lua de Hyprland no da un reloj en milisegundos; el CLI tarda ~60 ms y el retraso es el mismo en los dos toques |
| Muletillas | 12 frases cortas, nunca una de las tres últimas | «A ver» y «déjame ver» cansaban a la segunda vez |
| Muletilla | Solo si el primer token tarda más de 2 s (`--filler-after`) | Un «hola» rápido recibía «déjame ver» a los 0,6 s |
| Colores de la tarjeta | Del tema actual de Omarchy, releídos cada 2 s | La tarjeta no cambiaba con el tema |
| Motores de voz | Kokoro por defecto; OmniVoice opcional por ajuste. Qwen3-TTS descartado | Medido: Kokoro ~0,1 s/frase, OmniVoice ~0,75 s, Qwen3-TTS 0.6B 4–11 s y 1.7B 5–22 s sin su servidor de streaming |
| Perfil de Hermes | `reasoning_effort: minimal`, memoria encendida, MoA apagado, MCP de Supabase apagado y toolsets reducidos para `api_server` (clarify, file, memory, session_search, skills, terminal, todo, vision, web) | Medianas de 5 corridas al primer token: «hola» 1,8 s (low) → 1,2–1,4 s (minimal); apagar la memoria solo rasca 0,1–0,2 s más y se pierde lo que Omi sabe de ti; los modelos «flash» de opencode-go salieron más lentos (1,9–4 s) |
| Carril rápido | Haiku vía `omp --mode rpc`, en carrera con Hermes; Haiku escala si no está seguro; la cuenta la elige el broker de omp entre las autenticadas | Medido: 0,64–0,78 s por turno trivial frente a 1,1–1,4 s de Hermes; en paralelo no cuesta nada cuando escala |
| Aprobaciones de Hermes | `approvals: mode: smart` en el perfil `omadot` | Con `off` nunca pidió permiso en la prueba del borrado; la tarjeta ya sabe responder Sí/No |
| Sesión de Hermes | Una por conversación: nueva tras 10 min sin turno o 24 turnos | La sesión única crecía sin tope: 1,8–3,3 s al primer token frente a 1,1–1,3 s en una nueva |
| Hora en contexto | Cada turno lleva `[Now: …]` y la instrucción de usarlo | «¿qué hora es?» llamaba una herramienta: 3,4 s → 1,5 s |
| Navegador | Chromium `--app` flotante bajo la tarjeta, colocado por Hyprland, no un WebEngineView dentro | Quickshell no inicializa QtWebEngine; una ventana real se redimensiona y se cierra como cualquier otra |
| Reemplazar texto | `wl-copy` + `wtype` del texto nuevo sobre la selección | No hay API Wayland para sustituir la selección de otra app; escribirla es lo que haría una persona |
| Captura | Ruta del PNG en el turno; Hermes la mira con sus herramientas de imagen | La API de runs no acepta imágenes adjuntas; la ruta sí viaja |
| Recordatorios | Fecha ISO 8601 en el `@@omi`, agendada por el daemon y guardada en `~/.local/state/omadot` | Parsear «el martes a las nueve» es trabajo del modelo, no del daemon |
| Captura automática | Solo con frases que señalan la pantalla («qué es esto», «mira esto», «esta ventana», «captura») | «No quiero esto» capturaba la ventana en 7 de 9 frases cotidianas |
| Avisos en cola | Varios recordatorios a la vez se muestran uno por uno; cada botón pasa al siguiente | Tras una suspensión, el segundo pisaba al primero y se perdía |
| Enfoque = no molestar | Mientras corre el enfoque los recordatorios esperan; al acabar, primero el aviso del enfoque y luego ellos | Un recordatorio en medio del enfoque lo rompe |
| Quitar el silencio | `omadot mute off` solo quita la bandera; el micrófono lo abre `Super` dos veces | Un `mute off` abría el micrófono sin que nadie lo pidiera |
| Tarjeta ambiental | `progress` y `remind` desde fuera de la conversación ocupan la tarjeta solo si no hay una respuesta a la vista; si la hay, se suman sin borrarla | Se apilaban sobre la última respuesta; y, al revés, un tick de progreso borraba una respuesta que estabas leyendo |
| Idioma | Whisper transcribe en español; a Hermes se le pide responder en el idioma del último mensaje | Una pregunta escrita en inglés se respondía en español por la inercia de la sesión |
| Instalación | `uv tool install` desde git; la tarjeta, el plugin, los servidores de voz y la configuración de eco viajan dentro del paquete y `omadot setup …` instala cada pieza apuntando al binario que lo ejecuta | Todo asumía `~/dev/omadot/.venv`; así una instalación y un clon (`uv sync`) siguen el mismo camino |
| Plugin instalado | Copia de archivos reales en `~/.config/omarchy/plugins/ale.omadot`, con la ruta absoluta del binario en `OmadotBin.js`; no un symlink | El validador de Omarchy rechaza symlinks y la barra no hereda el `PATH` de tu terminal |
| Binario de la tarjeta | El daemon se lo pasa en `OMADOT_BIN` al lanzarla | La tarjeta vive dentro del paquete instalado, que no se reescribe |
| Kokoro | `omadot setup kokoro`: el mismo servidor de esta máquina, en su venv con PyTorch cu128, modelo en `~/.local/share/omadot/tts-kokoro/models` y `HF_HUB_OFFLINE=1` en el servicio | Se levanta con un comando; el servicio no depende del caché de Hugging Face de tu shell ni de la red |
| Perfil de Hermes desde cero | `omadot setup hermes` clona `default`, le da una `API_SERVER_KEY` propia y aplica los ajustes medidos; el modelo lo eliges tú | El gateway rechaza la llave del perfil raíz en `/p/<perfil>/`; el modelo depende de las credenciales de cada quien |
| Atajos al instalar | `omadot setup binds` imprime las líneas, no las escribe | Mismo motivo que «Atajo e idioma en ajustes»: no reescribir tu configuración de Hyprland |
| Versión de Pipecat | `pipecat-ai` fijado a 1.12.x | `uv tool install` no lee `uv.lock` y resolvía 1.14, que no está probada |
| Licencia | MIT | Decisión del autor |

## Límites conocidos

- La tarjeta y la barra se probaron con `qmllint`; el arrastre Wayland desde QML (`Drag.Automatic`) y la
  zona de soltar dependen de la versión de Quickshell/Qt y no tienen test automático.
- «Reemplazar» escribe el texto con `wtype` en la ventana enfocada: si cambiaste de ventana, escribe ahí.
- La captura va como ruta: si el perfil de Hermes no tiene herramienta de imagen, Omi solo sabrá el
  título de la ventana.

- La latencia la domina Hermes: ~1,1–1,4 s al primer token en turnos simples (con picos de 2–3 s
  del proveedor) y 2,5–3,5 s cuando usa una herramienta (la llama a ~1,5 s y vuelve al modelo). El
  relleno suena a los 2 s. Pedirle al modelo que hable antes de usar la herramienta no funcionó con
  gpt-6-luna. Con el carril rápido, un turno trivial llega a la primera palabra en ~0,7 s.
- Los turnos que responde Haiku no entran en la sesión de Hermes: si luego preguntas «¿qué me dijiste?»
  Hermes no lo tendrá.
- Si enciendes el carril rápido (`omadot set fast on`), cada turno viaja también a Claude Haiku (en la nube)
  con tu cuenta de Claude vía `omp`, aunque lo termine respondiendo Hermes.
- Las cifras de latencia son de pocas corridas con un micrófono virtual, no un benchmark.
- Fuera de este alcance: el modo de voz en la nube (GPT Live / Gemini Live) y los errores del diseño
  "Sin micrófono" y "No entendí (6 s sin voz)".
- `omadot status` (y el panel) muestra el gateway y el perfil por defecto (`127.0.0.1:8642`,
  `omadot`), no los que se le pasaron al daemon con `--hermes`/`--profile`.
- La investigación del stack está en [docs/research/voice-stack.md](docs/research/voice-stack.md).
- Los comandos `omadot setup` se probaron en un HOME temporal (`--no-enable`); habilitar los servicios y poner
  a Omi en la barra en marcha (`omarchy-shell`) solo tiene tests con comandos simulados.
- La barra (`BarWidget.qml`) no tiene test de carga automático: depende de los módulos `qs.Commons`/`qs.Ui`
  del shell de Omarchy.

## Licencia

MIT, ver [LICENSE](LICENSE). `omadot setup kokoro` descarga aparte, en su propio venv: el modelo Kokoro-82M
y las librerías `kokoro` y `misaki` (Apache-2.0) y espeak-ng (GPL-3.0, vía `espeakng-loader`); `omadot setup omni`
descarga OmniVoice (Apache-2.0). Ninguno se distribuye con OmaDot.
