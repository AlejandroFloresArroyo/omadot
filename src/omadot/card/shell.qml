// OmaDot invocation card: a layer-shell window under the bar, only while a turn is live (Super twice opens it).
// Its own Quickshell instance (like voxtype's OSD), started by the daemon from the installed package:
//   OMADOT_BIN=/path/to/omadot qs -p <site-packages>/omadot/card
// Reads the daemon's state file: {"state", "you", "omi", "card", "pill", "buttons"} (see state.py).
// Draws what the reply carries (files to drag, a draft, a diff, a plan…), a drop zone when a file hovers
// over Omi, and the buttons, each running `omadot …` with its arguments.

import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import Quickshell.Hyprland


ShellRoot {
  id: shell

  property string mood: "off"
  property string you: ""
  property string omi: ""
  property var card: ({})
  property var buttons: []
  property string pill: ""
  // Catch and notify are not about the last conversation: its text stays out of the way.
  readonly property bool ambient: mood === "catch" || mood === "notify"
  readonly property bool live: ["listening", "thinking", "working", "speaking", "done", "confused", "error", "catch", "notify"].indexOf(mood) >= 0
  // The daemon passes the binary it runs from; "omadot" on PATH when the card is started by hand.
  readonly property string omadotBin: Quickshell.env("OMADOT_BIN") || "omadot"

  // Colours follow the Omarchy theme (~/.local/state/omarchy/current/theme/colors.toml); Tokyo Night until it loads.
  property var theme: ({})
  function tc(key, fallback) { return shell.theme[key] || fallback }
  readonly property color bg: tc("lighter_background", "#1f2335")
  readonly property color sub: tc("background", "#24283b")
  readonly property color line: tc("muted", "#2f3549")
  readonly property color txt: tc("bright_foreground", "#e6e9f5")
  readonly property color dim: tc("foreground", "#9aa5ce")
  readonly property color accent: tc("accent", "#7aa2f7")
  readonly property color green: tc("green", "#9ece6a")
  readonly property color amber: tc("yellow", "#e0af68")
  readonly property color red: tc("red", "#f7768e")

  function parseTheme(text) {
    var out = {}
    var lines = text.split("\n")
    for (var i = 0; i < lines.length; i++) {
      var m = lines[i].match(/^\s*([a-z_]+)\s*=\s*"(#[0-9a-fA-F]{6})"/)
      if (m) out[m[1]] = m[2]
    }
    shell.theme = out
  }

  FileView {
    id: themeView
    path: (Quickshell.env("XDG_STATE_HOME") || (Quickshell.env("HOME") + "/.local/state")) + "/omarchy/current/theme/colors.toml"
    watchChanges: true
    printErrors: false
    onLoaded: shell.parseTheme(text())
    onFileChanged: reload()
  }

  // A theme switch retargets the symlink, which a file watch can miss.
  Timer {
    interval: 2000
    running: true
    repeat: true
    onTriggered: themeView.reload()
  }

  readonly property var labels: ({
    listening: "Escuchando",
    thinking: "Pensando",
    working: "Trabajando",
    speaking: "Omi",
    done: "Listo",
    confused: "No te entendí",
    error: "Algo falló",
    catch: "Omi",
    notify: "Aviso"
  })

  function notifyLabel() {
    if (shell.has("reminder")) return "Recordatorio"
    if (shell.has("task")) return "Tarea lista"
    var t = shell.has("notify") ? String(shell.card.notify.text).toLowerCase() : ""
    if (t.indexOf("enfoque") >= 0) return "Enfoque"
    return "Aviso"
  }

  function parse(text) {
    try {
      var data = JSON.parse(text)
      shell.mood = data.state || "off"
      shell.you = data.you || ""
      shell.omi = data.omi || ""
      shell.card = data.card || ({})
      shell.buttons = data.buttons || []
      shell.pill = data.pill || ""
    } catch (e) {
      shell.mood = "off"
    }
  }

  function omadot(args) {
    Quickshell.execDetached([shell.omadotBin].concat(args))
  }

  function has(key) {
    return shell.card && shell.card[key] !== undefined && shell.card[key] !== null
  }

  FileView {
    id: stateView
    path: (Quickshell.env("XDG_RUNTIME_DIR") || "/tmp") + "/omadot/state"
    watchChanges: true
    printErrors: false
    onLoaded: shell.parse(text())
    onFileChanged: reload()
    onLoadFailed: shell.mood = "off"
  }

  Timer {
    interval: 1000
    running: true
    repeat: true
    onTriggered: stateView.reload()
  }

  // ------------------------------------------------------------------ building blocks
  component Body: Text {
    width: parent ? parent.width : implicitWidth
    wrapMode: Text.WordWrap
    color: shell.txt
    font.pixelSize: 14
  }

  component Caption: Text {
    width: parent ? parent.width : implicitWidth
    wrapMode: Text.WordWrap
    color: shell.dim
    font.pixelSize: 12
  }

  component Mono: Text {
    color: shell.dim
    font.family: "JetBrainsMono Nerd Font"
    font.pixelSize: 11
    elide: Text.ElideMiddle
  }

  component Surface: Rectangle {
    width: parent ? parent.width : implicitWidth
    radius: 12
    color: shell.sub
    border.color: shell.line
    border.width: 1
  }

  component Chip: Rectangle {
    property string text: ""
    property color tint: shell.accent
    height: 22
    width: chipText.implicitWidth + 16
    radius: 11
    color: Qt.rgba(0.478, 0.635, 0.969, 0.14)
    Text {
      id: chipText
      anchors.centerIn: parent
      text: parent.text
      color: parent.tint
      font.family: "JetBrainsMono Nerd Font"
      font.pixelSize: 11
    }
  }

  component CardButton: Rectangle {
    id: btn
    property string text: ""
    property bool primary: false
    signal clicked()

    implicitWidth: label.implicitWidth + 24
    implicitHeight: label.implicitHeight + 12
    radius: 10
    color: primary ? (area.containsMouse ? "#8fb2f9" : shell.accent) : (area.containsMouse ? "#3b4261" : shell.line)

    Text {
      id: label
      anchors.centerIn: parent
      text: btn.text
      color: btn.primary ? "#1f2335" : shell.txt
      font.pixelSize: 13
      font.weight: Font.DemiBold
    }

    MouseArea {
      id: area
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: btn.clicked()
    }
  }

  // Type to Omi: Enter sends the text as a turn (`omadot ask`), like saying it.
  component TextBar: Rectangle {
    id: bar
    property string placeholder: ""
    height: 38
    radius: 12
    color: shell.sub
    border.color: field.activeFocus ? shell.accent : shell.line
    border.width: 1

    TextInput {
      id: field
      anchors.fill: parent
      anchors.leftMargin: 12
      anchors.rightMargin: 12
      verticalAlignment: TextInput.AlignVCenter
      color: shell.txt
      font.pixelSize: 14
      clip: true
      selectByMouse: true
      onAccepted: {
        var t = text.trim()
        if (t === "") return
        shell.omadot(["ask", t])
        text = ""
      }
      Keys.onEscapePressed: { text = ""; focus = false }
    }

    Text {
      anchors.fill: field
      verticalAlignment: Text.AlignVCenter
      visible: field.text === "" && !field.activeFocus
      text: bar.placeholder
      color: shell.dim
      font.pixelSize: 14
      elide: Text.ElideRight
    }

    MouseArea {
      anchors.fill: parent
      cursorShape: Qt.IBeamCursor
      onClicked: field.forceActiveFocus()
    }
  }

  // A file you can drag out of the card into any app (a real Wayland drag with the file's URI).
  component FileTile: Surface {
    id: tile
    property string path: ""
    property string label: ""
    property string meta: ""
    height: 58
    opacity: Drag.active ? 0.3 : 1

    Drag.dragType: Drag.Automatic
    Drag.supportedActions: Qt.CopyAction
    Drag.mimeData: ({ "text/uri-list": "file://" + tile.path, "text/plain": tile.path })

    Row {
      anchors.fill: parent
      anchors.margins: 12
      spacing: 12
      Item {
        width: 34; height: 34
        anchors.verticalCenter: parent.verticalCenter
        // The icon theme's file icon (Yaru-magenta in Omarchy); a glyph if the theme has none.
        readonly property string iconName: ({ pdf: "application-pdf", xlsx: "x-office-spreadsheet", xls: "x-office-spreadsheet", csv: "text-csv", ods: "x-office-spreadsheet",
          docx: "x-office-document", odt: "x-office-document", pptx: "x-office-presentation", png: "image-x-generic", jpg: "image-x-generic", jpeg: "image-x-generic",
          svg: "image-x-generic", webp: "image-x-generic", mp4: "video-x-generic", mkv: "video-x-generic", mp3: "audio-x-generic", wav: "audio-x-generic",
          zip: "application-zip", gz: "application-zip", md: "text-markdown", txt: "text-x-generic", py: "text-x-python", sh: "text-x-script", json: "text-x-generic"
        })[tile.label.split(".").pop().toLowerCase()] || "text-x-generic"
        readonly property string iconFile: Quickshell.iconPath(iconName, true)
        Image {
          anchors.fill: parent
          visible: parent.iconFile !== ""
          source: parent.iconFile
          sourceSize.width: 34
          sourceSize.height: 34
          fillMode: Image.PreserveAspectFit
        }
        Rectangle {
          anchors.fill: parent
          visible: parent.iconFile === ""
          radius: 8
          color: shell.line
          Text { anchors.centerIn: parent; text: ""; color: shell.txt; font.family: "JetBrainsMono Nerd Font"; font.pixelSize: 18 }
        }
      }
      Column {
        anchors.verticalCenter: parent.verticalCenter
        width: parent.width - 34 - 12 - 24
        spacing: 2
        Text { width: parent.width; text: tile.label; color: shell.txt; font.pixelSize: 14; elide: Text.ElideMiddle }
        Mono { width: parent.width; text: tile.meta }
      }
      Text {
        anchors.verticalCenter: parent.verticalCenter
        text: "⋮⋮"
        color: shell.dim
        font.pixelSize: 14
      }
    }

    MouseArea {
      id: grab
      property real startX: 0
      property real startY: 0
      anchors.fill: parent
      cursorShape: Qt.OpenHandCursor
      onPressed: function(mouse) {
        grab.startX = mouse.x
        grab.startY = mouse.y
        tile.grabToImage(function(result) { tile.Drag.imageSource = result.url })
      }
      onPositionChanged: function(mouse) {
        if (pressed && !tile.Drag.active && (Math.abs(mouse.x - grab.startX) > 8 || Math.abs(mouse.y - grab.startY) > 8)) tile.Drag.active = true
      }
      onReleased: tile.Drag.active = false
      onDoubleClicked: shell.omadot(["open", tile.path])
    }
  }

  // ------------------------------------------------------------------ the window
  PanelWindow {
    id: cardWindow

    // Follow the monitor you are working on.
    screen: {
      var focused = Hyprland.focusedMonitor
      for (var i = 0; i < Quickshell.screens.length; i++)
        if (focused && Quickshell.screens[i].name === focused.name) return Quickshell.screens[i]
      return Quickshell.screens[0]
    }

    // Nothing floats at rest: the card shows up when you summon Omi (or a reminder rings) and leaves when he is done.
    visible: shell.live
    anchors { top: true; right: true }
    margins { top: 8; right: 12 }
    // The card grows with its content, up to 70 % of the screen; then it scrolls.
    readonly property real maxHeight: screen ? screen.height * 0.7 : 800
    implicitWidth: 380
    implicitHeight: Math.min(column.implicitHeight + 32 + inputBar.height + 10, maxHeight)
    Behavior on implicitHeight { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
    color: "transparent"

    WlrLayershell.namespace: "omadot-card"
    WlrLayershell.layer: WlrLayer.Top
    // OnDemand: the text bar takes the keyboard only when you click it.
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.OnDemand

    Rectangle {
      anchors.fill: parent
      radius: 18
      color: shell.bg
      border.color: shell.mood === "catch" ? shell.accent : shell.line
      border.width: 1

      // Dropping anywhere on the card works like dropping on Omi in the bar.
      DropArea {
        anchors.fill: parent
        keys: ["text/uri-list"]
        onEntered: shell.omadot(["catch", "on"])
        onExited: shell.omadot(["catch", "off"])
        onDropped: function(drop) {
          var urls = drop.hasUrls ? drop.urls : (drop.hasText ? drop.text.split("\n") : [])
          for (var i = 0; i < urls.length; i++) {
            var u = String(urls[i]).trim()
            if (u.indexOf("file://") === 0) u = decodeURIComponent(u.substring(7))
            if (u !== "") shell.omadot(["drop", u])
          }
          drop.accept()
        }
      }

      // The text bar sits at the bottom, the content above it.
      TextBar {
        id: inputBar
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 16
        placeholder: "Escríbele a Omi…"
      }

      Flickable {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: inputBar.top
        anchors.margins: 16
        anchors.bottomMargin: 10
        contentHeight: column.implicitHeight
        clip: true
        interactive: contentHeight > height

        Column {
          id: column
          width: parent.width
          spacing: 10

          // Header: Omi, big and centred so his face reads, with what he is doing under him.
          Column {
            width: parent.width
            spacing: 6

            Omi {
              width: 84
              height: 84
              anchors.horizontalCenter: parent.horizontalCenter
              mood: shell.mood
              body: shell.txt
              accent: shell.accent
            }

            Text {
              width: parent.width
              horizontalAlignment: Text.AlignHCenter
              text: shell.has("approval") ? "Omi pide permiso" : shell.mood === "notify" ? shell.notifyLabel() : (shell.labels[shell.mood] || "")
              color: shell.mood === "error" ? shell.amber : shell.txt
              font.pixelSize: 15
              font.weight: Font.DemiBold
            }

            Chip {
              visible: shell.pill !== ""
              anchors.horizontalCenter: parent.horizontalCenter
              text: shell.pill
            }
          }

          // Catch: the drop zone.
          Rectangle {
            visible: shell.mood === "catch"
            width: parent.width
            height: 64
            radius: 12
            color: Qt.rgba(0.478, 0.635, 0.969, 0.12)
            border.color: shell.accent
            border.width: 2
            Text {
              anchors.centerIn: parent
              text: "Suéltalo aquí · lo guardo donde toca"
              color: shell.accent
              font.pixelSize: 14
            }
          }

          Caption { visible: shell.you !== "" && !shell.ambient; text: shell.you; font.pixelSize: 14 }

          // Notification (a reminder rang, a task finished).
          Surface {
            visible: shell.has("notify")
            height: notifyText.implicitHeight + 24
            border.color: shell.amber
            Text {
              id: notifyText
              anchors.fill: parent
              anchors.margins: 12
              text: shell.has("notify") ? shell.card.notify.text : ""
              wrapMode: Text.WordWrap
              color: shell.txt
              font.pixelSize: 15
            }
          }

          Text {
            width: parent.width
            visible: shell.omi !== "" && !shell.ambient
            text: shell.omi
            wrapMode: Text.WordWrap
            color: shell.txt
            font.pixelSize: 17
          }

          // Hermes asks permission for a tool.
          Surface {
            visible: shell.has("approval")
            height: approvalText.implicitHeight + 24
            border.color: shell.accent
            Text {
              id: approvalText
              anchors.fill: parent
              anchors.margins: 12
              text: shell.has("approval") ? shell.card.approval.question : ""
              wrapMode: Text.WordWrap
              color: shell.txt
              font.pixelSize: 14
            }
          }

          // Omi asks before acting.
          Body { visible: shell.has("ask"); text: shell.has("ask") ? shell.card.ask.question : ""; font.pixelSize: 15 }

          // The text Omi is working on (what you had selected).
          Surface {
            visible: shell.has("selection")
            height: selectionText.implicitHeight + 24
            Text {
              id: selectionText
              anchors.fill: parent
              anchors.margins: 12
              text: shell.has("selection") ? "“" + shell.card.selection + "”" : ""
              wrapMode: Text.WordWrap
              maximumLineCount: 3
              elide: Text.ElideRight
              color: shell.dim
              font.pixelSize: 13
              font.italic: true
            }
          }

          // The screenshot Omi looked at.
          Rectangle {
            visible: shell.has("shot")
            width: parent.width
            height: 110
            radius: 12
            clip: true
            color: "#16161e"
            border.color: shell.line
            Image {
              anchors.fill: parent
              source: shell.has("shot") ? "file://" + shell.card.shot : ""
              fillMode: Image.PreserveAspectCrop
              verticalAlignment: Image.AlignTop
              asynchronous: true
            }
          }

          // Files: drag them anywhere.
          Repeater {
            model: shell.has("files") ? shell.card.files : []
            FileTile {
              path: modelData.path
              label: modelData.label || modelData.path.split("/").pop()
              meta: modelData.path.replace(/^\/home\/[^/]+/, "~")
            }
          }

          // Clipboard entries.
          Repeater {
            model: shell.has("clips") ? shell.card.clips : []
            Surface {
              height: 40
              Row {
                anchors.fill: parent
                anchors.leftMargin: 10
                anchors.rightMargin: 10
                spacing: 10
                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  text: ({ text: "", link: "", image: "" })[modelData.kind] || ""
                  color: shell.dim
                  font.family: "JetBrainsMono Nerd Font"
                  font.pixelSize: 14
                }
                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  width: parent.width - 80
                  text: modelData.label
                  color: shell.txt
                  font.pixelSize: 13
                  elide: Text.ElideRight
                }
                Mono { anchors.verticalCenter: parent.verticalCenter; text: modelData.when || "" }
              }
            }
          }

          // An app Omi opened.
          Surface {
            visible: shell.has("app")
            height: 50
            Row {
              anchors.fill: parent
              anchors.margins: 10
              spacing: 10
              Text { anchors.verticalCenter: parent.verticalCenter; text: ""; color: shell.accent; font.family: "JetBrainsMono Nerd Font"; font.pixelSize: 16 }
              Column {
                anchors.verticalCenter: parent.verticalCenter
                Text { text: shell.has("app") ? (shell.card.app.name || "") : ""; color: shell.txt; font.pixelSize: 14 }
                Mono { text: shell.has("app") ? (shell.card.app.where || "") : "" }
              }
            }
          }

          // A page: the address, opened in a floating Chromium next to the card.
          Surface {
            visible: shell.has("url")
            height: 40
            Row {
              anchors.fill: parent
              anchors.leftMargin: 10
              anchors.rightMargin: 10
              spacing: 8
              Text { anchors.verticalCenter: parent.verticalCenter; text: ""; color: shell.green; font.family: "JetBrainsMono Nerd Font"; font.pixelSize: 12 }
              Mono { anchors.verticalCenter: parent.verticalCenter; width: parent.width - 30; text: shell.has("url") ? shell.card.url : ""; color: shell.txt; font.pixelSize: 12 }
            }
          }

          // Mail, ranked.
          Repeater {
            model: shell.has("mails") ? shell.card.mails : []
            Surface {
              height: 54
              Row {
                anchors.fill: parent
                anchors.margins: 10
                spacing: 10
                Rectangle {
                  anchors.verticalCenter: parent.verticalCenter
                  width: 8; height: 8; radius: 4
                  color: modelData.tag === "urgent" ? shell.red : modelData.tag === "today" ? shell.amber : shell.dim
                }
                Column {
                  anchors.verticalCenter: parent.verticalCenter
                  width: parent.width - 80
                  Text { width: parent.width; text: modelData.from || ""; color: shell.txt; font.pixelSize: 13; elide: Text.ElideRight }
                  Text { width: parent.width; text: modelData.subject || ""; color: shell.dim; font.pixelSize: 12; elide: Text.ElideRight }
                }
                Mono {
                  anchors.verticalCenter: parent.verticalCenter
                  text: modelData.tag || ""
                  color: modelData.tag === "urgent" ? shell.red : modelData.tag === "today" ? shell.amber : shell.dim
                }
              }
            }
          }

          // A draft, never sent without you.
          Column {
            visible: shell.has("draft")
            width: parent.width
            spacing: 6
            Mono { text: "BORRADOR · " + (shell.has("draft") ? (shell.card.draft.to || "") : "") }
            Rectangle {
              width: parent.width
              height: draftText.implicitHeight + 24
              radius: 12
              color: "#16161e"
              border.color: shell.accent
              Text {
                id: draftText
                anchors.fill: parent
                anchors.margins: 12
                text: shell.has("draft") ? (shell.card.draft.text || "") : ""
                wrapMode: Text.WordWrap
                color: shell.txt
                font.pixelSize: 14
              }
            }
          }

          // A reminder Omi set (or one that just rang).
          Row {
            visible: shell.has("reminder")
            spacing: 8
            Text { anchors.verticalCenter: parent.verticalCenter; text: ""; color: shell.dim; font.family: "JetBrainsMono Nerd Font"; font.pixelSize: 14 }
            Text { anchors.verticalCenter: parent.verticalCenter; text: shell.has("reminder") ? (shell.card.reminder.text || "") : ""; color: shell.txt; font.pixelSize: 14 }
            Chip { anchors.verticalCenter: parent.verticalCenter; text: shell.has("reminder") ? String(shell.card.reminder.at || "").replace("T", " ") : "" }
          }

          // Now playing.
          Surface {
            visible: shell.has("now_playing")
            height: 62
            Row {
              anchors.fill: parent
              anchors.margins: 10
              spacing: 12
              Rectangle { width: 42; height: 42; radius: 8; color: shell.accent; opacity: 0.6 }
              Column {
                anchors.verticalCenter: parent.verticalCenter
                Text { text: shell.has("now_playing") ? (shell.card.now_playing.title || "") : ""; color: shell.txt; font.pixelSize: 14 }
                Text { text: shell.has("now_playing") ? (shell.card.now_playing.artist || "") : ""; color: shell.dim; font.pixelSize: 12 }
              }
            }
          }

          // Before and after.
          Column {
            visible: shell.has("rewrite")
            width: parent.width
            spacing: 6
            Text { width: parent.width; text: shell.has("rewrite") ? (shell.card.rewrite.before || "") : ""; wrapMode: Text.WordWrap; color: shell.dim; font.pixelSize: 13; font.strikeout: true }
            Rectangle {
              width: parent.width
              height: afterText.implicitHeight + 24
              radius: 12
              color: "#16161e"
              border.color: shell.has("rewrite") && shell.card.rewrite.replaced ? shell.green : shell.accent
              Text {
                id: afterText
                anchors.fill: parent
                anchors.margins: 12
                text: shell.has("rewrite") ? (shell.card.rewrite.after || "") : ""
                wrapMode: Text.WordWrap
                color: shell.txt
                font.pixelSize: 14
              }
            }
          }

          // A code change Omi proposes.
          Rectangle {
            visible: shell.has("diff")
            width: parent.width
            height: diffColumn.implicitHeight + 20
            radius: 12
            color: "#16161e"
            Column {
              id: diffColumn
              anchors.fill: parent
              anchors.margins: 10
              Repeater {
                model: shell.has("diff") ? shell.card.diff : []
                Text {
                  width: parent.width
                  text: (modelData.sign || " ") + " " + (modelData.text || "")
                  color: modelData.sign === "+" ? shell.green : modelData.sign === "-" ? shell.red : shell.dim
                  font.family: "JetBrainsMono Nerd Font"
                  font.pixelSize: 12
                  elide: Text.ElideRight
                }
              }
            }
          }

          // A device.
          Surface {
            visible: shell.has("device")
            height: 54
            Row {
              anchors.fill: parent
              anchors.margins: 10
              spacing: 10
              Text { anchors.verticalCenter: parent.verticalCenter; text: "󰂯"; color: shell.accent; font.family: "JetBrainsMono Nerd Font"; font.pixelSize: 16 }
              Column {
                anchors.verticalCenter: parent.verticalCenter
                Text { text: shell.has("device") ? (shell.card.device.name || "") : ""; color: shell.txt; font.pixelSize: 14 }
                Text { text: shell.has("device") ? (shell.card.device.detail || "") : ""; color: shell.dim; font.pixelSize: 12 }
              }
            }
          }

          // A plan for the day.
          Repeater {
            model: shell.has("plan") ? shell.card.plan : []
            Row {
              spacing: 12
              Mono { width: 44; text: modelData.time || "" }
              Text { text: modelData.title || ""; color: shell.txt; font.pixelSize: 14 }
            }
          }

          // Long work in progress.
          Column {
            visible: shell.has("task")
            width: parent.width
            spacing: 8
            Row {
              width: parent.width
              Text { width: parent.width - 60; text: shell.has("task") ? (shell.card.task.label || "") : ""; color: shell.txt; font.pixelSize: 13; elide: Text.ElideRight }
              Mono { width: 60; horizontalAlignment: Text.AlignRight; text: shell.has("task") ? (shell.card.task.detail || Math.round(shell.card.task.progress || 0) + "%") : "" }
            }
            Rectangle {
              width: parent.width
              height: 6
              radius: 3
              color: shell.line
              Rectangle {
                width: parent.width * (shell.has("task") ? Math.min(100, shell.card.task.progress || 0) / 100 : 0)
                height: parent.height
                radius: 3
                color: shell.accent
                Behavior on width { NumberAnimation { duration: 300 } }
              }
            }
          }

          // Buttons: each runs `omadot …`.
          Flow {
            width: parent.width
            spacing: 8
            visible: shell.buttons.length > 0
            Repeater {
              model: shell.buttons
              CardButton {
                text: modelData.label
                primary: modelData.primary === true
                onClicked: shell.omadot(modelData.args)
              }
            }
          }

          Caption {
            visible: shell.mood === "listening" && shell.you === ""
            text: "Habla cuando quieras · Super dos veces para cerrar"
          }

          Caption {
            visible: shell.mood === "done" && shell.buttons.length > 0
            text: "Super dos veces para cerrar"
          }

          // The design's "Hermes no responde": retry now, or open settings.
          Row {
            visible: shell.mood === "error"
            spacing: 8

            CardButton {
              text: "Reintentar ahora"
              primary: true
              onClicked: shell.omadot(["retry"])
            }

            CardButton {
              text: "Ajustes"
              onClicked: Quickshell.execDetached(["omarchy-shell", "shell", "summon", "ale.omadot", ""])
            }
          }
        }
      }
    }
  }
}
