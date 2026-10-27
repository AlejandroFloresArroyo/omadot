# OmaDot: investigación de stack (2026-10-10)

Prototipo: asistente de voz para Omarchy. Cadena objetivo: mic → STT local → agente → Kokoro TTS → altavoz. Prioridad: sensación tipo ChatGPT Live (tiempo a primer audio, barge-in, fin de turno).

Convención: **[medido]** = lo corrí en esta máquina hoy. **[vendor]** = afirmación del proyecto, no reproducida. **[no verificado]** = no encontré fuente primaria o no pude probarlo.

## 0. Estado de la máquina (medido)

| Hecho | Evidencia |
|---|---|
| GPU RTX 5070 Ti, driver 610.57.04, 16303 MiB, 3759 MiB en uso | `nvidia-smi` |
| PipeWire 1.6.8 con capa Pulse; **sin** módulo de echo-cancel cargado | `pactl info`, `pactl list modules short` |
| Python del sistema 3.14.7; uv tiene 3.11, 3.12, 3.13 | `python3 --version`, `uv python list` |
| voxtype 1.1.0 (`voxtype-bin`), daemon systemd activo, Whisper `large-v3-turbo` en GPU vía Vulkan, `language = "es"` | `pacman -Qi voxtype-bin`, `voxtype status --extended`, `voxtype info accel`, `~/.config/voxtype/config.toml` |
| Hermes Agent instalado (`0.21.6+337.g26610b0`, commit 2026-10-09), gateway escuchando en `127.0.0.1:8642`, `API_SERVER_ENABLED=true` | `~/.hermes/hermes-agent/install-stamp.json`, `ss -ltnp`, `~/.hermes/.env` |
| Hermes hoy usa proveedor `opencode-go` (no Anthropic); STT local `base` en `es`; TTS `edge` | `~/.hermes/config.yaml` |
| Servidor Kokoro propio en `127.0.0.1:8880` (`~/.local/share/local-tts/server.py`, PyTorch `kokoro`, CUDA, ~1054 MiB VRAM), OpenAI-compatible, voces `ef_dora`, `em_alex`, `em_santa` | `ps`, `curl /v1/models`, `curl /v1/audio/voices`, `nvidia-smi` |
| `claude` 2.1.294 (vía mise); `omp` 18.8.5 (oh-my-pi vía mise) | `claude --version`, `omp --help` |
| **Ojo:** `~/.local/bin/claude` es un wrapper de mise (`mise x claude -- claude`) que en mi shell de agente entró en bucle infinito (se resolvía a sí mismo). Un daemon debe invocar el binario real por ruta absoluta o el SDK. | `cat ~/.local/bin/claude`; salida repetida de mise hasta timeout |

## 1. voxtype vs faster-whisper

### voxtype

- Qué es: dictado push-to-talk para Linux/Wayland, en Rust; motor por defecto Whisper vía whisper.cpp (whisper-rs); además Parakeet, Moonshine, SenseVoice, Paraformer, Dolphin, Omnilingual, Cohere, OpenVINO. Fuente: https://github.com/peteonrails/voxtype ; `voxtype --help` local (1.1.0). En este binario solo `whisper` está compilado (`voxtype info engines`).
- Omarchy lo integra: instalador opcional `omarchy-voxtype-install` (paquete `voxtype-bin`, `voxtype setup gpu --enable` si hay Vulkan, unidad systemd), notificación de primer arranque, binds Hyprland `SUPER+CTRL+X` (toggle) y `F9` (push-to-talk), indicador en la barra (`Dictation.qml`) y config por defecto `base.en`. Fuente local: `~/.local/share/omarchy/bin/omarchy-voxtype-install`, `default/hypr/bindings/voxtype.lua`, `install/user/first-run/install-voxtype.hook`, `default/voxtype/config.toml`.
- Interfaz para otro programa: solo control por señales (`voxtype record start|stop|toggle|cancel` envía SIGUSR1/SIGUSR2 al daemon), estado en `$XDG_RUNTIME_DIR/voxtype/state` y `voxtype status --follow --format json`, salida a teclado/portapapeles/archivo (`record start --file=...`). **No hay socket/API que entregue texto parcial** a otro proceso. Fuente: `voxtype record start --help`, `voxtype status --help`, config.toml local.
- Streaming: `whisper.eager_processing` ("Transcribe overlapping chunks while you are still speaking") y `parakeet.streaming`/`openvino.streaming` (no compilados en este binario). VAD (`vad.backend = auto|energy|whisper`, este último Silero) sirve para **rechazar grabaciones de puro silencio**, no para detectar fin de turno. Fuente: `voxtype config schema`.
- Modo `remote`: puede mandar audio a un endpoint OpenAI-compatible (`whisper.remote_endpoint`). Fuente: `voxtype config schema`.
- **[medido]** `voxtype transcribe` (CLI en frío: arranque + carga de modelo + inferencia) sobre un clip de 1,43 s: inferencia **0,12 s**, pared **0,74–0,85 s** (2 corridas). Clip: `/usr/share/sounds/alsa/Front_Center.wav` remuestreado a 16 kHz.

