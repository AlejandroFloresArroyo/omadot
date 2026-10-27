import QtQuick

// Omi: the Omarchy logo (54x52 grid from ~/.local/share/omarchy/icon.txt) with two block eyes.
// mood: off | idle | sleeping | listening | thinking | working | speaking | done | confused | error
//       | catch (a file hovers over him) | notify (a reminder rang) | muted (mic switched off)
Item {
  id: omi

  property string mood: "idle"
  property color body: "white"
  property color accent: "#7aa2f7"
  // Design's amber for "something went wrong" and for notifications (Tokyo Night yellow).
  property color warn: "#e0af68"
  property color dim: "#565f89"
  readonly property real u: Math.min(width / 58, height / 56)

  readonly property var blocks: [[0,0,54,4],[0,4,4,4],[25,4,4,4],[50,4,4,4],[0,8,4,4],[8,8,21,4],[38,8,8,4],[50,8,4,4],[0,12,4,4],[8,12,4,4],[42,12,4,4],[50,12,4,4],[0,16,4,4],[8,16,4,4],[42,16,4,4],[50,16,4,4],[0,20,4,4],[8,20,4,4],[42,20,4,4],[50,20,4,4],[0,24,12,4],[42,24,4,4],[50,24,4,4],[0,28,4,4],[8,28,4,4],[42,28,4,4],[50,28,4,4],[0,32,4,4],[8,32,4,4],[42,32,4,4],[50,32,4,4],[0,36,4,4],[8,36,4,4],[42,36,4,4],[50,36,4,4],[0,40,4,4],[8,40,38,4],[50,40,4,4],[0,44,4,4],[25,44,4,4],[50,44,4,4],[0,48,29,4],[34,48,20,4]]

  // Eye geometry per mood, in grid units: [x, y, w, h, dx] for the left eye; the right eye mirrors it
  // around the logo's center (x' = 54 - x - w) and then shifts by dx (to look sideways).
  // Big on purpose: at bar size (~16 px) a 1 px eye disappears.
  readonly property var eye: ({
    off: [17, 24, 7, 2.5, 0],
    error: [17, 24, 7, 2.5, 0],
    muted: [16, 24, 8, 2.5, 0],
    sleeping: [16, 27, 8, 2.5, 0],
    idle: [16, 17, 8, 13, 0],
    working: [17, 18, 6, 11, 0],
    listening: [16, 15, 8, 17, 0],
    catch: [15, 13, 9, 19, 0],
    notify: [16, 15, 8, 17, 0],
    thinking: [19, 14, 7, 8, 3],
    confused: [19, 14, 7, 8, -3],
    speaking: [16, 18, 8, 10, 0],
    done: [16, 22, 8, 4, 0]
  })[mood] || [16, 17, 8, 13, 0]

  // Everything is drawn inside a 58x56 box centered in the item (logo + 2 units of margin for the ring).
  Item {
    id: canvas
    width: 58 * omi.u
    height: 56 * omi.u
    anchors.centerIn: parent
    opacity: omi.mood === "off" ? 0.4 : 1
    rotation: omi.mood === "confused" ? -7 : 0
    Behavior on rotation { NumberAnimation { duration: 180 } }

    // Asleep: the logo dims and breathes slowly; a "z" floats up where there is room for it.
    SequentialAnimation on opacity {
      running: omi.mood === "sleeping"
      loops: Animation.Infinite
      onRunningChanged: if (!running) canvas.opacity = Qt.binding(function() { return omi.mood === "off" ? 0.4 : 1 })
      NumberAnimation { from: 0.75; to: 0.4; duration: 1800; easing.type: Easing.InOutSine }
      NumberAnimation { from: 0.4; to: 0.75; duration: 1800; easing.type: Easing.InOutSine }
    }

    // Done: a short hop. Catch: he leans up to the file.
    SequentialAnimation on y {
      running: omi.mood === "done"
      onRunningChanged: if (!running) canvas.y = Qt.binding(function() { return (omi.height - canvas.height) / 2 })
      NumberAnimation { to: (omi.height - canvas.height) / 2 - 3 * omi.u; duration: 140; easing.type: Easing.OutQuad }
      NumberAnimation { to: (omi.height - canvas.height) / 2; duration: 220; easing.type: Easing.OutBounce }
    }

    Text {
      visible: omi.mood === "sleeping" && omi.width >= 24
      text: "z"
      color: omi.body
      font.pixelSize: 16 * omi.u
      font.bold: true
      x: 44 * omi.u
      y: -6 * omi.u
    }

    Text {
      visible: omi.mood === "confused" && omi.width >= 24
      text: "?"
      color: omi.accent
      font.pixelSize: 16 * omi.u
      font.bold: true
      x: 46 * omi.u
      y: -8 * omi.u
    }

    Rectangle {
      id: ring
      visible: omi.mood === "listening" || omi.mood === "catch"
      x: 0; y: 0
      width: 58 * omi.u; height: 56 * omi.u
      radius: 4 * omi.u
      color: "transparent"
      border.color: omi.accent
      border.width: Math.max(1, 2.5 * omi.u)
      SequentialAnimation on opacity {
        running: ring.visible
        loops: Animation.Infinite
        NumberAnimation { from: 1; to: 0.25; duration: omi.mood === "catch" ? 350 : 700; easing.type: Easing.InOutSine }
        NumberAnimation { from: 0.25; to: 1; duration: omi.mood === "catch" ? 350 : 700; easing.type: Easing.InOutSine }
      }
    }

    Repeater {
      model: omi.blocks
      Rectangle {
        x: (2 + modelData[0]) * omi.u
        y: (2 + modelData[1]) * omi.u
        width: modelData[2] * omi.u
        height: modelData[3] * omi.u
        color: omi.body
        antialiasing: false
      }
    }

    Item {
      id: eyes
      anchors.fill: parent
      // Working: the eyes scan left and right.
      SequentialAnimation on x {
        running: omi.mood === "working"
        loops: Animation.Infinite
        onRunningChanged: if (!running) eyes.x = 0
        NumberAnimation { to: -2.5 * omi.u; duration: 500; easing.type: Easing.InOutSine }
        NumberAnimation { to: 2.5 * omi.u; duration: 500; easing.type: Easing.InOutSine }
        NumberAnimation { to: 0; duration: 400; easing.type: Easing.InOutSine }
      }

      Repeater {
        model: 2
        Rectangle {
          id: eyeRect
          x: (2 + (index === 0 ? omi.eye[0] : 54 - omi.eye[0] - omi.eye[2] + omi.eye[4])) * omi.u
          y: (2 + omi.eye[1]) * omi.u
          width: omi.eye[2] * omi.u
          height: omi.eye[3] * omi.u
          radius: 1 * omi.u
          color: omi.body
          Behavior on y { NumberAnimation { duration: 140 } }
          Behavior on height { NumberAnimation { duration: 140 } }
          // Idle blink every few seconds.
          SequentialAnimation on scale {
            running: omi.mood === "idle"
            loops: Animation.Infinite
            PauseAnimation { duration: 3800 }
            NumberAnimation { to: 0.1; duration: 90 }
            NumberAnimation { to: 1; duration: 110 }
          }
        }
      }
    }

    // The accent dot sits in the logo's bottom notch; it pulses while Omi thinks, works or talks.
    Rectangle {
      id: dot
      width: 4.6 * omi.u; height: width; radius: width / 2
      x: (2 + 31.5) * omi.u - width / 2
      y: (2 + 50) * omi.u - height / 2
      color: omi.mood === "error" || omi.mood === "notify" ? omi.warn : omi.mood === "muted" ? omi.dim : omi.accent
      visible: omi.mood !== "off"
      SequentialAnimation on scale {
        running: ["thinking", "working", "speaking", "notify"].indexOf(omi.mood) >= 0
        loops: Animation.Infinite
        NumberAnimation { to: 1.6; duration: omi.mood === "speaking" ? 160 : 420 }
        NumberAnimation { to: 1.0; duration: omi.mood === "speaking" ? 160 : 420 }
      }
    }
  }
}
