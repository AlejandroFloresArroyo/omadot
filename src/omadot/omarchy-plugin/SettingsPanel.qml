import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// OmaDot settings, under Omi in the bar (right click). Reads everything from `omadot status` when it
// opens and writes each change with `omadot set KEY VALUE`, which the daemon applies at once.
KeyboardPanel {
  id: popup

  property string omadotBin: ""
  property color voiceColor: "#7aa2f7"
  property var status: null
  readonly property var settings: status ? status.settings : ({})
  property real speedPreview: settings.speed || 1.0

  readonly property var voiceNames: ({ ef_dora: "Dora", em_alex: "Alex", em_santa: "Santa" })

  focusTarget: keyCatcher
  contentWidth: popup.fittedContentWidth(Style.space(340))
  contentHeight: popup.fittedContentHeight(content.implicitHeight)

  onOpenChanged: if (open) statusProc.running = true

  function set(key, value) {
    var next = Object.assign({}, popup.status)
    next.settings = Object.assign({}, popup.settings)
    next.settings[key] = value
    popup.status = next
    Quickshell.execDetached([popup.omadotBin, "set", key, String(value)])
  }

  component Caption: Text {
    width: parent ? parent.width : implicitWidth
    color: Qt.darker(Color.foreground, 1.5)
    font.family: Style.font.family
    font.pixelSize: Style.font.caption
    wrapMode: Text.WordWrap
  }

  component Label: Text {
    color: Color.foreground
    font.family: Style.font.family
    font.pixelSize: Style.font.body
  }

  PanelKeyCatcher {
    id: keyCatcher
    anchors.fill: parent
    onCloseRequested: popup.close()

    // Inside an Item: KeyboardPanel's default property only takes visual items.
    Process {
      id: statusProc
      command: [popup.omadotBin, "status"]
      stdout: StdioCollector {
        waitForEnd: true
        onStreamFinished: {
          try {
            popup.status = JSON.parse(text)
            popup.speedPreview = popup.status.settings.speed
          } catch (e) {
            popup.status = null
          }
        }
      }
    }

    Column {
      id: content
      width: parent.width
      spacing: Style.space(8)

      Row {
        spacing: Style.space(10)

        Omi {
          width: Style.space(28)
          height: Style.space(28)
          mood: popup.status && popup.status.running ? "idle" : "off"
          body: Color.foreground
          accent: popup.voiceColor
        }

        Column {
          anchors.verticalCenter: parent.verticalCenter
          Label {
            text: "OmaDot"
            font.pixelSize: Style.font.title
            font.bold: true
          }
          Caption {
            width: implicitWidth
            text: popup.status === null ? "Leyendo ajustes…"
              : popup.status.running ? "Omi está activo"
              : "Omi está apagado: los cambios se aplican al arrancar"
          }
        }
      }

      PanelSeparator {}

      PanelSectionHeader { text: "Voz" }

      ButtonGroup {
        options: (popup.status ? popup.status.voices : []).map(function(v) {
          return { value: v, label: popup.voiceNames[v] || v }
        })
        value: popup.settings.voice || ""
        onChanged: function(v) { popup.set("voice", v) }
      }

      Item {
        width: parent.width
        implicitHeight: speedLabel.implicitHeight
        Label { id: speedLabel; text: "Velocidad" }
        Label {
          anchors.right: parent.right
          text: popup.speedPreview.toFixed(2).replace(/0$/, "") + "×"
        }
      }

      PanelSlider {
        bar: popup.bar
        width: parent.width
        minimum: popup.status ? popup.status.speedRange[0] : 0.8
        maximum: popup.status ? popup.status.speedRange[1] : 1.4
        step: 0.05
        value: popup.settings.speed || 1.0
        onMoved: function(v) { popup.speedPreview = v }
        onReleased: function(v) { popup.set("speed", Math.round(v * 20) / 20) }
      }

      PanelSectionHeader { text: "Motor de voz" }

      ButtonGroup {
        options: (popup.status ? popup.status.engines : ["kokoro", "omni"]).map(function(e) {
          return { value: e, label: e === "omni" ? "OmniVoice" : "Kokoro" }
        })
        value: popup.settings.tts || "kokoro"
        onChanged: function(v) { popup.set("tts", v) }
      }

      Caption {
        text: popup.settings.tts === "omni"
          ? "Más natural, ~0,7 s por frase. Voz: " + (popup.settings.omni_voice || "") + " (omadot set omni_voice …)"
          : "Rápido (~0,1 s por frase). Las voces de arriba son de Kokoro."
      }

      Toggle {
        width: parent.width
        label: "Carril rápido"
        description: "Opcional: lo trivial lo responde Haiku (tu cuenta de Claude vía omp); el resto va a Hermes"
        checked: popup.settings.fast === true
        onClicked: popup.set("fast", !checked)
      }

      Toggle {
        width: parent.width
        label: "Responder en voz"
        description: "Si lo apagas, Omi solo responde por escrito en la tarjeta"
        checked: popup.settings.speak !== false
        onClicked: popup.set("speak", !checked)
      }

      PanelSeparator {}

      PanelSectionHeader { text: "Hermes" }

      Row {
        spacing: Style.space(8)
        Rectangle {
          anchors.verticalCenter: parent.verticalCenter
          width: Style.space(8)
          height: width
          radius: width / 2
          color: popup.status === null ? Color.muted
            : popup.status.hermes.connected ? popup.voiceColor : Color.urgent
        }
        Label {
          text: popup.status === null ? "…"
            : popup.status.hermes.connected ? "Conectado" : "Sin conexión"
        }
      }

      Caption {
        visible: popup.status !== null
        text: popup.status ? "Perfil " + popup.status.hermes.profile + " · "
          + (popup.status.hermes.model || "modelo desconocido")
          + (popup.status.hermes.provider ? " (" + popup.status.hermes.provider + ")" : "")
          + "\n" + popup.status.hermes.endpoint : ""
      }

      PanelSeparator {}

      PanelSectionHeader { text: "Atajo e idioma" }

      Label {
        text: popup.status && popup.status.shortcut ? popup.status.shortcut : "Sin atajo"
      }

      Caption {
        text: "Se cambia en ~/.config/hypr/bindings.lua. Omi responde en el idioma en que le hablas."
      }

      PanelSeparator {}

      Toggle {
        width: parent.width
        label: "Mostrar la transcripción"
        description: popup.settings.speak === false
          ? "Sin voz, la tarjeta siempre muestra la respuesta"
          : "Lo que dijiste y la respuesta, en la tarjeta"
        checked: popup.settings.transcript !== false
        enabled: popup.settings.speak !== false
        opacity: enabled ? 1 : 0.5
        onClicked: popup.set("transcript", !checked)
      }

      Toggle {
        width: parent.width
        label: "Dormir tras " + (popup.status ? popup.status.sleepAfterMin : 10) + " min"
        description: "Cierra el micrófono si nadie habla"
        checked: popup.settings.sleep !== false
        onClicked: popup.set("sleep", !checked)
      }
    }
  }
}