Conclusión: voxtype es excelente dictado, pero su modelo es "pulsar → hablar → soltar". No sirve como STT de un bucle conversacional con barge-in; sí como **fallback push-to-talk** (F9 → archivo → agente).

### faster-whisper y streaming

| Componente | Estado | Fuente |
|---|---|---|
| faster-whisper 1.2.1 (2025-10-31), CTranslate2, "up to 4 times faster than openai/whisper"; `vad_filter` con Silero; requiere cuBLAS + cuDNN 9 para CUDA 12 | [vendor] | https://pypi.org/project/faster-whisper/ , https://github.com/SYSTRAN/faster-whisper |
| Benchmark vendor: large-v2 fp16, 13 min de audio en 63 s (17 s con batch 8) en RTX 3070 Ti | [vendor] | idem |
| whisper-large-v3-turbo: 809M params, decoder de 32→4 capas, 99 idiomas | [vendor] | https://huggingface.co/openai/whisper-large-v3-turbo |
| distil-large-v3.5: **solo inglés** → descartado para español | [vendor] | https://huggingface.co/distil-whisper/distil-large-v3.5 |
| whisper_streaming (LocalAgreement, ~3,3 s de latencia en long-form): el propio repo dice que en 2025 lo reemplaza SimulStreaming | [vendor] | https://github.com/ufal/whisper_streaming |
| WhisperLive: servidor WebSocket, backends faster_whisper / tensorrt / openvino, VAD opcional, MIT | [vendor] | https://github.com/collabora/WhisperLive |
| Silero VAD: <1 ms por chunk de 30 ms en un hilo de CPU, 8/16 kHz, MIT | [vendor] | https://github.com/snakers4/silero-vad |

Para un asistente no hace falta transcripción parcial continua: con VAD + detector de turno, se transcribe el segmento completo al cerrar el turno. Con large-v3-turbo en GPU eso cuesta ~0,1–0,2 s por frase (mi medición con whisper.cpp/Vulkan: 0,12 s para 1,4 s de audio). faster-whisper en CUDA **no lo medí** (no está instalado; soporte de CTranslate2 para Blackwell sm_120: **[no verificado]**).

## 2. Hermes Agent (Nous Research)

- Estado: v0.21.6 (2026-10-08, parche de seguridad); v0.21.4 (2026-09-21) añadió `--format stream-json`; v0.21.0 "Pantheon" (2026-08-31). MIT. Fuente: https://github.com/NousResearch/hermes-agent/releases , https://github.com/NousResearch/hermes-agent
- Claude como proveedor: sí, `provider: anthropic` (alias `claude`, `claude-code`). Tres vías: `ANTHROPIC_API_KEY` (pago por token), `ANTHROPIC_TOKEN`, u OAuth que reusa credenciales de Claude Code. El OAuth **solo funciona con plan Max + créditos de uso extra**; nunca consume la cuota base de Max; Pro no sirve. Fuente local: `website/docs/integrations/providers.md` (líneas 21, 142–207).
- Modo servidor (`hermes gateway` + `API_SERVER_ENABLED=true`, puerto 8642), fuente `website/docs/user-guide/features/api-server.md`:
  - `POST /v1/chat/completions` (stateless, SSE token a token + evento `hermes.tool.progress`).
  - `POST /v1/responses` con estado en servidor (`previous_response_id` o `conversation: "<nombre>"`).
  - `POST /v1/runs` + `GET /v1/runs/{id}/events` (SSE con `message.delta`) + **`POST /v1/runs/{id}/stop`** ("Interrupt a running agent turn") → encaja con barge-in.
  - `/api/sessions/{id}/chat/stream` (SSE `assistant.delta`, `tool.started`...).
