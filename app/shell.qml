//@ pragma UseQApplication
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Effects
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui as O

// Pear Messages. This window is only a view of pear-messagesd: it connects to the daemon's
// socket, shows what it is told, and asks it to send. Closing the window changes nothing -
// the daemon keeps receiving, storing and notifying.
ShellRoot {
    id: root

    property var threads: []
    property string query: ""
    readonly property var shownThreads: {
        const q = root.query.trim().toLowerCase();
        if (!q) return root.threads;
        return root.threads.filter(t => (t.title || "").toLowerCase().indexOf(q) >= 0
                                        || (t.preview || "").toLowerCase().indexOf(q) >= 0
                                        || (t.address || "").indexOf(q) >= 0);
    }
    property string current: ""
    property var currentInfo: null
    property var msgs: []
    property var status: ({})
    property bool statusSeen: false
    property bool settingsOpen: false
    property bool composing: false
    property string composeTo: ""
    property string composeToName: ""
    property var searchResults: []
    property var pair: ({})
    property var bbTest: ({})
    property bool bbTesting: false
    property var bbFound: null
    property bool bbFinding: false
    property var attachments: ({})
    property string flash: ""
    property int reqId: 1

    // reactions / effects / previews
    property var picker: null            // {id, x, y, mine} - the reaction picker, when open
    property int newBelow: 0             // messages that arrived while you were scrolled up
    property bool effectPickerOpen: false
    property string pendingEffect: ""    // effect for the next send
    property var fxPlay: ({})            // message id -> nonce; a change replays its bubble effect
    property string selAtt: ""           // attachment guid clicked last (Space previews it)
    property var hoverAtt: null          // attachment under the pointer
    property string pendingPreview: ""   // guid to preview once it has downloaded
    property string builtinPreview: ""   // path shown in our own preview (no Sushi)
    readonly property var abilities: status.abilities || ({ reactions: false, effects: false, reason: "Not connected." })

    readonly property var effectNames: ({
        "com.apple.MobileSMS.expressivesend.impact": "Slam",
        "com.apple.MobileSMS.expressivesend.loud": "Loud",
        "com.apple.MobileSMS.expressivesend.gentle": "Gentle",
        "com.apple.MobileSMS.expressivesend.invisibleink": "Invisible Ink",
        "com.apple.messages.effect.CKEchoEffect": "Echo",
        "com.apple.messages.effect.CKSpotlightEffect": "Spotlight",
        "com.apple.messages.effect.CKHappyBirthdayEffect": "Balloons",
        "com.apple.messages.effect.CKConfettiEffect": "Confetti",
        "com.apple.messages.effect.CKHeartEffect": "Love",
        "com.apple.messages.effect.CKLasersEffect": "Lasers",
        "com.apple.messages.effect.CKFireworksEffect": "Fireworks",
        "com.apple.messages.effect.CKSparklesEffect": "Celebration",
        "com.apple.messages.effect.CKShootingStarEffect": "Shooting Star",
        "text:big": "Big", "text:small": "Small", "text:shake": "Shake", "text:nod": "Nod",
        "text:explode": "Explode", "text:ripple": "Ripple", "text:bloom": "Bloom", "text:jitter": "Jitter"
    })
    readonly property var textEffects: ["text:big", "text:small", "text:shake", "text:nod",
                                        "text:explode", "text:ripple", "text:bloom", "text:jitter"]
    // Formatted text (bold, italic, underline, strikethrough) as rich text.
    function styledHtml(segments, linkColor) {
        let out = "";
        for (const seg of segments) {
            let t = root.linkify(seg.text, linkColor);
            const st = seg.styles || [];
            if (st.indexOf("bold") >= 0) t = "<b>" + t + "</b>";
            if (st.indexOf("italic") >= 0) t = "<i>" + t + "</i>";
            if (st.indexOf("underline") >= 0) t = "<u>" + t + "</u>";
            if (st.indexOf("strikethrough") >= 0) t = "<s>" + t + "</s>";
            out += t;
        }
        return out;
    }
    readonly property var bubbleEffects: ["com.apple.MobileSMS.expressivesend.impact", "com.apple.MobileSMS.expressivesend.loud",
                                          "com.apple.MobileSMS.expressivesend.gentle", "com.apple.MobileSMS.expressivesend.invisibleink"]
    readonly property var screenEffects: ["com.apple.messages.effect.CKEchoEffect", "com.apple.messages.effect.CKSpotlightEffect",
                                          "com.apple.messages.effect.CKHappyBirthdayEffect", "com.apple.messages.effect.CKConfettiEffect",
                                          "com.apple.messages.effect.CKHeartEffect", "com.apple.messages.effect.CKLasersEffect",
                                          "com.apple.messages.effect.CKFireworksEffect", "com.apple.messages.effect.CKSparklesEffect",
                                          "com.apple.messages.effect.CKShootingStarEffect"]
    readonly property var reactionKinds: ["love", "like", "dislike", "laugh", "emphasize", "question"]

    // Messages plays some screen effects by itself when it sees a phrase ("Happy birthday!" ->
    // balloons). Nothing is stored for those - every device spots the phrase on its own - so
    // this does the same. Most specific first.
    readonly property var keywordEffects: [
        [/happy (chinese|lunar) new year|恭喜发财|新年快乐/i, "com.apple.messages.effect.CKSparklesEffect"],
        [/happy new year|bonne ann[ée]e|feliz a[ñn]o nuevo|an nou fericit|la mul[țt]i ani .*an nou|frohes neues jahr|felice anno nuovo|gelukkig nieuwjaar/i, "com.apple.messages.effect.CKFireworksEffect"],
        [/happy birthday|joyeux anniversaire|feliz cumplea[ñn]os|feliz anivers[áa]rio|buon compleanno|alles gute zum geburtstag|la mul[țt]i ani|gefeliciteerd met je verjaardag|с днем рождения|с днём рождения/i, "com.apple.messages.effect.CKHappyBirthdayEffect"],
        [/congratulations|\bcongrats\b|f[ée]licitations|felicidades|felicit[ăa]ri|herzlichen gl[üu]ckwunsch|congratulazioni|parab[ée]ns|selamat|gefeliciteerd/i, "com.apple.messages.effect.CKConfettiEffect"],
        [/pew pew/i, "com.apple.messages.effect.CKLasersEffect"]
    ]
    function autoEffect(text) {
        for (const [re, id] of root.keywordEffects) if (re.test(text || "")) return id;
        return "";
    }

    function playEffect(m, auto) {
        const effect = (m && m.effect) || (auto ? root.autoEffect(m && m.text) : "");
        if (!effect) return;
        if (screenFx.isScreen(effect)) { screenFx.play(effect, m.text); return; }
        const f = Object.assign({}, root.fxPlay);
        f[m.id] = Date.now();
        root.fxPlay = f;
    }

    // Why a reaction can't be sent to this message, or "" when it can.
    function reactBlock(m) {
        if (!root.abilities.reactions) return root.abilities.reason;
        if (!m.guid) return "This message only came through your iPhone, so your Mac doesn't know it yet. Try again in a moment.";
        return "";
    }

    function isVideo(att) {
        return (att.mime || "").indexOf("video/") === 0 || /\.(mov|mp4|m4v|avi|mkv|webm|3gp)$/i.test(att.name || "");
    }
    // Photos more than 200 messages up the conversation only ever go to the temp folder.
    function attTemp(att, msgIndex) { return root.isVideo(att) || msgIndex < root.msgs.length - 200; }

    function previewAttachment(att, msgIndex) {
        if (!att || !att.guid) return false;
        root.selAtt = att.guid;
        const st = root.attachments[att.guid];
        if (st && st.path) { root.send({ op: "preview", path: st.path }); return true; }
        root.pendingPreview = att.guid;
        root.needAttachment(att, msgIndex);
        return true;
    }
    function spacePreview() {
        const target = root.hoverAtt || root.findAtt(root.selAtt);
        if (!target) return false;
        return root.previewAttachment(target.att, target.index);
    }
    function findAtt(guid) {
        if (!guid) return null;
        for (let i = root.msgs.length - 1; i >= 0; i--)
            for (const a of (root.msgs[i].attachments || []))
                if (a.guid === guid) return { att: a, index: i };
        return null;
    }

    readonly property string route: status.route || "none"
    readonly property var bb: status.bluebubbles || ({})
    readonly property var phone: status.iphone || ({})
    readonly property bool daemonUp: sock.connected || root.preview !== ""

    // Development: PEAR_MESSAGES_PREVIEW=threads|settings|pair|compose draws made-up data
    // without a daemon; with PEAR_MESSAGES_SNAPSHOT=<png> the window renders itself to that
    // file and quits (run under QT_QPA_PLATFORM=offscreen and nothing appears on screen).
    readonly property string preview: Quickshell.env("PEAR_MESSAGES_PREVIEW") || ""
    readonly property string snapshotPath: Quickshell.env("PEAR_MESSAGES_SNAPSHOT") || ""
    readonly property string openOnStart: Quickshell.env("PEAR_MESSAGES_OPEN") || ""

    // ------------------------------------------------------------------ daemon link
    Socket {
        id: sock
        path: (Quickshell.env("XDG_RUNTIME_DIR") || "/run/user/1000") + "/pear-messages/sock"
        connected: root.preview === ""
        parser: SplitParser {
            splitMarker: "\n"
            onRead: data => root.onEvent(data)
        }
        onConnectedChanged: {
            if (connected) {
                root.send({ op: "hello" });
                if (root.current) root.send({ op: "open", thread: root.current });
                root.reportView();
            } else if (root.preview === "") reconnect.start();
        }
        onError: if (root.preview === "") reconnect.start()
    }
    // ------------------------------------------------------------------ Omarchy theme
    // Omarchy pushes a theme switch to its own shell over IPC (`shell applyTheme`); a standalone
    // window never hears it and would keep the theme it started with. So this watches the
    // current theme itself and applies a switch the way the shell does: reload colors.toml and
    // shell.toml into Omarchy's Color singleton, then refresh Style. Everything here is drawn
    // from those, so the whole window follows.
    readonly property string themeDir: Quickshell.env("HOME") + "/.local/state/omarchy/current"
    property string appliedTheme: ""
    FileView {
        id: themeColors
        path: root.themeDir + "/theme/colors.toml"
        blockLoading: true
        watchChanges: true
        printErrors: false
        onFileChanged: themeSettle.restart()
    }
    FileView {
        id: themeShell
        path: root.themeDir + "/theme/shell.toml"
        blockLoading: true
        watchChanges: true
        printErrors: false
        onFileChanged: themeSettle.restart()
    }
    // theme-set replaces files one after another; wait for it to finish before applying.
    Timer { id: themeSettle; interval: 300; onTriggered: root.applyTheme() }
    // A file that's deleted and recreated can lose its watch, so also look every few seconds.
    Timer {
        interval: 3000; repeat: true; running: true
        onTriggered: {
            themeColors.reload();
            if (themeColors.text() !== root.appliedTheme) root.applyTheme();
        }
    }
    function applyTheme() {
        themeColors.reload();
        themeShell.reload();
        const colors = themeColors.text();
        if (!colors) return;
        root.appliedTheme = colors;
        Color.loadColors(colors);
        Color.loadShell(themeShell.text() || "");
        Style.scheduleRefresh();
    }

    // For testing from a shell:  quickshell ipc -p <app dir> call pearmessages effect Confetti
    IpcHandler {
        target: "pearmessages"
        function effect(name: string): string {
            const id = Object.keys(root.effectNames).find(k => root.effectNames[k].toLowerCase() === name.toLowerCase());
            if (!id) return "unknown effect";
            root.playEffect({ id: -1, text: "Pear Messages", effect: id });
            return "playing " + root.effectNames[id] + "; overlay=" + screenFx.current + " visible=" + screenFx.visible
                   + " size=" + screenFx.width + "x" + screenFx.height;
        }
        function state(): string {
            return JSON.stringify({ theme: { bg: String(Theme.bg), fg: String(Theme.fg), accent: String(Theme.accent),
                                             panel: String(Theme.panel), font: Theme.uiFont, radius: Theme.radius },
                                    connected: sock.connected, threads: root.threads.length,
                                    current: root.current, msgs: root.msgs.length, fx: screenFx.current,
                                    focused: root.focused, onScreen: root.onScreen,
                                    auto: root.autoEffect("Happy New Year!") });
        }
    }

    // Tells us when fingers touch the touchpad - the one event Qt's Wayland client never
    // delivers (see touch_watch.py). Prints only "touch".
    Process {
        running: root.preview === ""
        command: ["/usr/bin/python3", decodeURIComponent(Qt.resolvedUrl("touch_watch.py").toString().replace(/^file:\/\//, ""))]
        stdout: SplitParser {
            splitMarker: "\n"
            onRead: function (line) {
                if (line !== "touch") return;
                threadPhys.catchCoast(); msgPhys.catchCoast(); settingsPhys.catchCoast();
            }
        }
    }

    // Keep trying while the service is away (restarting, not started yet). A single retry
    // could land before it was back, and then nothing ever tried again.
    Timer {
        id: reconnect
        interval: 2000
        repeat: true
        running: !sock.connected && root.preview === ""
        onTriggered: { sock.connected = false; sock.connected = true; }
    }

    function send(obj) {
        if (root.preview !== "") return 0;
        const id = root.reqId++;
        obj.id = id;
        sock.write(JSON.stringify(obj) + "\n");
        sock.flush();
        return id;
    }

    // Messages count as read only while you can actually see them: the window has keyboard focus
    // and the conversation is on screen (not behind Settings, not while composing a new one).
    readonly property bool focused: win.visible && Qt.application.state === Qt.ApplicationActive
    readonly property string onScreen: (root.composing || root.settingsOpen) ? "" : root.current
    onFocusedChanged: root.reportView()
    onOnScreenChanged: root.reportView()
    function reportView() {
        root.send({ op: "view", thread: root.onScreen, active: root.focused === true });
    }

    function onEvent(line) {
        let d = null;
        try { d = JSON.parse(line); } catch (e) { return; }
        switch (d.ev) {
        case "status":
            root.status = d;
            if (!root.statusSeen) {
                root.statusSeen = true;
                if (!d.bluebubbles.configured && !d.iphone.address) root.settingsOpen = true;
            }
            break;
        case "threads":
            root.threads = d.threads;
            if (root.current) {
                const t = d.threads.find(x => x.id === root.current);
                if (t) root.currentInfo = t;
            } else if (!root.composing && !root.settingsOpen && d.threads.length && !root.openOnStart) {
                root.openThread(d.threads[0].id);
            }
            if (root.openOnStart && !root.current) root.openThread(root.openOnStart);
            break;
        case "thread":
            if (d.thread === root.current) {
                root.msgs = d.messages;
                if (d.info) root.currentInfo = d.info;
                list.stickToEnd();
            }
            break;
        case "older":
            if (d.thread === root.current && d.messages.length) {
                const keep = list.contentHeight - list.contentY;
                root.msgs = d.messages.concat(root.msgs);
                Qt.callLater(() => list.contentY = list.contentHeight - keep);
            }
            break;
        case "messages": {
            let changed = false;
            const next = root.msgs.slice();
            for (const m of d.messages) {
                const i = next.findIndex(x => x.id === m.id);
                if (m.hidden) {                    // the received copy of a message you sent yourself
                    if (i >= 0) { next.splice(i, 1); changed = true; }
                    continue;
                }
                if (m.thread === root.current) {
                    if (i >= 0) next[i] = m; else next.push(m);
                    changed = true;
                    // A message arriving now (or one just sent) plays its effect, as on the phone.
                    if (i < 0 && !m.fromMe && !list.followEnd) root.newBelow++;
                    if (i < 0 && Date.now() / 1000 - m.ts < 90 && (m.effect || root.autoEffect(m.text)))
                        Qt.callLater(() => root.playEffect(m, true));
                } else if (i >= 0) {           // merged into another thread (a group chat)
                    next.splice(i, 1);
                    changed = true;
                }
            }
            if (changed) {
                next.sort((a, b) => a.ts - b.ts);
                const atEnd = list.followEnd || list.atYEnd || list.contentHeight <= list.height;
                root.msgs = next;
                if (atEnd) list.stickToEnd();
                if (root.focused && root.onScreen) root.send({ op: "view", thread: root.onScreen, active: true });
            }
            break;
        }
        case "sent":
            if (!d.ok) root.flash = d.error || "Couldn't send";
            else if (root.composing && d.thread) { root.composing = false; root.openThread(d.thread); }
            break;
        case "search":
            if (d.q === toField.text.trim()) root.searchResults = d.results;
            break;
        case "pair":
            root.pair = d;
            break;
        case "bb_test":
            root.bbTesting = false;
            root.bbTest = d;
            if (d.ok) bbPassword.text = "";
            break;
        case "bb_found":
            root.bbFinding = false;
            root.bbFound = d.servers;
            break;
        case "attachment": {
            const a = Object.assign({}, root.attachments);
            a[d.guid] = d.path ? { path: d.path, temp: !!d.temp } : { error: d.error || "failed" };
            root.attachments = a;
            if (d.path && root.pendingPreview === d.guid) { root.pendingPreview = ""; root.send({ op: "preview", path: d.path }); }
            break;
        }
        case "preview":
            // No Sushi: our own preview for pictures, the default app for everything else.
            if (/\.(png|jpe?g|gif|webp|heic|bmp|tiff?)$/i.test(d.path)) root.builtinPreview = root.builtinPreview === d.path ? "" : d.path;
            else Qt.openUrlExternally("file://" + d.path);
            break;
        case "open":
            if (d.thread) root.openThread(d.thread);
            break;
        case "error":
            root.flash = d.error;
            break;
        }
    }

    function openThread(id) {
        root.newBelow = 0;
        root.composing = false;
        root.picker = null;
        root.effectPickerOpen = false;
        root.pendingEffect = "";
        root.hoverAtt = null;
        root.settingsOpen = false;
        if (id !== root.current) {
            root.current = id;
            root.msgs = [];
            root.currentInfo = root.threads.find(t => t.id === id) || null;
        }
        root.send({ op: "open", thread: id });
        root.reportView();
        composer.forceActiveFocus();
    }

    function startCompose() {
        root.settingsOpen = false;
        root.composing = true;
        root.composeTo = "";
        root.composeToName = "";
        root.searchResults = [];
        root.msgs = [];
        root.current = "";
        root.currentInfo = null;
        toField.text = "";
        toField.forceActiveFocus();
        root.reportView();
    }

    function sendCurrent() {
        const text = composer.text.trim();
        if (!text) return;
        if (root.composing) {
            const to = root.composeTo || toField.text.trim();
            if (!to) { root.flash = "Who is this to?"; toField.forceActiveFocus(); return; }
            root.send({ op: "send", to: to, text: text, effect: root.pendingEffect });
        } else if (root.current) {
            root.send({ op: "send", thread: root.current, text: text, effect: root.pendingEffect });
        } else return;
        composer.text = "";
        root.pendingEffect = "";
        root.effectPickerOpen = false;
    }

    function needAttachment(att, msgIndex) {
        if (!att.guid) return;
        const cur = root.attachments[att.guid];
        if (cur && (cur.path || cur.loading)) return;
        const a = Object.assign({}, root.attachments);
        a[att.guid] = { loading: true };
        root.attachments = a;
        root.send({ op: "attachment", guid: att.guid, name: att.name, mime: att.mime,
                    temp: root.attTemp(att, msgIndex === undefined ? root.msgs.length : msgIndex) });
    }

    // ------------------------------------------------------------------ formatting
    function initials(name) {
        const s = (name || "").replace(/[^\p{L}\p{N} ]/gu, "").trim();
        if (!s) return "#";
        if (/^[+\d ]+$/.test(name || "")) return "#";
        const p = s.split(/\s+/);
        return (p[0][0] + (p.length > 1 ? p[p.length - 1][0] : "")).toUpperCase();
    }
    function dayStart(d) { return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime(); }
    function listTime(ts) {
        if (!ts) return "";
        const d = new Date(ts * 1000), now = new Date();
        const days = (dayStart(now) - dayStart(d)) / 86400000;
        if (days < 1) return Qt.formatTime(d, "HH:mm");
        if (days < 2) return "Yesterday";
        if (days < 7) return Qt.formatDate(d, "dddd");
        return Qt.formatDate(d, d.getFullYear() === now.getFullYear() ? "d MMM" : "d MMM yyyy");
    }
    function stampTime(ts) {
        const d = new Date(ts * 1000), now = new Date();
        const days = (dayStart(now) - dayStart(d)) / 86400000;
        const t = Qt.formatTime(d, "HH:mm");
        if (days < 1) return "Today " + t;
        if (days < 2) return "Yesterday " + t;
        if (days < 7) return Qt.formatDate(d, "dddd") + " " + t;
        return Qt.formatDate(d, "d MMM yyyy") + " " + t;
    }
    function escapeHtml(s) {
        return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }
    function linkify(s, color) {
        return escapeHtml(s).replace(/(https?:\/\/[^\s<]+[^\s<.,;:!?)\]'"])/g,
            '<a href="$1" style="color:' + color + '">$1</a>').replace(/\n/g, "<br>");
    }
    function fmtSize(n) {
        if (n >= 1e9) return (n / 1e9).toFixed(1) + " GB";
        if (n >= 1e6) return (n / 1e6).toFixed(1) + " MB";
        if (n >= 1e3) return Math.round(n / 1e3) + " KB";
        return n + " B";
    }
    function openPicker(m, item) {
        if (root.picker && root.picker.id === m.id) { root.picker = null; return; }
        const p = item.mapToItem(msgArea, 0, 0);
        root.effectPickerOpen = false;
        root.picker = { id: m.id, m: m, x: p.x, y: p.y, w: item.width, mine: m.fromMe };
    }
    function reactionGlyph(kind) {
        return ({ love: "♥", like: "👍", dislike: "👎", laugh: "😂", emphasize: "‼", question: "?" })[kind] || kind;
    }
    function routeLabel() {
        if (root.route === "bluebubbles")
            return "BlueBubbles" + (root.bb.link === "tailscale" ? " · Tailscale" : "");
        if (root.route === "iphone") return (root.phone.name || "iPhone") + " · Bluetooth";
        if (!sock.connected && root.preview === "") return "Pear Messages service isn't running";
        return "Not connected";
    }
    function stateText(s) {
        return ({ online: "Connected", connecting: "Connecting…", error: "Can't connect", off: "Off",
                  away: "Out of range", needs_permission: "Needs permission" })[s] || s || "Off";
    }

    // ------------------------------------------------------------------ window
    FloatingWindow {
        id: win
        title: "Pear Messages"
        implicitWidth: 1040
        implicitHeight: 680
        color: Theme.bg
        visible: true
        onClosed: Qt.quit()

        FocusScope {
            id: scope
            anchors.fill: parent
            focus: true
            Rectangle { anchors.fill: parent; color: Theme.bg; z: -1 }


            Keys.onPressed: function (ev) {
                const ctrl = ev.modifiers & Qt.ControlModifier;
                if (ev.key === Qt.Key_Space && root.builtinPreview) { root.builtinPreview = ""; ev.accepted = true; return; }
                if (ev.key === Qt.Key_Space && root.spacePreview()) { ev.accepted = true; return; }
                if (ev.key === Qt.Key_Escape) {
                    if (root.builtinPreview) root.builtinPreview = "";
                    else if (root.picker) root.picker = null;
                    else if (root.effectPickerOpen) root.effectPickerOpen = false;
                    else if (root.settingsOpen) root.settingsOpen = false;
                    else if (root.composing) { root.composing = false; if (root.threads.length) root.openThread(root.threads[0].id); }
                    else if (search.text) search.text = "";
                    else Qt.quit();
                    ev.accepted = true;
                } else if (ctrl && ev.key === Qt.Key_N) { root.startCompose(); ev.accepted = true; }
                else if (ctrl && ev.key === Qt.Key_Comma) { root.settingsOpen = !root.settingsOpen; ev.accepted = true; }
                else if (ctrl && ev.key === Qt.Key_F) { search.forceActiveFocus(); ev.accepted = true; }
                else if (ev.key === Qt.Key_End && (ctrl || !composer.activeFocus)) { list.goToBottom(); ev.accepted = true; }
                else if ((ev.modifiers & Qt.AltModifier) && (ev.key === Qt.Key_Down || ev.key === Qt.Key_Up)) {
                    const list = root.shownThreads;
                    let i = list.findIndex(t => t.id === root.current);
                    i = Math.max(0, Math.min(list.length - 1, i + (ev.key === Qt.Key_Down ? 1 : -1)));
                    if (list[i]) root.openThread(list[i].id);
                    ev.accepted = true;
                }
            }

            Rectangle {
                anchors.fill: parent
                z: 100
                visible: root.builtinPreview !== ""
                color: Qt.rgba(0, 0, 0, 0.88)
                Image {
                    anchors.fill: parent
                    anchors.margins: 32
                    source: root.builtinPreview ? "file://" + root.builtinPreview : ""
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                }
                Text {
                    anchors.bottom: parent.bottom
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.bottomMargin: 10
                    text: "Space or Esc to close"
                    color: "#bbbbbb"
                    font.family: Theme.uiFont
                    font.pixelSize: Theme.fCaption
                }
                TapHandler { onTapped: root.builtinPreview = "" }
            }

            RowLayout {
                anchors.fill: parent
                spacing: 0

                // ================================================= sidebar
                Rectangle {
                    Layout.preferredWidth: 320
                    Layout.fillHeight: true
                    color: Theme.bg

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 10

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            Text {
                                Layout.fillWidth: true
                                text: "Messages"
                                color: Theme.fg
                                font.family: Theme.uiFont
                                font.pixelSize: Theme.fHeading
                                font.weight: Font.DemiBold
                            }
                            AppButton { text: "New"; onClicked: root.startCompose() }
                        }

                        O.TextField {
                            id: search
                            Layout.fillWidth: true
                            placeholderText: "Search"
                            font.pixelSize: Theme.fBody
                            onTextChanged: root.query = text
                            Keys.onDownPressed: if (root.shownThreads.length) root.openThread(root.shownThreads[0].id)
                        }

                        ListView {
                            id: threadList
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            model: root.shownThreads
                            spacing: 2
                            interactive: false
                            ScrollBar.vertical: AppScrollBar {}
                            ScrollPhysics { id: threadPhys; flick: threadList }
                            WheelHandler {
                                acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
                                onWheel: ev => threadPhys.wheel(ev)
                            }
                            function stopPhysics() { threadPhys.stopPhysics(); }

                            delegate: Rectangle {
                                id: row
                                required property var modelData
                                readonly property bool sel: modelData.id === root.current && !root.composing
                                width: threadList.width
                                height: 62
                                radius: Theme.radius
                                color: sel ? Theme.selected : rowMouse.containsMouse ? Theme.hover : "transparent"

                                MouseArea {
                                    id: rowMouse
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    onClicked: root.openThread(row.modelData.id)
                                }

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 8
                                    anchors.rightMargin: 10
                                    spacing: 10

                                    Rectangle {
                                        Layout.preferredWidth: 38
                                        Layout.preferredHeight: 38
                                        radius: Math.max(Theme.radius, 19)
                                        color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.18)
                                        Text {
                                            anchors.centerIn: parent
                                            text: row.modelData.group ? "⋯" : root.initials(row.modelData.title)
                                            color: Theme.accent
                                            font.family: Theme.uiFont
                                            font.pixelSize: Theme.fSmall
                                            font.weight: Font.DemiBold
                                        }
                                    }

                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 2
                                        RowLayout {
                                            Layout.fillWidth: true
                                            Text {
                                                Layout.fillWidth: true
                                                text: row.modelData.title
                                                elide: Text.ElideRight
                                                color: Theme.fg
                                                font.family: Theme.uiFont
                                                font.pixelSize: Theme.fBody
                                                font.weight: row.modelData.unread ? Font.Bold : Font.Medium
                                            }
                                            Text {
                                                text: root.listTime(row.modelData.ts)
                                                color: Theme.dim
                                                font.family: Theme.uiFont
                                                font.pixelSize: Theme.fCaption
                                            }
                                        }
                                        RowLayout {
                                            Layout.fillWidth: true
                                            Text {
                                                Layout.fillWidth: true
                                                text: (row.modelData.fromMe ? "You: " : "") + (row.modelData.preview || "").replace(/\s+/g, " ")
                                                elide: Text.ElideRight
                                                maximumLineCount: 1
                                                color: row.modelData.unread ? Theme.fg : Theme.dim
                                                font.family: Theme.uiFont
                                                font.pixelSize: Theme.fSmall
                                            }
                                            Rectangle {
                                                visible: row.modelData.unread > 0
                                                Layout.preferredWidth: 9
                                                Layout.preferredHeight: 9
                                                radius: 4.5
                                                color: Theme.accent
                                            }
                                        }
                                    }
                                }
                            }

                            Text {
                                anchors.centerIn: parent
                                width: parent.width - 20
                                visible: root.shownThreads.length === 0
                                horizontalAlignment: Text.AlignHCenter
                                wrapMode: Text.WordWrap
                                text: root.query ? "No conversations match" :
                                      (root.route === "none" ? "Connect BlueBubbles or your iPhone in Settings to see your messages."
                                                             : "No messages yet")
                                color: Theme.dim
                                font.family: Theme.uiFont
                                font.pixelSize: Theme.fSmall
                            }
                        }

                        // connection chip
                        Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: 38
                            radius: Theme.radius
                            color: chipMouse.containsMouse ? Theme.hover : "transparent"
                            border.width: 1
                            border.color: Theme.line
                            MouseArea {
                                id: chipMouse
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: root.settingsOpen = !root.settingsOpen
                            }
                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 12
                                anchors.rightMargin: 10
                                spacing: 8
                                Rectangle {
                                    Layout.preferredWidth: 8
                                    Layout.preferredHeight: 8
                                    radius: 4
                                    color: root.route === "none" ? Theme.danger : Theme.accent
                                    opacity: root.route === "iphone" ? 0.65 : 1
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: root.routeLabel()
                                    elide: Text.ElideRight
                                    color: Theme.fg
                                    font.family: Theme.uiFont
                                    font.pixelSize: Theme.fSmall
                                }
                                Text {
                                    text: "Settings"
                                    color: Theme.dim
                                    font.family: Theme.uiFont
                                    font.pixelSize: Theme.fCaption
                                }
                            }
                        }
                    }
                }

                Rectangle { Layout.fillHeight: true; Layout.preferredWidth: 1; color: Theme.line }

                // ================================================= conversation
                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    ColumnLayout {
                        anchors.fill: parent
                        spacing: 0

                        // header
                        Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: 60
                            color: Theme.bg
                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 18
                                anchors.rightMargin: 14
                                spacing: 10
                                visible: !root.composing
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 1
                                    Text {
                                        Layout.fillWidth: true
                                        text: root.currentInfo ? root.currentInfo.title : ""
                                        elide: Text.ElideRight
                                        color: Theme.fg
                                        font.family: Theme.uiFont
                                        font.pixelSize: Theme.fBody + 2
                                        font.weight: Font.DemiBold
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        visible: text !== ""
                                        text: {
                                            const t = root.currentInfo;
                                            if (!t) return "";
                                            if (t.group) return t.participants.length + " people";
                                            if (t.self) return "You" + (t.addresses && t.addresses.length > 1 ? " · " + t.addresses.join(" · ") : "");
                                            if (t.addresses && t.addresses.length > 1) return t.addresses.join(" · ");
                                            return t.address && t.address !== t.title ? t.address : "";
                                        }
                                        elide: Text.ElideRight
                                        color: Theme.dim
                                        font.family: Theme.uiFont
                                        font.pixelSize: Theme.fCaption
                                    }
                                }
                            }
                            // "To:" row while composing
                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 18
                                anchors.rightMargin: 14
                                spacing: 10
                                visible: root.composing
                                Text {
                                    text: "To:"
                                    color: Theme.dim
                                    font.family: Theme.uiFont
                                    font.pixelSize: Theme.fBody
                                }
                                Rectangle {
                                    visible: root.composeTo !== ""
                                    implicitHeight: 30
                                    implicitWidth: chipText.implicitWidth + 30
                                    radius: Theme.radius
                                    color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.18)
                                    Text {
                                        id: chipText
                                        anchors.verticalCenter: parent.verticalCenter
                                        x: 10
                                        text: root.composeToName ? root.composeToName + "  " + root.composeTo : root.composeTo
                                        color: Theme.fg
                                        font.family: Theme.uiFont
                                        font.pixelSize: Theme.fSmall
                                    }
                                    Text {
                                        anchors.right: parent.right
                                        anchors.rightMargin: 8
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: "×"
                                        color: Theme.dim
                                        font.pixelSize: Theme.fBody
                                        MouseArea {
                                            anchors.fill: parent
                                            anchors.margins: -4
                                            onClicked: { root.composeTo = ""; root.composeToName = ""; toField.forceActiveFocus(); }
                                        }
                                    }
                                }
                                O.TextField {
                                    id: toField
                                    Layout.fillWidth: true
                                    visible: root.composeTo === ""
                                    placeholderText: "Name, phone number or email"
                                    font.pixelSize: Theme.fBody
                                    onTextChanged: searchTimer.restart()
                                    Keys.onReturnPressed: {
                                        if (root.searchResults.length) {
                                            root.composeTo = root.searchResults[0].addr;
                                            root.composeToName = root.searchResults[0].name;
                                        } else if (text.trim()) root.composeTo = text.trim();
                                        if (root.composeTo) composer.forceActiveFocus();
                                    }
                                }
                                Timer {
                                    id: searchTimer
                                    interval: 150
                                    onTriggered: root.send({ op: "search", q: toField.text.trim() })
                                }
                            }
                            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.line }
                        }

                        // messages
                        Item {
                            id: msgArea
                            Layout.fillWidth: true
                            Layout.fillHeight: true

                            ListView {
                                id: list
                                anchors.fill: parent
                                anchors.leftMargin: 16
                                anchors.rightMargin: 16
                                clip: true
                                model: root.msgs
                                spacing: 3
                                interactive: false
                                ScrollBar.vertical: AppScrollBar {}
                                ScrollPhysics { id: msgPhys; flick: list }
                                WheelHandler {
                                    acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
                                    onWheel: ev => msgPhys.wheel(ev)
                                }
                                function stopPhysics() { msgPhys.stopPhysics(); }
                                header: Item { width: 1; height: 12 }
                                footer: Item { width: 1; height: 12 }
                                readonly property real bubbleMax: Math.min(560, width * 0.72)
                                readonly property int lastMine: {
                                    for (let i = root.msgs.length - 1; i >= 0; i--) if (root.msgs[i].fromMe) return i;
                                    return -1;
                                }
                                // Follow the newest message until the user scrolls up. Photos load after the
                // first jump to the end and grow the list, so growth re-pins it while following.
                property bool followEnd: true
                // Pinning to the end while a long conversation is still being laid out can leave the
                // list at the right place with nothing drawn. So pin, then pin again as it settles.
                function stickToEnd() { followEnd = true; msgPhys.stopPhysics(); settle.left = 8; settle.restart(); }
                function pinEnd() { list.forceLayout(); list.positionViewAtEnd(); }
                // Glide back to the newest message. From far up, jump most of the way first so
                // the glide is a short, readable one rather than a blur through the history.
                function goToBottom() {
                    msgPhys.stopPhysics();
                    list.forceLayout();
                    const target = msgPhys.maxY;
                    if (target - list.contentY > list.height * 2.5) list.contentY = target - list.height * 1.5;
                    toBottom.to = target;
                    toBottom.restart();
                }
                NumberAnimation {
                    id: toBottom
                    target: list; property: "contentY"
                    duration: 420; easing.type: Easing.OutCubic
                    onFinished: list.stickToEnd()
                }
                readonly property bool farFromEnd: contentHeight > height && contentY < msgPhys.maxY - 160
                onFollowEndChanged: if (followEnd) root.newBelow = 0
                Timer {
                    id: settle
                    property int left: 0
                    interval: 60
                    repeat: true
                    onTriggered: {
                        if (list.followEnd && !msgPhys.busy) list.pinEnd();
                        if (--left <= 0) stop();
                    }
                }
                onContentHeightChanged: if (followEnd && !msgPhys.busy && !settle.running) { settle.left = 3; settle.restart(); }
                onHeightChanged: if (followEnd && !msgPhys.busy) { settle.left = 3; settle.restart(); }
                onMovementEnded: followEnd = atYEnd
                                onContentYChanged: {
                                    // wheel, drag and scrollbar all move contentY; growth alone doesn't.
                                    // A bounce past the end still counts as at the end.
                                    followEnd = contentY >= msgPhys.maxY - 2;
                                    root.picker = null;
                                    if (contentY <= originY + 40 && root.msgs.length >= 200 && !loadingOlder.running) {
                                        loadingOlder.start();
                                        root.send({ op: "older", thread: root.current, before: root.msgs[0].ts });
                                    }
                                }
                                Timer { id: loadingOlder; interval: 800 }

                                delegate: Item {
                                    id: msgItem
                                    required property var modelData
                                    required property int index
                                    readonly property var m: modelData
                                    readonly property bool mine: m.fromMe
                                    readonly property var prev: index > 0 ? root.msgs[index - 1] : null
                                    readonly property bool showStamp: !prev || m.ts - prev.ts > 1800
                                    readonly property bool showSender: root.currentInfo && root.currentInfo.group && !mine
                                                                       && (!prev || prev.fromMe || prev.sender !== m.sender || showStamp)
                                    readonly property bool showStatus: mine && (index === list.lastMine || m.status === "failed")
                                    readonly property var reacts: Object.keys(m.reactions || {})
                                    width: list.width
                                    height: col.implicitHeight + (prev && prev.fromMe !== mine ? 8 : 0)

                                    Column {
                                        id: col
                                        width: parent.width
                                        anchors.bottom: parent.bottom
                                        spacing: 3

                                        Text {
                                            visible: msgItem.showStamp
                                            width: parent.width
                                            topPadding: 10
                                            bottomPadding: 6
                                            horizontalAlignment: Text.AlignHCenter
                                            text: root.stampTime(msgItem.m.ts)
                                            color: Theme.dim
                                            font.family: Theme.uiFont
                                            font.pixelSize: Theme.fCaption
                                        }
                                        Text {
                                            visible: msgItem.showSender
                                            x: 12
                                            text: msgItem.m.senderName || msgItem.m.sender || ""
                                            color: Theme.dim
                                            font.family: Theme.uiFont
                                            font.pixelSize: Theme.fCaption
                                        }

                                        // attachments: click selects, Space or a double-click previews
                                        Repeater {
                                            model: msgItem.m.attachments || []
                                            delegate: Item {
                                                id: attItem
                                                required property var modelData
                                                readonly property var st: root.attachments[modelData.guid] || ({})
                                                readonly property bool isImage: (modelData.mime || "").indexOf("image/") === 0
                                                                               && modelData.size < 25000000
                                                readonly property bool isVideo: root.isVideo(modelData)
                                                readonly property bool selected: root.selAtt === modelData.guid
                                                width: col.width
                                                height: isImage && st.path ? img.height : fileChip.height
                                                // Photos load by themselves (to the temp folder when far up the
                                                // conversation); videos only when you ask to see one.
                                                Component.onCompleted: if (isImage) root.needAttachment(modelData, msgItem.index)

                                                function hover(on) {
                                                    if (on) root.hoverAtt = { att: attItem.modelData, index: msgItem.index };
                                                    else if (root.hoverAtt && root.hoverAtt.att.guid === attItem.modelData.guid) root.hoverAtt = null;
                                                }

                                                Image {
                                                    id: img
                                                    visible: attItem.isImage && !!attItem.st.path
                                                    anchors.right: msgItem.mine ? parent.right : undefined
                                                    source: attItem.st.path ? "file://" + attItem.st.path : ""
                                                    asynchronous: true
                                                    fillMode: Image.PreserveAspectFit
                                                    // at most 280 wide and 340 tall, keeping the photo's shape
                                                    readonly property real aspect: implicitHeight / Math.max(1, implicitWidth)
                                                    width: status === Image.Ready ? Math.min(280, list.bubbleMax, 340 / Math.max(0.01, aspect)) : 200
                                                    height: status === Image.Ready ? width * aspect : 120
                                                    sourceSize.width: 600
                                                    HoverHandler { cursorShape: Qt.PointingHandCursor; onHoveredChanged: attItem.hover(hovered) }
                                                    TapHandler {
                                                        onTapped: root.selAtt = attItem.modelData.guid
                                                        onDoubleTapped: root.previewAttachment(attItem.modelData, msgItem.index)
                                                    }
                                                    Rectangle {
                                                        anchors.fill: parent
                                                        anchors.margins: -3
                                                        visible: attItem.selected
                                                        color: "transparent"
                                                        radius: Theme.radius
                                                        border.width: 2
                                                        border.color: Theme.accent
                                                    }
                                                }
                                                Rectangle {
                                                    id: fileChip
                                                    visible: !(attItem.isImage && attItem.st.path)
                                                    anchors.right: msgItem.mine ? parent.right : undefined
                                                    width: Math.min(list.bubbleMax, fileText.implicitWidth + 28)
                                                    height: 40
                                                    radius: Theme.radius
                                                    color: chipHover.hovered ? Theme.hover : Theme.panel
                                                    border.width: attItem.selected ? 2 : 1
                                                    border.color: attItem.selected ? Theme.accent : Theme.line
                                                    Text {
                                                        id: fileText
                                                        anchors.fill: parent
                                                        anchors.leftMargin: 14
                                                        anchors.rightMargin: 14
                                                        verticalAlignment: Text.AlignVCenter
                                                        elide: Text.ElideMiddle
                                                        text: (attItem.st.loading ? "Loading… " : attItem.st.error ? "Couldn't load " : attItem.isVideo ? "▶  " : "📎  ")
                                                              + (attItem.modelData.name || "Attachment")
                                                              + (attItem.modelData.size ? "  ·  " + root.fmtSize(attItem.modelData.size) : "")
                                                        color: attItem.st.error ? Theme.danger : Theme.fg
                                                        font.family: Theme.uiFont
                                                        font.pixelSize: Theme.fSmall
                                                    }
                                                    HoverHandler { id: chipHover; cursorShape: Qt.PointingHandCursor; onHoveredChanged: attItem.hover(hovered) }
                                                    TapHandler {
                                                        onTapped: root.selAtt = attItem.modelData.guid
                                                        onDoubleTapped: root.previewAttachment(attItem.modelData, msgItem.index)
                                                    }
                                                }
                                            }
                                        }

                                        // the bubble
                                        Item {
                                            id: bubbleRow
                                            visible: msgItem.m.text !== ""
                                            width: parent.width
                                            height: bubble.height + (msgItem.reacts.length ? 18 : 0)
                                            HoverHandler { id: bubbleHover }

                                            Rectangle {
                                                id: bubble
                                                anchors.right: msgItem.mine ? parent.right : undefined
                                                anchors.bottom: parent.bottom
                                                readonly property var segs: msgItem.m.segments || []
                                                readonly property bool textFx: segs.some(sg => !!sg.effect)
                                                // letters drawn one by one come out a little wider than the string
                                                width: Math.min(textFx ? effText.naturalWidth + 2 : measure.implicitWidth, list.bubbleMax - 28) + 28
                                                height: (textFx ? effText.height : body.implicitHeight) + 16
                                                radius: Math.max(Theme.radius, 4)
                                                color: msgItem.mine ? (msgItem.m.status === "failed" ? Theme.danger : Theme.accent) : Theme.panel
                                                opacity: msgItem.m.status === "sending" ? 0.6 : 1
                                                transformOrigin: msgItem.mine ? Item.BottomRight : Item.BottomLeft

                                                // ---- bubble effects
                                                readonly property string effect: msgItem.m.effect || ""
                                                readonly property bool ink: effect.endsWith("invisibleink")
                                                property bool revealed: false
                                                readonly property bool inkHidden: ink && !revealed && !bubbleHover.hovered
                                                readonly property real fxNonce: root.fxPlay[msgItem.m.id] || 0
                                                onFxNonceChanged: if (fxNonce) runEffect()
                                                function runEffect() {
                                                    if (effect.endsWith("impact")) slamAnim.restart();
                                                    else if (effect.endsWith("loud")) loudAnim.restart();
                                                    else if (effect.endsWith("gentle")) gentleAnim.restart();
                                                    else if (ink) { revealed = false; inkAnim.restart(); }
                                                }
                                                SequentialAnimation {
                                                    id: slamAnim
                                                    PropertyAction { target: bubble; property: "opacity"; value: 0 }
                                                    ParallelAnimation {
                                                        NumberAnimation { target: bubble; property: "scale"; from: 3.2; to: 1; duration: 300; easing.type: Easing.InQuad }
                                                        NumberAnimation { target: bubble; property: "opacity"; from: 0; to: 1; duration: 160 }
                                                    }
                                                    ScriptAction { script: shock.go() }
                                                    SequentialAnimation {
                                                        loops: 2
                                                        NumberAnimation { target: list; property: "anchors.leftMargin"; to: 22; duration: 40 }
                                                        NumberAnimation { target: list; property: "anchors.leftMargin"; to: 10; duration: 40 }
                                                    }
                                                    NumberAnimation { target: list; property: "anchors.leftMargin"; to: 16; duration: 40 }
                                                }
                                                SequentialAnimation {
                                                    id: loudAnim
                                                    NumberAnimation { target: bubble; property: "scale"; from: 1; to: 2.1; duration: 160; easing.type: Easing.OutQuad }
                                                    SequentialAnimation {
                                                        loops: 3
                                                        NumberAnimation { target: bubble; property: "rotation"; to: -4; duration: 55 }
                                                        NumberAnimation { target: bubble; property: "rotation"; to: 4; duration: 55 }
                                                    }
                                                    NumberAnimation { target: bubble; property: "rotation"; to: 0; duration: 50 }
                                                    PauseAnimation { duration: 220 }
                                                    NumberAnimation { target: bubble; property: "scale"; to: 1; duration: 320; easing.type: Easing.OutBack }
                                                }
                                                ParallelAnimation {
                                                    id: gentleAnim
                                                    NumberAnimation { target: bubble; property: "scale"; from: 0.35; to: 1; duration: 1600; easing.type: Easing.InOutSine }
                                                    NumberAnimation { target: body; property: "opacity"; from: 0.15; to: 1; duration: 1600 }
                                                }
                                                NumberAnimation { id: inkAnim; target: inkDots; property: "opacity"; from: 0; to: 1; duration: 500 }

                                                Text {
                                                    id: measure
                                                    visible: false
                                                    text: msgItem.m.text
                                                    font: body.font
                                                }
                                                TextEdit {
                                                    id: body
                                                    x: 14
                                                    y: 8
                                                    width: bubble.width - 28
                                                    readOnly: true
                                                    selectByMouse: !bubble.inkHidden
                                                    wrapMode: TextEdit.Wrap
                                                    textFormat: TextEdit.RichText
                                                    visible: !bubble.textFx
                                                    text: bubble.segs.length ? root.styledHtml(bubble.segs, msgItem.mine ? Theme.onAccent : Theme.accent)
                                                                             : root.linkify(msgItem.m.text, msgItem.mine ? Theme.onAccent : Theme.accent)
                                                    color: msgItem.mine ? Theme.onAccent : Theme.fg
                                                    selectedTextColor: msgItem.mine ? Theme.accent : Theme.bg
                                                    selectionColor: msgItem.mine ? Theme.onAccent : Theme.accent
                                                    font.family: Theme.uiFont
                                                    font.pixelSize: Theme.fBody
                                                    onLinkActivated: link => Qt.openUrlExternally(link)
                                                    // Invisible Ink: blurred away until the pointer is on it.
                                                    layer.enabled: bubble.inkHidden
                                                    layer.effect: MultiEffect { blurEnabled: true; blur: 1.0; blurMax: 48; saturation: -1 }
                                                    HoverHandler {
                                                        enabled: body.hoveredLink !== ""
                                                        cursorShape: Qt.PointingHandCursor
                                                    }
                                                }
                                                // iOS 18 text effects: letters that move
                                                EffectText {
                                                    id: effText
                                                    visible: bubble.textFx
                                                    x: 14
                                                    y: 8
                                                    width: bubble.width - 28
                                                    segments: bubble.textFx ? bubble.segs : []
                                                    color: body.color
                                                    font: body.font
                                                }
                                                // the ink's shimmer
                                                Item {
                                                    id: inkDots
                                                    anchors.fill: parent
                                                    anchors.margins: 6
                                                    clip: true
                                                    visible: bubble.inkHidden
                                                    Repeater {
                                                        model: bubble.ink ? 36 : 0
                                                        Rectangle {
                                                            required property int index
                                                            width: 2; height: 2; radius: 1
                                                            color: msgItem.mine ? Theme.onAccent : Theme.fg
                                                            x: Math.random() * inkDots.width
                                                            y: Math.random() * inkDots.height
                                                            SequentialAnimation on opacity {
                                                                loops: Animation.Infinite
                                                                running: bubble.inkHidden
                                                                PauseAnimation { duration: Math.random() * 900 }
                                                                NumberAnimation { from: 0.1; to: 0.9; duration: 500 + Math.random() * 500 }
                                                                NumberAnimation { from: 0.9; to: 0.1; duration: 500 + Math.random() * 500 }
                                                            }
                                                        }
                                                    }
                                                }
                                            }

                                            // Slam's shockwave: a ring leaving the bubble as it lands
                                            Rectangle {
                                                id: shock
                                                anchors.centerIn: bubble
                                                width: bubble.width; height: bubble.height
                                                radius: bubble.radius + 4
                                                color: "transparent"
                                                border.width: 3
                                                border.color: bubble.color
                                                opacity: 0
                                                function go() { shockAnim.restart(); }
                                                ParallelAnimation {
                                                    id: shockAnim
                                                    NumberAnimation { target: shock; property: "scale"; from: 1; to: 1.5; duration: 520; easing.type: Easing.OutCubic }
                                                    NumberAnimation { target: shock; property: "opacity"; from: 0.8; to: 0; duration: 520; easing.type: Easing.OutQuad }
                                                }
                                            }

                                            // react button, beside the bubble while the pointer is on it
                                            Rectangle {
                                                visible: (bubbleHover.hovered || (!!root.picker && root.picker.id === msgItem.m.id))
                                                         && msgItem.m.status !== "sending"
                                                width: 32; height: 32; radius: 16
                                                anchors.verticalCenter: bubble.verticalCenter
                                                x: msgItem.mine ? bubble.x - width - 6 : bubble.x + bubble.width + 6
                                                color: reactHover.hovered ? Theme.hover : Theme.bg
                                                border.width: 1
                                                border.color: Theme.line
                                                Text {
                                                    anchors.centerIn: parent
                                                    text: "☺"
                                                    color: root.reactBlock(msgItem.m) ? Theme.faint : Theme.fg
                                                    font.pixelSize: Math.round(Theme.fBody * 1.3)
                                                }
                                                HoverHandler { id: reactHover; cursorShape: Qt.PointingHandCursor }
                                                TapHandler { onTapped: root.openPicker(msgItem.m, bubble) }
                                            }

                                            Rectangle {
                                                visible: msgItem.reacts.length > 0
                                                anchors.top: parent.top
                                                anchors.topMargin: -8
                                                x: msgItem.mine ? bubble.x - width + 14 : bubble.x + bubble.width - 14
                                                height: 34
                                                width: Math.max(height, reactRow.implicitWidth + 16)
                                                radius: height / 2
                                                color: Theme.bg
                                                border.width: 1
                                                border.color: (msgItem.m.reactions || {}).me ? Theme.accent : Theme.line
                                                Row {
                                                    id: reactRow
                                                    anchors.centerIn: parent
                                                    spacing: 3
                                                    Repeater {
                                                        model: msgItem.reacts
                                                        Text {
                                                            required property var modelData
                                                            text: root.reactionGlyph(msgItem.m.reactions[modelData])
                                                            color: modelData === "me" ? Theme.accent : Theme.fg
                                                            font.pixelSize: Math.round(Theme.fBody * 1.45)
                                                        }
                                                    }
                                                }
                                            }
                                        }

                                        // "Sent with Slam · Replay" - click to watch it again
                                        Text {
                                            visible: !!root.effectNames[msgItem.m.effect || ""] && !(msgItem.m.effect || "").startsWith("text:")
                                            anchors.right: msgItem.mine ? parent.right : undefined
                                            leftPadding: 4
                                            rightPadding: 4
                                            text: (msgItem.mine ? "Sent with " : "") + (root.effectNames[msgItem.m.effect || ""] || "") + " · Replay"
                                            color: Theme.dim
                                            font.family: Theme.uiFont
                                            font.pixelSize: Theme.fCaption
                                            MouseArea {
                                                anchors.fill: parent
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: root.playEffect(msgItem.m)
                                            }
                                        }

                                        Text {
                                            visible: msgItem.showStatus
                                            anchors.right: parent.right
                                            rightPadding: 4
                                            text: {
                                                const s = msgItem.m.status;
                                                const via = msgItem.m.via === "iphone" ? " · via iPhone" : "";
                                                if (s === "failed") return "Not delivered — click to retry" + (msgItem.m.error ? "  (" + msgItem.m.error + ")" : "");
                                                if (s === "sending") return "Sending…";
                                                return ({ sent: "Sent", delivered: "Delivered", read: "Read" })[s] + via
                                                       + (msgItem.m.error ? " — " + msgItem.m.error : "");
                                            }
                                            color: msgItem.m.status === "failed" ? Theme.danger : Theme.dim
                                            font.family: Theme.uiFont
                                            font.pixelSize: Theme.fCaption
                                            width: Math.min(implicitWidth, col.width)
                                            elide: Text.ElideRight
                                            MouseArea {
                                                anchors.fill: parent
                                                enabled: msgItem.m.status === "failed"
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: root.send({ op: "retry", message: msgItem.m.id })
                                            }
                                        }
                                    }
                                }
                            }

                            // contact search results while composing
                            Rectangle {
                                visible: root.composing && root.composeTo === "" && root.searchResults.length > 0
                                anchors.top: parent.top
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.margins: 12
                                height: Math.min(resultsCol.implicitHeight + 8, parent.height - 24)
                                color: Theme.bg
                                radius: Theme.radius
                                border.width: 1
                                border.color: Theme.line
                                Column {
                                    id: resultsCol
                                    anchors.fill: parent
                                    anchors.margins: 4
                                    Repeater {
                                        model: root.searchResults
                                        Rectangle {
                                            required property var modelData
                                            required property int index
                                            width: resultsCol.width
                                            height: 44
                                            radius: Theme.radius
                                            color: resMouse.containsMouse || index === 0 ? Theme.hover : "transparent"
                                            MouseArea {
                                                id: resMouse
                                                anchors.fill: parent
                                                hoverEnabled: true
                                                onClicked: {
                                                    root.composeTo = parent.modelData.addr;
                                                    root.composeToName = parent.modelData.name;
                                                    composer.forceActiveFocus();
                                                }
                                            }
                                            Column {
                                                anchors.verticalCenter: parent.verticalCenter
                                                x: 12
                                                Text { text: modelData.name; color: Theme.fg; font.family: Theme.uiFont; font.pixelSize: Theme.fBody }
                                                Text { text: modelData.addr; color: Theme.dim; font.family: Theme.uiFont; font.pixelSize: Theme.fCaption }
                                            }
                                        }
                                    }
                                }
                            }

                            // "go to bottom": shows once you've scrolled up away from the newest messages
                            Rectangle {
                                id: toBottomBtn
                                z: 55
                                readonly property bool shown: list.farFromEnd && root.current !== "" && !root.composing
                                anchors.horizontalCenter: parent.horizontalCenter
                                y: parent.height - height - 14 + (shown ? 0 : 16)
                                opacity: shown ? 1 : 0
                                visible: opacity > 0.01
                                Behavior on opacity { NumberAnimation { duration: 180; easing.type: Easing.OutQuad } }
                                Behavior on y { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
                                height: 36
                                width: btnRow.implicitWidth + 28
                                radius: height / 2
                                color: btnHover.hovered ? Qt.lighter(Theme.bg, 1.9) : Qt.lighter(Theme.bg, 1.5)
                                border.width: 1
                                border.color: root.newBelow > 0 ? Theme.accent : Theme.line
                                Row {
                                    id: btnRow
                                    anchors.centerIn: parent
                                    spacing: 8
                                    Text {
                                        text: "↓"
                                        color: root.newBelow > 0 ? Theme.accent : Theme.fg
                                        font.family: Theme.uiFont
                                        font.pixelSize: Theme.fBody
                                        font.weight: Font.DemiBold
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                    Text {
                                        text: root.newBelow > 0 ? (root.newBelow === 1 ? "1 new message" : root.newBelow + " new messages")
                                                                : "Go to bottom"
                                        color: Theme.fg
                                        font.family: Theme.uiFont
                                        font.pixelSize: Theme.fSmall
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                }
                                HoverHandler { id: btnHover; cursorShape: Qt.PointingHandCursor }
                                TapHandler { onTapped: list.goToBottom() }
                            }

                            ScreenEffects { id: screenFx }

                            // click anywhere else closes a picker
                            MouseArea {
                                anchors.fill: parent
                                z: 60
                                visible: !!root.picker || root.effectPickerOpen
                                onClicked: { root.picker = null; root.effectPickerOpen = false; }
                                onWheel: wheel => { root.picker = null; root.effectPickerOpen = false; wheel.accepted = false; }
                            }

                            // ---- reaction picker
                            Rectangle {
                                id: pickerBox
                                z: 61
                                visible: !!root.picker
                                readonly property string block: root.picker ? root.reactBlock(root.picker.m) : ""
                                readonly property string mine: root.picker ? ((root.picker.m.reactions || {}).me || "") : ""
                                width: block ? Math.max(pickRow.implicitWidth, 300) + 20 : pickRow.implicitWidth + 20
                                height: pickCol.implicitHeight + 20
                                x: root.picker ? Math.max(8, Math.min(msgArea.width - width - 8,
                                       root.picker.mine ? root.picker.x + root.picker.w - width : root.picker.x)) : 0
                                y: root.picker ? Math.max(8, root.picker.y - height - 8) : 0
                                radius: Math.max(Theme.radius, 8)
                                color: Theme.bg
                                border.width: 1
                                border.color: Theme.line
                                Column {
                                    id: pickCol
                                    x: 10; y: 10
                                    spacing: 8
                                    Row {
                                        id: pickRow
                                        spacing: 4
                                        Repeater {
                                            model: root.reactionKinds
                                            Rectangle {
                                                required property string modelData
                                                width: 50; height: 50; radius: 25
                                                opacity: pickerBox.block ? 0.35 : 1
                                                color: pickerBox.mine === modelData ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.3)
                                                     : kindHover.hovered && !pickerBox.block ? Theme.hover : "transparent"
                                                Text {
                                                    anchors.centerIn: parent
                                                    text: root.reactionGlyph(parent.modelData)
                                                    color: Theme.fg
                                                    font.pixelSize: Math.round(Theme.fBody * 1.85)
                                                }
                                                HoverHandler { id: kindHover; cursorShape: pickerBox.block ? Qt.ForbiddenCursor : Qt.PointingHandCursor }
                                                TapHandler {
                                                    enabled: !pickerBox.block
                                                    onTapped: {
                                                        root.send({ op: "react", message: root.picker.id, reaction: parent.modelData });
                                                        root.picker = null;
                                                    }
                                                }
                                            }
                                        }
                                    }
                                    Text {
                                        visible: pickerBox.block !== ""
                                        width: pickerBox.width - 20
                                        wrapMode: Text.WordWrap
                                        text: pickerBox.block
                                        color: Theme.dim
                                        font.family: Theme.uiFont
                                        font.pixelSize: Theme.fCaption
                                    }
                                }
                            }

                            // ---- effect picker (above the composer)
                            Rectangle {
                                id: effectBox
                                z: 61
                                visible: root.effectPickerOpen
                                anchors.right: parent.right
                                anchors.bottom: parent.bottom
                                anchors.margins: 12
                                width: 400
                                height: effCol.implicitHeight + 28
                                radius: Math.max(Theme.radius, 8)
                                color: Theme.bg
                                border.width: 1
                                border.color: Theme.line
                                Column {
                                    id: effCol
                                    x: 14; y: 14
                                    width: effectBox.width - 28
                                    spacing: 8
                                    Text {
                                        text: "Send with effect"
                                        color: Theme.fg
                                        font.family: Theme.uiFont
                                        font.pixelSize: Theme.fBody
                                        font.weight: Font.DemiBold
                                    }
                                    Text {
                                        visible: !root.abilities.effects
                                        width: parent.width
                                        wrapMode: Text.WordWrap
                                        text: root.abilities.reason
                                        color: Theme.dim
                                        font.family: Theme.uiFont
                                        font.pixelSize: Theme.fCaption
                                    }
                                    Text { text: "Bubble"; color: Theme.dim; font.family: Theme.uiFont; font.pixelSize: Theme.fCaption }
                                    Flow {
                                        width: parent.width
                                        spacing: 6
                                        Repeater {
                                            model: root.bubbleEffects
                                            AppButton {
                                                required property string modelData
                                                text: root.effectNames[modelData]
                                                fontSize: Theme.fSmall
                                                enabled: root.abilities.effects
                                                selected: root.pendingEffect === modelData
                                                onClicked: { root.pendingEffect = modelData; root.effectPickerOpen = false; composer.forceActiveFocus(); }
                                            }
                                        }
                                    }
                                    Text { text: "Text"; color: Theme.dim; font.family: Theme.uiFont; font.pixelSize: Theme.fCaption }
                                    Flow {
                                        width: parent.width
                                        spacing: 6
                                        Repeater {
                                            model: root.textEffects
                                            AppButton {
                                                required property string modelData
                                                text: root.effectNames[modelData]
                                                fontSize: Theme.fSmall
                                                enabled: root.abilities.effects
                                                selected: root.pendingEffect === modelData
                                                onClicked: { root.pendingEffect = modelData; root.effectPickerOpen = false; composer.forceActiveFocus(); }
                                            }
                                        }
                                    }
                                    Text { text: "Screen"; color: Theme.dim; font.family: Theme.uiFont; font.pixelSize: Theme.fCaption }
                                    Flow {
                                        width: parent.width
                                        spacing: 6
                                        Repeater {
                                            model: root.screenEffects
                                            AppButton {
                                                required property string modelData
                                                text: root.effectNames[modelData]
                                                fontSize: Theme.fSmall
                                                enabled: root.abilities.effects
                                                selected: root.pendingEffect === modelData
                                                onClicked: {
                                                    root.pendingEffect = modelData;
                                                    root.effectPickerOpen = false;
                                                    screenFx.play(modelData, composer.text);   // a preview of what they'll see
                                                    composer.forceActiveFocus();
                                                }
                                            }
                                        }
                                    }
                                }
                            }

                            Text {
                                anchors.centerIn: parent
                                visible: !root.current && !root.composing
                                text: root.threads.length ? "Pick a conversation" : ""
                                color: Theme.dim
                                font.family: Theme.uiFont
                                font.pixelSize: Theme.fBody
                            }
                        }

                        // flash line
                        Text {
                            Layout.fillWidth: true
                            Layout.leftMargin: 18
                            visible: root.flash !== ""
                            text: root.flash
                            color: Theme.danger
                            font.family: Theme.uiFont
                            font.pixelSize: Theme.fSmall
                            wrapMode: Text.WordWrap
                            Timer { running: root.flash !== ""; interval: 6000; onTriggered: root.flash = "" }
                        }

                        // composer
                        Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: Math.min(160, composer.implicitHeight + 24)
                            color: Theme.bg
                            visible: root.current !== "" || root.composing
                            Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: Theme.line }
                            RowLayout {
                                anchors.fill: parent
                                anchors.margins: 12
                                anchors.leftMargin: 16
                                spacing: 10
                                ScrollView {
                                    Layout.fillWidth: true
                                    Layout.fillHeight: true
                                    TextArea {
                                        id: composer
                                        wrapMode: TextEdit.Wrap
                                        placeholderText: {
                                            const group = root.currentInfo && root.currentInfo.group;
                                            if (root.route === "bluebubbles") return "iMessage";
                                            if (root.route === "iphone") return group ? "Group chats need BlueBubbles" : "Message via your iPhone";
                                            return "Not connected";
                                        }
                                        placeholderTextColor: Theme.dim
                                        color: Theme.fg
                                        font.family: Theme.uiFont
                                        font.pixelSize: Theme.fBody
                                        selectByMouse: true
                                        background: Rectangle {
                                            color: Style.controlFill(composer.activeFocus, false, Theme.fg, Theme.accent)
                                            radius: Theme.radius
                                            border.width: 1
                                            border.color: composer.activeFocus ? Theme.accent : Theme.line
                                        }
                                        leftPadding: 12
                                        rightPadding: 12
                                        topPadding: 9
                                        bottomPadding: 9
                                        Keys.onPressed: function (ev) {
                                            if (ev.key === Qt.Key_Space && composer.text === "" && (root.builtinPreview || root.spacePreview())) {
                                                if (root.builtinPreview) root.builtinPreview = "";
                                                ev.accepted = true;
                                                return;
                                            }
                                            if (ev.key === Qt.Key_Escape && (root.builtinPreview || root.picker || root.effectPickerOpen)) {
                                                root.builtinPreview = ""; root.picker = null; root.effectPickerOpen = false;
                                                ev.accepted = true;
                                                return;
                                            }
                                            if ((ev.key === Qt.Key_Return || ev.key === Qt.Key_Enter) && !(ev.modifiers & Qt.ShiftModifier)) {
                                                root.sendCurrent();
                                                ev.accepted = true;
                                            }
                                        }
                                    }
                                }
                                Rectangle {
                                    Layout.alignment: Qt.AlignBottom
                                    visible: root.pendingEffect !== ""
                                    implicitHeight: 34
                                    implicitWidth: fxChip.implicitWidth + 34
                                    radius: Theme.radius
                                    color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.18)
                                    Text {
                                        id: fxChip
                                        x: 10
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: "✦ " + (root.effectNames[root.pendingEffect] || "")
                                        color: Theme.fg
                                        font.family: Theme.uiFont
                                        font.pixelSize: Theme.fSmall
                                    }
                                    Text {
                                        anchors.right: parent.right
                                        anchors.rightMargin: 8
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: "×"
                                        color: Theme.dim
                                        font.pixelSize: Theme.fBody
                                        TapHandler { onTapped: root.pendingEffect = "" }
                                    }
                                }
                                AppButton {
                                    Layout.alignment: Qt.AlignBottom
                                    text: "✦"
                                    selected: root.effectPickerOpen
                                    tooltipText: root.abilities.effects ? "Send with effect" : root.abilities.reason
                                    onClicked: { root.picker = null; root.effectPickerOpen = !root.effectPickerOpen; }
                                }
                                AppButton {
                                    Layout.alignment: Qt.AlignBottom
                                    text: "Send"
                                    enabled: composer.text.trim() !== "" && root.route !== "none"
                                    onClicked: root.sendCurrent()
                                }
                            }
                        }
                    }

                    // ============================================ settings sheet
                    Rectangle {
                        anchors.fill: parent
                        visible: root.settingsOpen
                        color: Theme.bg

                        MouseArea { anchors.fill: parent }   // swallow clicks behind the sheet

                        Flickable {
                            id: settingsFlick
                            anchors.fill: parent
                            contentHeight: settingsCol.implicitHeight + 40
                            clip: true
                            interactive: false
                            ScrollBar.vertical: AppScrollBar {}
                            ScrollPhysics { id: settingsPhys; flick: settingsFlick }
                            WheelHandler {
                                acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
                                onWheel: ev => settingsPhys.wheel(ev)
                            }
                            function stopPhysics() { settingsPhys.stopPhysics(); }

                            ColumnLayout {
                                id: settingsCol
                                x: 28
                                y: 20
                                width: settingsFlick.width - 56
                                spacing: 14

                                RowLayout {
                                    Layout.fillWidth: true
                                    Text {
                                        Layout.fillWidth: true
                                        text: "Settings"
                                        color: Theme.fg
                                        font.family: Theme.uiFont
                                        font.pixelSize: Theme.fHeading
                                        font.weight: Font.DemiBold
                                    }
                                    AppButton { text: "Done"; onClicked: root.settingsOpen = false }
                                }

                                Text {
                                    Layout.fillWidth: true
                                    wrapMode: Text.WordWrap
                                    text: "Pear Messages sends through BlueBubbles when it can reach your Mac, and falls back to your iPhone over Bluetooth when it can't. A message that arrives both ways is shown once."
                                    color: Theme.dim
                                    font.family: Theme.uiFont
                                    font.pixelSize: Theme.fSmall
                                }

                                // ---------------- BlueBubbles card
                                Rectangle {
                                    Layout.fillWidth: true
                                    implicitHeight: bbCol.implicitHeight + 32
                                    radius: Theme.radius
                                    color: "transparent"
                                    border.width: 1
                                    border.color: Theme.line

                                    ColumnLayout {
                                        id: bbCol
                                        x: 16; y: 16
                                        width: parent.width - 32
                                        spacing: 10

                                        RowLayout {
                                            Layout.fillWidth: true
                                            spacing: 10
                                            Text {
                                                text: "1"
                                                color: Theme.accent
                                                font.family: Theme.uiFont
                                                font.pixelSize: Theme.fBody
                                                font.weight: Font.Bold
                                            }
                                            ColumnLayout {
                                                Layout.fillWidth: true
                                                spacing: 1
                                                Text {
                                                    text: "BlueBubbles"
                                                    color: Theme.fg
                                                    font.family: Theme.uiFont
                                                    font.pixelSize: Theme.fBody + 1
                                                    font.weight: Font.DemiBold
                                                }
                                                Text {
                                                    Layout.fillWidth: true
                                                    wrapMode: Text.WordWrap
                                                    text: "Full iMessage: history, group chats, photos and reactions. Needs a Mac signed in to Messages running the free BlueBubbles Server."
                                                    color: Theme.dim
                                                    font.family: Theme.uiFont
                                                    font.pixelSize: Theme.fCaption
                                                }
                                            }
                                            StatePill { state: root.bb.state || "off" }
                                        }

                                        Text {
                                            Layout.fillWidth: true
                                            visible: root.bb.state === "online"
                                            wrapMode: Text.WordWrap
                                            text: "Connected to " + (root.bb.url || "") + " · BlueBubbles " + (root.bb.serverVersion || "?")
                                                  + (root.bb.macOS ? " on macOS " + root.bb.macOS : "")
                                                  + (root.bb.link === "tailscale" ? " · over Tailscale" : "")
                                                  + (root.bb.privateApi ? " · Private API on" : "")
                                            color: Theme.fg
                                            font.family: Theme.uiFont
                                            font.pixelSize: Theme.fSmall
                                        }
                                        Text {
                                            Layout.fillWidth: true
                                            visible: root.bb.state === "error" && !!root.bb.detail
                                            wrapMode: Text.WordWrap
                                            text: root.bb.detail || ""
                                            color: Theme.danger
                                            font.family: Theme.uiFont
                                            font.pixelSize: Theme.fSmall
                                        }

                                        GridLayout {
                                            Layout.fillWidth: true
                                            columns: 2
                                            columnSpacing: 10
                                            rowSpacing: 8
                                            Text { text: "Server address"; color: Theme.dim; font.family: Theme.uiFont; font.pixelSize: Theme.fSmall }
                                            O.TextField {
                                                id: bbUrl
                                                Layout.fillWidth: true
                                                placeholderText: "http://100.x.y.z:1234 or https://…"
                                                font.pixelSize: Theme.fSmall
                                                text: root.bb.url || ""
                                            }
                                            Text { text: "Password"; color: Theme.dim; font.family: Theme.uiFont; font.pixelSize: Theme.fSmall }
                                            O.TextField {
                                                id: bbPassword
                                                Layout.fillWidth: true
                                                password: true
                                                placeholderText: root.bb.configured ? "Saved — type to change" : "The server password from BlueBubbles"
                                                font.pixelSize: Theme.fSmall
                                                Keys.onReturnPressed: testBtn.clicked()
                                            }
                                        }

                                        RowLayout {
                                            Layout.fillWidth: true
                                            spacing: 8
                                            AppButton {
                                                id: testBtn
                                                text: root.bbTesting ? "Checking…" : "Connect"
                                                enabled: !root.bbTesting && bbUrl.text.trim() !== ""
                                                onClicked: {
                                                    root.bbTesting = true;
                                                    root.bbTest = {};
                                                    root.send({ op: "bb_test", url: bbUrl.text.trim(), password: bbPassword.text, save: true });
                                                }
                                            }
                                            AppButton {
                                                text: root.bbFinding ? "Looking…" : "Find my Mac"
                                                enabled: !root.bbFinding
                                                onClicked: { root.bbFinding = true; root.bbFound = null; root.send({ op: "bb_find" }); }
                                            }
                                            Item { Layout.fillWidth: true }
                                            AppButton {
                                                visible: !!root.bb.configured
                                                text: "Forget"
                                                onClicked: { root.send({ op: "bb_forget" }); bbUrl.text = ""; root.bbTest = {}; }
                                            }
                                        }

                                        Text {
                                            Layout.fillWidth: true
                                            visible: root.bbTest.ok === false
                                            wrapMode: Text.WordWrap
                                            text: root.bbTest.error || ""
                                            color: Theme.danger
                                            font.family: Theme.uiFont
                                            font.pixelSize: Theme.fSmall
                                        }
                                        Text {
                                            Layout.fillWidth: true
                                            visible: root.bbTest.ok === true
                                            wrapMode: Text.WordWrap
                                            text: "Saved. Your history is loading."
                                            color: Theme.accent
                                            font.family: Theme.uiFont
                                            font.pixelSize: Theme.fSmall
                                        }

                                        // Find my Mac results
                                        Text {
                                            Layout.fillWidth: true
                                            visible: root.bbFound !== null
                                            wrapMode: Text.WordWrap
                                            text: root.bbFound && root.bbFound.length
                                                  ? "Found on your Tailscale network — pick one, then enter its password:"
                                                  : "No BlueBubbles server found on your Tailscale network. Enter its address yourself (from BlueBubbles Server → Settings → Connection)."
                                            color: Theme.dim
                                            font.family: Theme.uiFont
                                            font.pixelSize: Theme.fSmall
                                        }
                                        Flow {
                                            Layout.fillWidth: true
                                            spacing: 8
                                            visible: !!root.bbFound && root.bbFound.length > 0
                                            Repeater {
                                                model: root.bbFound || []
                                                AppButton {
                                                    required property var modelData
                                                    text: modelData.name + "  " + modelData.ip
                                                    onClicked: { bbUrl.text = modelData.url; bbPassword.forceActiveFocus(); }
                                                }
                                            }
                                        }

                                        O.Toggle {
                                            Layout.fillWidth: true
                                            visible: !!root.bb.configured
                                            label: "Use BlueBubbles"
                                            description: "Turn off to use only the iPhone connection."
                                            checked: !!root.bb.enabled
                                            onClicked: root.send({ op: "settings", bluebubbles: { enabled: !root.bb.enabled } })
                                        }
                                    }
                                }

                                // ---------------- iPhone card
                                Rectangle {
                                    Layout.fillWidth: true
                                    implicitHeight: phCol.implicitHeight + 32
                                    radius: Theme.radius
                                    color: "transparent"
                                    border.width: 1
                                    border.color: Theme.line

                                    ColumnLayout {
                                        id: phCol
                                        x: 16; y: 16
                                        width: parent.width - 32
                                        spacing: 10

                                        RowLayout {
                                            Layout.fillWidth: true
                                            spacing: 10
                                            Text {
                                                text: "2"
                                                color: Theme.accent
                                                font.family: Theme.uiFont
                                                font.pixelSize: Theme.fBody
                                                font.weight: Font.Bold
                                            }
                                            ColumnLayout {
                                                Layout.fillWidth: true
                                                spacing: 1
                                                Text {
                                                    text: "iPhone over Bluetooth"
                                                    color: Theme.fg
                                                    font.family: Theme.uiFont
                                                    font.pixelSize: Theme.fBody + 1
                                                    font.weight: Font.DemiBold
                                                }
                                                Text {
                                                    Layout.fillWidth: true
                                                    wrapMode: Text.WordWrap
                                                    text: "No Mac needed. New messages arrive as they come in and you can reply one-to-one; your iPhone decides iMessage or SMS. No history or group chats — BlueBubbles fills those in."
                                                    color: Theme.dim
                                                    font.family: Theme.uiFont
                                                    font.pixelSize: Theme.fCaption
                                                }
                                            }
                                            StatePill { state: root.phone.state || "off" }
                                        }

                                        Text {
                                            Layout.fillWidth: true
                                            visible: !!root.phone.address
                                            wrapMode: Text.WordWrap
                                            text: (root.phone.name || root.phone.address) + (root.phone.detail ? " — " + root.phone.detail : "")
                                            color: root.phone.state === "needs_permission" ? Theme.danger : Theme.fg
                                            font.family: Theme.uiFont
                                            font.pixelSize: Theme.fSmall
                                        }

                                        // pairing
                                        Rectangle {
                                            Layout.fillWidth: true
                                            visible: root.status.pairing || ["confirm", "pairing", "paired", "timeout", "rejected", "cancelled"].indexOf(root.pair.stage) >= 0
                                            implicitHeight: pairCol.implicitHeight + 24
                                            radius: Theme.radius
                                            color: Theme.panel
                                            ColumnLayout {
                                                id: pairCol
                                                x: 14; y: 12
                                                width: parent.width - 28
                                                spacing: 8
                                                Text {
                                                    Layout.fillWidth: true
                                                    wrapMode: Text.WordWrap
                                                    color: Theme.fg
                                                    font.family: Theme.uiFont
                                                    font.pixelSize: Theme.fSmall
                                                    text: {
                                                        const p = root.pair;
                                                        switch (p.stage) {
                                                        case "waiting": return "On your iPhone, open Settings → Bluetooth and tap “" + (p.computer || "this computer") + "” under Other Devices.";
                                                        case "confirm": return "Does " + (p.device || "your iPhone") + " show this code?";
                                                        case "pairing": return "Pairing…";
                                                        case "paired": return "Paired. Last step, on the iPhone: Settings → Bluetooth → ⓘ next to this computer → turn on Show Notifications and Sync Contacts.";
                                                        case "timeout": return "Nothing paired in time. Try again, with the iPhone's Bluetooth settings open.";
                                                        case "rejected": return "Pairing cancelled.";
                                                        case "cancelled": return "The iPhone cancelled pairing.";
                                                        }
                                                        return "Getting ready…";
                                                    }
                                                }
                                                Text {
                                                    visible: root.pair.stage === "confirm"
                                                    text: (root.pair.code || "").replace(/(\d{3})(\d{3})/, "$1 $2")
                                                    color: Theme.accent
                                                    font.family: Theme.uiFont
                                                    font.pixelSize: Theme.fHeading * 1.8
                                                    font.weight: Font.DemiBold
                                                    font.letterSpacing: 2
                                                }
                                                RowLayout {
                                                    visible: root.pair.stage === "confirm"
                                                    spacing: 8
                                                    AppButton { text: "Yes, it matches"; onClicked: root.send({ op: "pair_answer", accept: true }) }
                                                    AppButton { text: "No"; onClicked: root.send({ op: "pair_answer", accept: false }) }
                                                }
                                            }
                                        }

                                        RowLayout {
                                            Layout.fillWidth: true
                                            spacing: 8
                                            AppButton {
                                                text: root.status.pairing ? "Stop pairing" : (root.phone.address ? "Pair another iPhone" : "Pair iPhone")
                                                onClicked: root.send({ op: root.status.pairing ? "pair_stop" : "pair_start" })
                                            }
                                            AppButton {
                                                visible: !!root.phone.address && root.phone.state !== "online"
                                                text: "Reconnect"
                                                onClicked: root.send({ op: "iphone_reconnect" })
                                            }
                                            Item { Layout.fillWidth: true }
                                        }

                                        // more than one paired iPhone: pick
                                        Flow {
                                            Layout.fillWidth: true
                                            spacing: 8
                                            visible: (root.phone.paired || []).length > 1
                                            Repeater {
                                                model: root.phone.paired || []
                                                AppButton {
                                                    required property var modelData
                                                    text: (modelData.address === root.phone.address ? "✓ " : "") + modelData.name
                                                    onClicked: root.send({ op: "iphone_use", address: modelData.address })
                                                }
                                            }
                                        }

                                        O.Toggle {
                                            Layout.fillWidth: true
                                            visible: !!root.phone.address
                                            label: "Use the iPhone connection"
                                            description: "Receive and send through the iPhone when BlueBubbles can't."
                                            checked: !!root.phone.enabled
                                            onClicked: root.send({ op: "settings", iphone: { enabled: !root.phone.enabled } })
                                        }
                                        O.Toggle {
                                            Layout.fillWidth: true
                                            visible: !!root.phone.address
                                            label: "Keep the iPhone's audio on the iPhone"
                                            description: "Paired iPhones otherwise play music and calls through this computer."
                                            checked: root.phone.keepAudio !== false
                                            onClicked: root.send({ op: "settings", iphone: { keepAudio: !(root.phone.keepAudio !== false) } })
                                        }
                                    }
                                }

                                // ---------------- notifications
                                Text {
                                    Layout.topMargin: 6
                                    text: "Notifications"
                                    color: Theme.fg
                                    font.family: Theme.uiFont
                                    font.pixelSize: Theme.fBody + 1
                                    font.weight: Font.DemiBold
                                }
                                O.Toggle {
                                    Layout.fillWidth: true
                                    label: "Show notifications"
                                    description: "Also when this window is closed."
                                    checked: !!(root.status.settings && root.status.settings.notifications)
                                    onClicked: root.send({ op: "settings", notifications: !root.status.settings.notifications })
                                }
                                O.Toggle {
                                    Layout.fillWidth: true
                                    label: "Show message text"
                                    description: "Off shows only who it's from."
                                    checked: !!(root.status.settings && root.status.settings.notificationPreview)
                                    onClicked: root.send({ op: "settings", notificationPreview: !root.status.settings.notificationPreview })
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    component StatePill: Rectangle {
        property string state: "off"
        implicitHeight: 24
        implicitWidth: pillText.implicitWidth + 20
        radius: Theme.radius
        color: state === "online" ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.18)
             : (state === "error" || state === "needs_permission") ? Qt.rgba(Theme.danger.r, Theme.danger.g, Theme.danger.b, 0.2)
             : Theme.panel
        Text {
            id: pillText
            anchors.centerIn: parent
            text: root.stateText(parent.state)
            color: parent.state === "online" ? Theme.accent : (parent.state === "error" || parent.state === "needs_permission") ? Theme.danger : Theme.dim
            font.family: Theme.uiFont
            font.pixelSize: Theme.fCaption
            font.weight: Font.DemiBold
        }
    }

    // ------------------------------------------------------------------ preview data
    Timer {
        id: snapshotTimer
        interval: Number(Quickshell.env("PEAR_MESSAGES_SNAPSHOT_MS") || 1500)
        onTriggered: scope.grabToImage(function (r) { r.saveToFile(root.snapshotPath); Qt.quit(); })
    }

    Component.onCompleted: {
        root.appliedTheme = themeColors.text();
        // Development: a snapshot of the real window (connected to the running service).
        if (root.preview === "" && root.snapshotPath) { snapshotTimer.interval = Number(Quickshell.env("PEAR_MESSAGES_SNAPSHOT_MS") || 4000); snapshotTimer.start(); }
        if (root.preview === "") return;
        const now = Date.now() / 1000;
        root.status = {
            route: root.preview === "offline" ? "none" : "bluebubbles",
            bluebubbles: { state: "online", configured: true, enabled: true, url: "http://100.101.102.103:1234", link: "tailscale",
                           serverVersion: "1.9.9", macOS: "27.0", privateApi: false },
            iphone: { state: "online", address: "AA:BB:CC:DD:EE:FF", name: "Alex’s iPhone", enabled: true, keepAudio: true, paired: [] },
            settings: { notifications: true, notificationPreview: true }, pairing: root.preview === "pair"
        };
        root.threads = [
            { id: "addr:1", title: "Sam Rivera", preview: "Sounds good, see you at 7!", ts: now - 120, unread: 1, fromMe: false, group: false, address: "+44 7700 900123", participants: ["+44 7700 900123"] },
            { id: "chat:2", title: "Climbing crew", preview: "Who's bringing the rope?", ts: now - 3600, unread: 0, fromMe: false, group: true, participants: ["a", "b", "c", "d"] },
            { id: "addr:3", title: "Mum", preview: "Call me when you land x", ts: now - 86400 * 1.2, unread: 0, fromMe: false, group: false, participants: ["x"] },
            { id: "addr:4", title: "Jordan Lee", preview: "Photo", ts: now - 86400 * 3, unread: 0, fromMe: true, group: false, participants: ["y"] },
            { id: "addr:5", title: "+1 555 010 0123", preview: "Your code is 481 223", ts: now - 86400 * 9, unread: 0, fromMe: false, group: false, participants: ["z"] }
        ];
        root.current = "addr:1";
        root.currentInfo = root.threads[0];
        root.msgs = [
            { id: 1, thread: "addr:1", fromMe: false, sender: "+44", text: "Are we still on for dinner tonight?", ts: now - 7400, status: "", attachments: [], reactions: {} },
            { id: 2, thread: "addr:1", fromMe: true, text: "Yes! The new ramen place on King St — https://example.com/ramen", ts: now - 7300, status: "read", via: "bluebubbles", attachments: [], reactions: { "+44": "love" } },
            { id: 3, thread: "addr:1", fromMe: false, sender: "+44", text: "Perfect. What time works for you?", ts: now - 300, status: "", attachments: [], reactions: {} },
            { id: 4, thread: "addr:1", fromMe: true, text: "7?", ts: now - 200, status: "delivered", via: "iphone", attachments: [], reactions: {} },
            { id: 5, thread: "addr:1", fromMe: false, sender: "+44", text: "Sounds good, see you at 7!", ts: now - 120, status: "", attachments: [], reactions: { me: "like" }, guid: "G5",
              effect: "com.apple.MobileSMS.expressivesend.impact" },
            { id: 6, thread: "addr:1", fromMe: false, sender: "+44", text: "", ts: now - 100, status: "", reactions: {}, guid: "G6",
              attachments: [{ guid: "A6", mime: "video/quicktime", name: "IMG_4412.MOV", size: 48200000 }] },
            { id: 8, thread: "addr:1", fromMe: false, sender: "+44", text: "Wow! 🤯 that is huge", ts: now - 80, status: "", reactions: {}, guid: "G8", attachments: [],
              segments: [{ text: "Wow! 🤯 ", styles: [], effect: "explode" }, { text: "that is ", styles: [], effect: "" }, { text: "huge", styles: ["bold"], effect: "big" }] },
            { id: 9, thread: "addr:1", fromMe: false, sender: "+44", text: "so bold and italic and struck", ts: now - 75, status: "", reactions: {}, guid: "G9", attachments: [],
              segments: [{ text: "so ", styles: [], effect: "" }, { text: "bold", styles: ["bold"], effect: "" }, { text: " and ", styles: [], effect: "" },
                         { text: "italic", styles: ["italic"], effect: "" }, { text: " and ", styles: [], effect: "" }, { text: "struck", styles: ["strikethrough"], effect: "" }] },
            { id: 7, thread: "addr:1", fromMe: true, text: "psst — the surprise is at 8", ts: now - 60, status: "delivered", via: "bluebubbles", reactions: {}, guid: "G7",
              attachments: [], effect: "com.apple.MobileSMS.expressivesend.invisibleink" }
        ];
        if (root.preview === "settings" || root.preview === "pair") root.settingsOpen = true;
        if (root.preview === "pair") root.pair = { stage: "confirm", device: "Alex’s iPhone", code: "436952" };
        const reason = "Reactions and effects need BlueBubbles. You're connected through your iPhone over Bluetooth, which only carries plain text.";
        if (root.preview === "react" || root.preview === "effects" || root.preview === "iphone") {
            root.status = Object.assign({}, root.status, { route: "iphone", abilities: { reactions: false, effects: false, reason: reason } });
        } else {
            root.status = Object.assign({}, root.status, { abilities: { reactions: true, effects: true, reason: "" } });
        }
        if (root.preview === "effects") root.effectPickerOpen = true;
        if (root.preview === "react") Qt.callLater(() => { root.picker = { id: 5, m: root.msgs[4], x: 20, y: 470, w: 240, mine: false }; });
        if (root.preview === "reactok") Qt.callLater(() => { root.picker = { id: 5, m: root.msgs[4], x: 20, y: 470, w: 240, mine: false }; });
        // fx_<Name>: play that screen effect, e.g. PEAR_MESSAGES_PREVIEW=fx_Confetti
        if (root.preview === "textfx") {
            const fxs = ["big", "small", "shake", "nod", "explode", "ripple", "bloom", "jitter"];
            root.msgs = fxs.map((f, k) => ({ id: 100 + k, thread: "addr:1", fromMe: k % 2 === 1, sender: "+44", text: f + " effect 🎉",
                ts: now - 60 + k, status: k % 2 ? "delivered" : "", reactions: {}, guid: "T" + k, attachments: [],
                segments: [{ text: f.charAt(0).toUpperCase() + f.slice(1) + " effect 🎉", styles: [], effect: f }] }));
        }
        if (root.preview === "keyword") Qt.callLater(() => root.playEffect({ id: 99, text: "Happy birthday!!", effect: "" }, true));
        if (root.preview.indexOf("fx_") === 0) {
            const want = root.preview.slice(3).replace(/_/g, " ");
            const id = Object.keys(root.effectNames).find(k => root.effectNames[k] === want);
            if (id) Qt.callLater(() => screenFx.play(id, "Happy birthday!"));
        }
        if (root.preview === "compose") { root.composing = true; root.current = ""; root.msgs = []; root.searchResults = [{ name: "Sam Rivera", addr: "+44 7700 900123" }, { name: "Sam Okafor", addr: "sam@example.com" }]; }
        if (root.snapshotPath) snapshotTimer.start();
    }
}
