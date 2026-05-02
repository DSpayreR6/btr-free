# btr-free – Projektspezifikation

## Ziel

TUI-Tool zur Analyse von Btrfs-Snapshot-Speicherbelegung. Beantwortet die Frage: **„Was wird frei, wenn ich diese Snapshots lösche?"** – bevor irgendetwas gelöscht wird.

Erstversion: reines Auswertungstool, kein Löschen.

---

## Konzeptionelles Modell

Jeder Datenbestand (Snapshot + produktives System) wird als „Landkarte" betrachtet. Übereinandergelegt entstehen abgegrenzte Mosaic-Bereiche, jeder mit einer eindeutigen Signatur: welche Datensätze referenzieren diesen Block.

Drei Zustände:

- **Alles**: Gesamtbelegung aller Datensätze inkl. produktivem System (`btrfs filesystem usage`)
- **Kontinent**: Schnitt aller Datensätze – Blöcke die immer belegt sind, durch nichts freizubekommen
- **Adressierbare Fluktuation**: Blöcke die in Snapshots vorkommen, im produktiven System aber nicht mehr referenziert sind – das einzige was durch Löschen gewinnbar ist

Formel: **Gewinnbar = Fluktuation ∩ nicht-produktiv ∩ alle Referenzen in Löschmenge**

---

## Technische Grundlage

### Platzberechnung für eine Snapshot-Gruppe

Btrfs qgroup-Hierarchie:

```bash
btrfs qgroup create 1/1 /
btrfs qgroup assign 0/<subvol-id> 1/1 /   # wiederholen für alle gewählten Snapshots
btrfs quota rescan -w /
btrfs qgroup show -p /                     # excl von 1/1 = tatsächlich freiwerdender Platz
btrfs qgroup destroy 1/1 /
```

Der `excl`-Wert der Gruppe ist nicht die Summe der Einzel-`excl`-Werte, sondern die korrekte Mengenlehre-Berechnung über alle gewählten Snapshots gemeinsam – inkl. Berücksichtigung des produktiven Systems.

### Snapshot-Liste

```bash
sudo btrfs subvolume list /
sudo btrfs qgroup show -reF /
```

### rescan-Dauer

Auf einem Testsystem mit ~120 Subvolumes: ca. 2 Minuten (abhängig von Subvolume-Anzahl und Hardware).

---

## Cache-Strategie

- **Altbestand** (Snapshots älter als heute): Signaturen ändern sich nie → einmalig berechnen, in SQLite cachen
- **Aktuell** (heutiger Tag + produktives System): bei jeder Abfrage frisch laden
- Cache-Aktualisierung: systemd oneshot-Service beim Systemstart, maximal einmal pro Woche

Beim Start prüfen ob Cache älter als 7 Tage → wenn ja: rescan + neu schreiben.

---

## System-Kontext

- NixOS, Btrfs, mehrere Subvolumes mit Snapper-Snapshots
- Filesystem-Root: `/`
- Quota bereits aktiv

---

## UI – TUI mit `textual`

### Ablauf

1. **Startbildschirm**: Auswahl welche Subvolume-Gruppen betrachtet werden sollen (Checkboxen: root, homenix, home, data)
2. **Hauptansicht**: Liste aller Snapshots der gewählten Gruppen
3. **Auswahl**: Snapshots per Tastatur/Maus markieren
4. **Berechnung**: Button „Berechnen" → temporäre qgroup wird gebaut, rescan, Ergebnis
5. **Ergebnis**: Anzeige „Diese Auswahl würde X freigeben" + Gesamtübersicht (Alles / Kontinent / Fluktuation)

### Spalten Snapshot-Liste

| Spalte | Inhalt |
|---|---|
| Datum | Snapshot-Zeitstempel |
| Subvolume | z.B. `root`, `homenix` |
| Referenziert | `rfer`-Wert aus qgroup |
| Exklusiv | `excl`-Wert (Untergrenze freiwerdend bei Einzellöschung) |
| Auswahl | Checkbox |

### Footer

- Summe gewählter `excl`-Werte (Untergrenze, sofort)
- Berechneter Wert nach qgroup-Abfrage (exakt, nach Berechnen-Button)

---

## Technischer Stack

- **Sprache**: Python
- **TUI**: `textual`
- **Datenhaltung Cache**: SQLite
- **Btrfs-Zugriff**: subprocess auf `btrfs`-CLI
- **Rechte**: Tool wird mit `sudo` gestartet

---

## Nicht in Erstversion

- Löschen von Snapshots
- Grafische Turm-Visualisierung
- Automatischer Cache-Service (erst wenn Grundtool funktioniert)
- Unterstützung mehrerer Filesystems

---

## Arbeitstitel

**btr-free**