- Headless: `hermes chat -q "..." --format stream-json` (JSONL). Fuente: `website/docs/reference/cli-commands.md:134-198`. También ACP (`acp_adapter/`).
- Voz propia: modo `/voice` (CLI/TUI) con VAD por energía RMS (umbral 200, **fin de turno tras 3,0 s de silencio** por defecto, configurable), barge-in full-duplex (`voice.barge_in`, calibra contra el cuarto en silencio), TTS en streaming **por frase** (`min_len: 20`), Kokoro vía `tts.provider: openai` + `base_url` + `language` (`lang_code`). Modo desktop `gpt-live` delega a Hermes. Fuente: `user-guide/features/voice-mode.md:148-273`, `user-guide/features/tts.md:50-68,157-159`, `developer-guide/streaming-tts.md`.
- Memoria: `MEMORY.md` (2.200 chars) y `USER.md` (1.375 chars) en `~/.hermes/memories/`, inyectados como snapshot congelado al inicio de sesión; el agente los edita con la herramienta `memory`; plugins externos (Honcho, Mem0, Hindsight...). Advertencia: un solo proceso por `HERMES_HOME`. Fuente: `user-guide/features/memory.md`, `memory-providers.md`.
- Aprendizaje: el system prompt pide guardar flujos no triviales como skills (`skill_manage`); un "background review" (fork del contexto) revisa memoria y skills cada `memory.nudge_interval` / `skills.creation_nudge_interval`; puede gastar una parte notable de tokens. Fuente: `user-guide/features/skills.md:596-616`, `memory.md:395-412`.
- Tareas programadas: herramienta `cronjob_manage` (lenguaje natural o cron), jobs con skills adjuntas, modo sin agente (script), disparo por webhook, entrega a chat/archivo/plataforma; CRUD remoto en el API server. Fuente: `user-guide/features/cron.md`.

## 3. `claude` CLI headless y Agent SDK

Fuente: https://code.claude.com/docs/en/headless (leída hoy) y `claude --help` local 2.1.294.

| Capacidad | Detalle |
|---|---|
| `-p/--print` | no interactivo; sin diálogo de confianza |
| `--output-format stream-json --verbose --include-partial-messages` | eventos `stream_event` con `text_delta`; último evento `result` con `session_id` y coste |
| `--input-format stream-json` | entrada en streaming: **un proceso vivo** recibe varios mensajes de usuario |
| `--resume <id>` / `--continue` / `--fork-session` / `--no-session-persistence` | sesiones persistidas como `.jsonl`; `--resume` acepta id o ruta |
| `--bare` | salta hooks, skills, plugins, MCP, auto-memory, CLAUDE.md "to reduce startup time"; **no lee OAuth/keychain, exige `ANTHROPIC_API_KEY`**; será el default de `-p` en el futuro |
| Interrupción | SIGINT o `interrupt()` del SDK terminan el turno limpio; SIGTERM (exit 143) lo deja sin resultado. `system/init` anuncia `capabilities` como `interrupt_receipt_v1` |

- **[medido]** `claude --version` (binario real): 78 ms la primera vez, 10–11 ms después. **No representa** el arranque de `-p` (que carga hooks, MCP, CLAUDE.md, auth y hace la llamada a la API). Arranque real de `claude -p` hasta primer token: **[no medido]** (requiere un prompt de pago).
- Agent SDK (Python `ClaudeSDKClient` / TS `query` con generador): "Streaming Input Mode" es el preferido, proceso largo, cola de mensajes, interrupción en tiempo real; el modo de mensaje único **no** soporta interrupción. El SDK "runs the Claude Code binary". Fuente: https://code.claude.com/docs/en/agent-sdk/streaming-vs-single-mode , https://code.claude.com/docs/en/agent-sdk/overview
- Licencia/uso: "Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK" (overview). Para uso personal con tu propia suscripción la doc no es explícita: **pregunta abierta**.

Veredicto: para un daemon, `ClaudeSDKClient` en modo streaming (o un `claude -p --input-format stream-json --output-format stream-json` vivo) es mejor que lanzar `claude -p --resume` por turno: se paga el arranque una vez y hay `interrupt()` para barge-in.

## 4. omp = oh-my-pi

- Fork de pi-mono (Mario Zechner), agente de código en terminal, TypeScript, MIT, por Stencil Labs. Soporta Anthropic OAuth entre otros. Fuente: https://github.com/can1357/oh-my-pi ; local `omp --help` v18.8.5 (wrapper `mise ... github:can1357/oh-my-pi`).
- Modos: `--mode text|json|rpc|rpc-ui`, `-p`, `--resume`, `--no-session`, roles de modelo `--smol/--slow/--plan`. Fuente: `omp --help` local.
- RPC: NDJSON por stdin/stdout, comandos `prompt`, `steer`, `follow_up`, `abort`, **`abort_and_prompt`**, `new_session`, `set_model`; eventos con `text_delta`; cada prompt cierra con `prompt_result`. `abort_and_prompt` es justo la primitiva de barge-in. Fuente: https://github.com/can1357/oh-my-pi/blob/main/docs/rpc.md (vía búsqueda) y https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/rpc.md (protocolo upstream). Detalle exacto de nombres de eventos de omp: **[parcialmente verificado]**, leí el resumen, no el archivo completo.

