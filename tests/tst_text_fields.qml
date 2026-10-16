import QtQuick
import QtTest
import "../app/TextEdit.js" as Edit

// Pear's text fields are read-only to Qt and typed into by Edit.key, so they never
// read the clipboard when it changes (q_canPasteChanged only runs for editable ones).
Item {
    width: 400; height: 200
    property int pastes: 0
    property int enters: 0
    readonly property var std: ({ paste: StandardKey.Paste, selectAll: StandardKey.SelectAll, cut: StandardKey.Cut })

    TextInput {
        id: line
        width: 400; height: 30
        readOnly: true
        Keys.onPressed: ev => ev.accepted = Edit.key(line, ev, {
            paste: () => pastes++, enter: () => { enters++; return true; } }, std)
    }
    TextEdit {
        id: area
        textFormat: TextEdit.PlainText
        y: 40; width: 400; height: 100
        readOnly: true
        Keys.onPressed: ev => ev.accepted = Edit.key(area, ev, {
            multiline: true, paste: () => pastes++,
            enter: e => (e.modifiers & Qt.ShiftModifier) ? false : (enters++, true) }, std)
    }

    TestCase {
        name: "TextFields"
        when: windowShown

        function init() { line.text = ""; area.text = ""; pastes = 0; enters = 0; }

        function test_typing_and_editing() {
            line.forceActiveFocus();
            for (const k of [Qt.Key_H, Qt.Key_I, Qt.Key_Space, Qt.Key_Y, Qt.Key_O, Qt.Key_U]) keyClick(k);
            compare(line.text, "hi you");
            keyClick(Qt.Key_Backspace);
            compare(line.text, "hi yo");
            keyClick(Qt.Key_Backspace, Qt.ControlModifier);
            compare(line.text, "hi ");
            keyClick(Qt.Key_Home);
            keyClick(Qt.Key_Delete);
            compare(line.text, "i ");
            keyClick(Qt.Key_A, Qt.ControlModifier);
            keyClick(Qt.Key_X);
            compare(line.text, "x");
        }

        function test_surrogate_pair_is_one_step() {
            line.forceActiveFocus();
            line.text = "a\u{1F600}";
            line.cursorPosition = line.text.length;
            keyClick(Qt.Key_Backspace);
            compare(line.text, "a");
        }

        function test_enter_and_paste() {
            line.forceActiveFocus();
            keyClick(Qt.Key_O);
            keyClick(Qt.Key_Return);
            compare(enters, 1);
            keyClick(Qt.Key_V, Qt.ControlModifier);
            compare(pastes, 1);
            compare(line.text, "o");                 // Qt's own paste never ran
        }

        function test_composer_newline_and_send() {
            area.forceActiveFocus();
            keyClick(Qt.Key_A);
            keyClick(Qt.Key_Return, Qt.ShiftModifier);
            keyClick(Qt.Key_B);
            compare(area.text, "a\nb");
            keyClick(Qt.Key_Return);
            compare(enters, 1);
            compare(area.text, "a\nb");
        }

        function test_never_offers_paste() {
            verify(line.readOnly && !line.canPaste);
            verify(area.readOnly && !area.canPaste);
        }
    }
}
