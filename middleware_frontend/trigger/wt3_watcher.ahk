; trigger/wt3_watcher.ahk
;
; Monitors the WT3 window for message selection events.
; When the broker clicks a message, captures the text via
; clipboard and sends the order body to the Python backend
; for immediate matching.
;
; Requirements: AutoHotkey v2 (https://www.autohotkey.com)
; Run this script on startup alongside the Electron panel.

#Requires AutoHotkey v2.0
#SingleInstance Force

BACKEND_URL := "http://127.0.0.1:5000/internal/ingest"
WT3_WINDOW   := "ahk_exe wtelix.exe"   ; adjust to match actual WT3 process name

; ── Tray icon ────────────────────────────────────────────────────────────────
TraySetIcon("shell32.dll", 165)
A_TrayMenu.Delete()
A_TrayMenu.Add("Maritime Panel — running", (*) => {})
A_TrayMenu.Disable("Maritime Panel — running")
A_TrayMenu.Add()
A_TrayMenu.Add("Exit", (*) => ExitApp())

; ── Hook: broker clicks inside WT3 ──────────────────────────────────────────
; Listen for Ctrl+C inside the WT3 window, which fires when a message
; is selected and copied. A background timer also monitors clipboard
; changes while WT3 is the active window.

last_clip := ""

SetTimer(CheckClipboard, 500)

CheckClipboard() {
    global last_clip, BACKEND_URL, WT3_WINDOW

    ; Only act when WT3 is the active window
    if !WinActive(WT3_WINDOW)
        return

    clip := A_Clipboard
    if (clip = "" || clip = last_clip)
        return

    ; Ignore very short clips — probably not a full message
    if (StrLen(clip) < 20)
        return

    last_clip := clip

    ; Fire POST to backend asynchronously (non-blocking)
    PostToBackend(clip)
}

PostToBackend(body) {
    global BACKEND_URL

    ; Escape JSON
    body := StrReplace(body, "\", "\\")
    body := StrReplace(body, '"', '\"')
    body := StrReplace(body, "`n", "\n")
    body := StrReplace(body, "`r", "")

    payload := '{"sender":"wt3-clipboard","subject":"WT3 selection","raw_body":"' . body . '"}'

    ; Use PowerShell's Invoke-WebRequest for the HTTP POST
    ; (no external dependencies needed)
    cmd := 'powershell -WindowStyle Hidden -Command "'
        . 'Invoke-WebRequest -Uri ''' . BACKEND_URL . ''' '
        . '-Method POST '
        . '-ContentType ''application/json'' '
        . '-Body ''' . payload . ''' '
        . '-UseBasicParsing | Out-Null"'

    Run(cmd, , "Hide")
}

; ── Alternative: Ctrl+C shortcut inside WT3 ─────────────────────────────────
; If the broker uses Ctrl+C to copy a message in WT3,
; this fires immediately rather than waiting for the timer.

#HotIf WinActive(WT3_WINDOW)
^c:: {
    Send("^c")          ; let WT3 copy as normal
    Sleep(100)          ; wait for clipboard to update
    CheckClipboard()    ; immediately check
}
#HotIf