## 5. Kokoro TTS

| Hecho | Tipo | Fuente |
|---|---|---|
| Kokoro-82M, pesos Apache-2.0, paquete `kokoro` 0.9.4 (2025-04-05), Python 3.10–<3.13 | vendor | https://pypi.org/project/kokoro/ , https://github.com/hexgrad/kokoro |
| Español: `lang_code='e'`, voces `ef_dora`, `em_alex`, `em_santa`; sin notas de calidad; "Support for non-English languages may be absent or thin" (G2P vía espeak-ng) | vendor | https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md |
| `KPipeline` es un generador que entrega audio por fragmento/frase → streaming por frase nativo | vendor | https://github.com/hexgrad/kokoro |
| Kokoro-FastAPI: `/v1/audio/speech` OpenAI-compatible, `stream=true`, `pcm`, `lang_code`; primer audio ~300 ms GPU (chunk 400), ~3,5 s CPU i7 viejo, <1 s M3 Pro; 35–100x tiempo real (RTX 4060 Ti) | vendor | https://github.com/remsky/Kokoro-FastAPI |
| kokoro-onnx: ONNX Runtime CPU/GPU, ~300 MB (80 MB cuantizado), MIT + modelo Apache | vendor | https://github.com/thewh1teagle/kokoro-onnx |
| **Servidor local (PyTorch, CUDA):** frase de 2,1 s de audio en **39–60 ms**; dos frases (9,2 s de audio, cuerpo completo) en **88–126 ms**, español `ef_dora`, 3 corridas cada una | **medido** | `curl -w` a `127.0.0.1:8880/v1/audio/speech` |

Calidad del español: **[no verificado]**, es subjetiva y la documentación no la califica. El servidor local ya trae un `lexicon.json` de re-escritura fonética para español, señal de que el G2P falla en términos técnicos. CPU: solo hay cifras vendor.

## 6. Orquestación

| Opción | Barge-in | Fin de turno | Local mic/altavoz | Agente custom | Notas |
|---|---|---|---|---|---|
| **Pipecat** v1.12.0 (2026-09-26) | Sí por defecto: "immediately stops speaking, in-flight work is cancelled" | Silero VAD (`stop_secs` 0,2 s por defecto) + **Smart Turn v3** local (`LocalSmartTurnAnalyzerV3`, ONNX) | `LocalAudioTransport` (PyAudio, **sin AEC**) | Sí: cualquier `FrameProcessor`, o servicio OpenAI-compatible | `WhisperSTTService` local (CUDA), `KokoroTTSService` (kokoro-onnx) |
| **LiveKit Agents** | Sí, interrupción adaptativa, distingue backchannel | Turn detector multilingüe (audio+texto) + Silero | `console` mode; el resto asume servidor LiveKit (Cloud o propio) | Sí (plugins / nodo LLM) | Más infraestructura (WebRTC/room) de la que pide un asistente de escritorio |
| **Hermes `/voice`** | Sí (energía RMS) | RMS + 3 s de silencio por defecto | Sí (sounddevice) | Es el agente | Cero código; fin de turno grueso; STT local no hace streaming |
| **Bucle propio** | Lo escribes tú | Silero + Smart Turn a mano | sounddevice | Total | Más control, más bugs de concurrencia |

Fuentes: https://github.com/pipecat-ai/pipecat/releases , https://docs.pipecat.ai/pipecat/learn/speech-input , https://docs.pipecat.ai/api-reference/server/services/tts/kokoro , https://docs.pipecat.ai/server/services/stt/whisper , https://raw.githubusercontent.com/pipecat-ai/pipecat/main/src/pipecat/transports/local/audio.py , https://docs.livekit.io/agents/build/turns/ , https://docs.livekit.io/agents/start/voice-ai/

Smart Turn v3.2: BSD-2, 23 idiomas **incluido español**, solo audio (16 kHz mono, hasta 8 s), 8 MB cuantizado CPU, "as little as 10ms on some CPUs" [vendor]. Fuente: https://github.com/pipecat-ai/smart-turn . Idiomas del turn detector de LiveKit: **[no verificado]**.

