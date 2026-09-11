# ChatGPT Classic Response Audiovisual Alert System

Windows-notifiering för **ChatGPT Classic Desktop**. Projektet är byggt som en sibling till `krs_audiovisual_alert_system_repo`: samma idé med ljud + KRS-popup + debounce + loggning, men med en separat watcher som detekterar när ett svar i ChatGPT Classic går från **genererar** till **klart**.

## Hur det fungerar

`chatgpt_watcher.py` använder Windows UI Automation via `pywinauto` och letar efter ChatGPT Classics fönster (`ChatGPT.exe`, titel `ChatGPT`). Under pågående svar exponeras normalt en stop-knapp, t.ex. `Stop generating` eller `Stop streaming`. Watchern kräver följande tillståndsövergång:

`IDLE -> GENERATING -> IDLE (stabilt i 0,9 s) -> notifiera`

Det betyder att gamla redan färdiga konversationer inte ska skapa notifieringar bara för att de öppnas. Watchern behöver inte läsa själva konversationstexten och behöver ingen OpenAI API-nyckel.

När completion är bekräftad startas `notify.py`, som hanterar popup, ljud, debounce och loggar. Om ChatGPT Classic är det aktiva/fokuserade fönstret kan både ljud och popup undertryckas, precis som i Codex-versionens fokuslogik.

## Installation

1. Öppna en terminal i repomappen.
2. Kör `install.cmd` en gång. Den skapar `.venv` och installerar `pywinauto` + `psutil` lokalt i projektet.
3. Öppna **ChatGPT Classic Desktop**.
4. Kör `diagnose_chatgpt.cmd`.
5. Om diagnosen hittar ChatGPT-fönstret, kör `test_the_app.cmd` för att verifiera ljud + popup.
6. Starta den riktiga watchern med `launch_watcher.cmd`.

För felsökning med synlig konsol: `launch_watcher_visible.cmd`.

Stoppa watchern med `stop_watcher.cmd`.

## Autostart

- `enable_startup.cmd` skapar en genväg i din Windows Startup-mapp.
- `disable_startup.cmd` tar bort den igen.

## Viktiga filer

- `chatgpt_watcher.py` — detekterar ChatGPT Classic och response-completion.
- `notify.py` — ljud, popup, fokus-undertryckning, debounce och notifieringslogg.
- `settings.json` — all konfiguration.
- `notify_popup.hta` — primär KRS-popup.
- `notify_popup.vbs` — fallback-popup.
- `diagnose_chatgpt.cmd` — visar hittade ChatGPT-fönster och UIA-knappnamn.
- `logs/chatgpt_watcher.jsonl` — watcher-event.
- `logs/notify_events.jsonl` — notifieringsresultat.

## Konfiguration

De viktigaste värdena i `settings.json`:

- `generation_button_patterns`: regex för UIA-namnet på knappen som endast finns medan svar genereras.
- `watcher_poll_seconds`: pollingintervall, default `0.45` s.
- `completion_settle_seconds`: hur länge stop-knappen måste vara borta innan completion accepteras, default `0.9` s.
- `classic_path_contains`: används för att skilja Classic-paketet `OpenAI.ChatGPT-Desktop` från andra appar som också kan ha `ChatGPT.exe`.
- `suppress_visual_when_chatgpt_focused` / `suppress_sound_when_chatgpt_focused`: samma princip som i Codex-alertsystemet.

Om OpenAI ändrar accessible-name på stop-knappen krävs normalt bara att lägga till namnet i `generation_button_patterns`; `diagnose_chatgpt.cmd` listar de knappnamn som UI Automation ser.

## Statusmodell och falskpositivskydd

Watchern notifierar **inte** från ett statiskt IDLE-läge. Den måste först själv ha sett ett aktivt generationstillstånd. När stop-kontrollen försvinner väntar den dessutom `completion_settle_seconds` och verifierar att generationen inte återkommer. Om ChatGPT-fönstret försvinner medan ett svar körs avbryts kandidaten i stället för att notifiera.

## Första verifiering på datorn

Efter `install.cmd`, ha ChatGPT Classic öppet och kör:

```text
diagnose_chatgpt.cmd
```

Starta sedan ett vanligt ChatGPT-svar och kör `launch_watcher_visible.cmd`. I `logs/chatgpt_watcher.jsonl` ska sekvensen bli ungefär:

```text
generation_started
completion_candidate
response_completed_notified
```

När detta är verifierat kan den dolda `launch_watcher.cmd` användas till vardags.
