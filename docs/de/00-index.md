# Regulatorische Dokumentation (deutsch)

Diese Dokumente beschreiben die Durchführung von ISiK-Konformitätstests der
Stufen 3 und 5 gegen ein FHIR-Repository von InterSystems IRIS for Health, in der
Sprache der ISiK-Spezifikation und des *Bestätigungsverfahrens*.

| Dokument | Inhalt |
|---|---|
| [01-uebersicht.md](01-uebersicht.md) | Prüfgegenstand, Prüfwerkzeug, Prüfumgebung, Prüfumfang |
| [02-bestaetigungsverfahren.md](02-bestaetigungsverfahren.md) | Die durchgeführten Schritte, in der Reihenfolge ihrer Ausführung, mit Nachweis je Schritt |
| [03-ergebnisse.md](03-ergebnisse.md) | Wie ein Ergebnis dieser Umgebung zu lesen ist, welche Nachweise es belegen, und die Zugangsdaten zur Umgebung |

Das Modul `Connect` der Stufe 5 weist sich mit OAuth-2.0-Zugriffstoken selbst aus
und setzt deshalb einen Autorisierungsserver und eine TLS-Verbindung voraus, die
kein anderes Modul benötigt. Verfahren und Bewertung dazu stehen in den drei
Dokumenten oben; die technische Darstellung in
[../en/08-connect.md](../en/08-connect.md).

**Kein Übersetzungsdokument.** Die technische Spezifikation und Beschreibung der
Testumgebung ist englischsprachig und liegt in [`../en/`](../en/). Diese Dokumente
sind keine Übersetzung davon, sondern behandeln das Verfahren und die Bewertung von
Ergebnissen. Wo beide dieselbe Tatsache berühren, ist die englische Fassung die
technisch ausführlichere; ein Widerspruch zwischen beiden ist ein Fehler und keine
Nuance.

**Zur Sprachwahl.** Anforderungstexte aus den Testfällen des Prüfwerkzeugs werden
grundsätzlich **wörtlich und unübersetzt** wiedergegeben. Die ISiK-Spezifikation ist
bewusst ausschließlich deutschsprachig veröffentlicht, um Übersetzungsfehler und
Mehrdeutigkeiten zu vermeiden; eine Übersetzung würde eine Auslegung zwischen die
Spezifikation und den Test setzen.

**Maßgeblich ist der offizielle Bericht.** Alle abgeleiteten Dokumente dieses
Projekts — auch die hier beschriebenen — sind nachrangig gegenüber
`test-report.zip`, das die offizielle ISiK-Testsuite selbst erzeugt und das
unverändert archiviert wird. Bei Abweichungen gilt der offizielle Bericht.