Eco: sin auriculares, el altavoz dispara el VAD y el barge-in se vuelve falso positivo. PipeWire trae `module-echo-cancel` (WebRTC AEC, `aec/libspa-aec-webrtc`); no está cargado aquí. Fuente: https://docs.pipewire.org/page_module_echo_cancel.html

## 7. OpenAI Realtime (para después)

Fuente: https://developers.openai.com/api/docs/models , `/models/gpt-live-1`, `/models/gpt-realtime-2.1`, https://developers.openai.com/api/docs/guides/live-delegation

- `gpt-live-1`: full-duplex, endpoint `v1/live/sessions` (no `v1/realtime`), USD 0,05/min por segundo; **delega** razonamiento y herramientas a un backend: "Responses delegation" (OpenAI corre el modelo) o "Client delegation" (tu app corre cualquier agente). Encaja con "voz en la nube, agente local". Hermes Desktop ya lo usa (`voice_chat_mode: gpt-live`).
- `gpt-realtime-2.1`, `gpt-realtime-2.1-mini`, `gpt-realtime-2`, `gpt-realtime-1.5`: endpoint `v1/realtime`, function calling soportado; `gpt-realtime-mini` deprecado.
- STT en vivo: `gpt-live-transcribe`, `gpt-realtime-whisper`.

## Recomendación para el prototipo

**Stack:** Pipecat (Python 3.12 vía uv) con `LocalAudioTransport` + PipeWire `module-echo-cancel` → Silero VAD (`stop_secs` ~0,2) + Smart Turn v3.2 local → faster-whisper `large-v3-turbo` en CUDA, `language="es"`, transcribiendo el turno completo → **adaptador de agente** (un `FrameProcessor` propio) → Kokoro (reusar el servidor de `:8880` en `pcm`, o `KokoroTTSService`) por frase → altavoz.

**Adaptador de agente, en este orden:**
1. **Claude Agent SDK** (`ClaudeSDKClient`, streaming input, proceso persistente): menor latencia por turno, `interrupt()` para barge-in, herramientas y sesiones de Claude Code.
2. **Hermes** vía `POST /v1/runs` + SSE + `/stop` cuando quieras memoria, skills y cron. Ya corre aquí; falta cambiar el proveedor a `anthropic` (API key, o Max + créditos extra).
3. **omp** `--mode rpc` (`abort_and_prompt`) como tercera opción, mismo contrato.

Fallback push-to-talk: voxtype `record start --file=...` con F9 y el texto al mismo adaptador.

**Paso 0 (una tarde, sin código):** apuntar el `/voice` de Hermes a Kokoro `:8880` y STT local `large-v3-turbo`, bajar `silence_duration` y medir. Es la línea base contra la que juzgar Pipecat.

**Truco de latencia:** que la voz arranque con una frase corta antes de que el agente termine sus herramientas (comentario intermedio por streaming, o un modelo rápido para turnos de voz como el `auxiliary.voice_chat` de Hermes). El TTFT del LLM más las herramientas dominan; STT (~0,1 s) y TTS (~0,05 s por frase) no.

### Riesgos

- **TTFT de Claude con herramientas** domina la latencia; no lo medí. Puede quedar lejos de ChatGPT Live en turnos con herramientas.
- **Eco/barge-in falso** sin AEC ni auriculares.
- **Blackwell (sm_120)**: CTranslate2/cuDNN y onnxruntime-gpu sin verificar en la 5070 Ti; PyTorch sí funciona (Kokoro corre en CUDA aquí).
- **Calidad del español** de Kokoro (G2P de espeak) en términos técnicos: ya hay un lexicon de parches.
- **Auth/ToS:** el Agent SDK y `--bare` piden API key; la doc prohíbe que terceros ofrezcan el login de claude.ai. Hermes vía OAuth solo consume créditos extra de Max.
- **Wrapper mise de `claude`** se auto-recursa en ciertos PATH: usar ruta absoluta.
- **VRAM:** voxtype (1,5 GB) + Kokoro (1 GB) + faster-whisper turbo (~1,5–2 GB, [no verificado]) caben en 16 GB, pero voxtype duplica el modelo si ambos quedan residentes.

### Preguntas abiertas

- Q1: Backend de agente por defecto: (a) Agent SDK con API key, (b) Hermes+Anthropic, (c) Hermes con su proveedor actual barato para voz y Claude solo para tareas.
- Q2: ¿Auriculares obligatorios en el MVP, o AEC desde el día 1?
- Q3: ¿Wake word / siempre escuchando, o activación por tecla (F9) que abre una ventana conversacional?
- ¿Kokoro en español suena aceptable para ti? Hay muestras en `~/.local/share/local-tts/samples`.
