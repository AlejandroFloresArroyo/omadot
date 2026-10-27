import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "OmadotBin.js" as OmadotBin

// Omi in the bar: mirrors the OmaDot daemon's state file. Left click (or Super twice) opens/closes the mic;
// right click opens the settings. Drop a file on him and he files it; a pill next to him shows a focus
// countdown or a task's progress.
Panel {
  id: root

  moduleName: "ale.omadot"
  property string mood: "off"
  property string pill: ""

  // The design's voice blue: the theme accent can be a grey that vanishes at bar size.
  readonly property color voice: "#7aa2f7"
  readonly property var moods: ["idle", "sleeping", "listening", "thinking", "working", "speaking", "done", "confused", "error", "catch", "notify", "muted"]

  readonly property string runtimeDir: Quickshell.env("XDG_RUNTIME_DIR") || "/tmp"
  readonly property string stateFile: runtimeDir + "/omadot/state"
  // Written by `omadot setup plugin`: the absolute path of the installed `omadot`.
  readonly property string omadotBin: OmadotBin.path

  implicitWidth: row.implicitWidth
  implicitHeight: button.implicitHeight

  function parse(text) {
    try {
      var data = JSON.parse(text)
      root.mood = root.moods.indexOf(data.state) >= 0 ? data.state : "off"
      root.pill = data.pill || ""
    } catch (e) {
      root.mood = "off"
      root.pill = ""
    }
  }

  function omadot(args) {
    Quickshell.execDetached([root.omadotBin].concat(args))
  }

  FileView {
    id: stateView
    path: root.stateFile
    watchChanges: true
    printErrors: false
    onLoaded: root.parse(text())
    onFileChanged: reload()
    onLoadFailed: { root.mood = "off"; root.pill = "" }
  }

  // The daemon replaces the file atomically and deletes it on exit; poll as a backstop for both.
  Timer {
    interval: 1000
    running: true
    repeat: true
    onTriggered: stateView.reload()
  }

  Process {
    id: micToggle
    command: [root.omadotBin, "toggle"]
  }

  Row {
    id: row
    height: parent.height
    spacing: 0

    // Focus countdown or task progress, next to Omi.
    Rectangle {
      visible: root.pill !== ""
      anchors.verticalCenter: parent.verticalCenter
      width: visible ? pillText.implicitWidth + Style.space(12) : 0
      height: Style.space(18)
      radius: height / 2
      color: Qt.rgba(0.478, 0.635, 0.969, 0.14)
      Text {
        id: pillText
        anchors.centerIn: parent
        text: root.pill
        color: root.voice
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
      }
    }

    BarIconButton {
      id: button
      height: parent.height
      bar: root.bar
      active: root.opened
      useActiveColor: false

      tooltipText: ({
        off: "OmaDot apagado · clic derecho: ajustes",
        idle: "Omi · Super dos veces para hablar · suelta un archivo para guardarlo",
        sleeping: "Omi duerme · Super dos veces para despertarlo",
        listening: "Omi te escucha · Super dos veces para cerrar",
        thinking: "Omi está pensando",
        working: "Omi está trabajando",
        speaking: "Omi está hablando",
        done: "Omi terminó · Super dos veces para cerrar la tarjeta",
        confused: "Omi no te entendió",
        error: "Omi no pudo responder · clic derecho: ajustes",
        catch: "Suéltalo: Omi lo guarda donde toca",
        notify: "Omi tiene algo para ti",
        muted: "Micrófono apagado · Super+Shift+. para encenderlo"
      })[root.mood] || "Omi"

      iconComponent: Component {
        Omi {
          anchors.fill: parent
          mood: root.mood
          body: button.foreground
          accent: root.voice
        }
      }

      onPressed: function(mouseButton) {
        if (mouseButton === Qt.RightButton) root.toggle()
        else if (root.mood !== "off") micToggle.running = true
      }

      // Files dragged over Omi: he looks up (catch), and a drop files them.
      DropArea {
        anchors.fill: parent
        keys: ["text/uri-list"]
        onEntered: function(drag) { if (root.mood !== "off") root.omadot(["catch", "on"]) }
        onExited: root.omadot(["catch", "off"])
        onDropped: function(drop) {
          var urls = drop.hasUrls ? drop.urls : []
          if (urls.length === 0 && drop.hasText) urls = drop.text.split("\n")
          for (var i = 0; i < urls.length; i++) {
            var u = String(urls[i]).trim()
            if (u.indexOf("file://") === 0) u = decodeURIComponent(u.substring(7))
            if (u !== "") root.omadot(["drop", u])
          }
          drop.accept()
        }
      }
    }
  }

  SettingsPanel {
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    omadotBin: root.omadotBin
    voiceColor: root.voice
  }
}
