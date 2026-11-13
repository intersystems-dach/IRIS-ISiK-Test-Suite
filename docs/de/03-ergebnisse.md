# 3. Ergebnisse: Aufbau, Lesart, Nachweis, Zugangsdaten

## Drei Dokumente und ein Anhang, eines davon maßgeblich

| Dokument | Erzeugt von | Adressat | Status |
|---|---|---|---|
| `<stufe>/official/test-report.zip` | der offiziellen ISiK-Testsuite selbst | Gematik / TITUS | **maßgeblich** |
| `analysis/results.md` | diesem Projekt, zitierend | prüfende Stelle | abgeleitet |
| `GAP-ANALYSIS.md` | diesem Projekt, auslegend | Produktentwicklung InterSystems | abgeleitet, englischsprachig, **keine regulatorische Dokumentation** |
| `CASE-EVIDENCE.md` | diesem Projekt, zitierend | Produktentwicklung InterSystems | abgeleitet, englischsprachig, Anhang zur Lückenanalyse, **keine regulatorische Dokumentation** |

> Bei einer Abweichung zwischen einem abgeleiteten Dokument und dem offiziellen
> Bericht gilt der offizielle Bericht. Die abgeleiteten Dokumente zitieren ihn; sie
> ersetzen ihn nicht und ändern ihn nicht.

Das offizielle Archiv wird **unverändert** archiviert. Kein Skript dieses Projekts
schreibt in es hinein. Zusätzlich wird es ausgepackt — Original und Auszug liegen
nebeneinander —, damit eine prüfende Person einer Zitatangabe folgen kann, ohne
selbst entpacken zu müssen, und den Auszug gegen das Original prüfen kann.

## Aufbau des Nachweisdatensatzes

```
reports/<run-id>/
  README.md                              Einstiegspunkt des Datensatzes
  analysis/results.md                    das zitierende Ergebnisdokument
  analysis/results.json                  dasselbe, maschinenlesbar
  analysis/scenario-verdicts.csv         eine Zeile je Testfall
  analysis/MANIFEST.txt                  SHA-256 über jede Datei des Datensatzes
  GAP-ANALYSIS.md                        die Lückenanalyse für die Produktentwicklung
  gap-analysis.json                      dasselbe, maschinenlesbar, für Vergleiche
  CASE-EVIDENCE.md                       deren Anhang, eine Klausel je fehlgeschlagenem Fall
  case-evidence.json                     dasselbe, maschinenlesbar, für Vergleiche
  stufe3/official/*.zip                  unverändert, wie die Suite sie geschrieben hat
  stufe3/official/SHA256SUMS             deren Prüfsummen, wie empfangen
  stufe3/official/extracted/…            der wörtliche Auszug beider Archive
  stufe3/run.json, run.log
  stufe5/…
  provenance/                            alles, was das Ergebnis erklärt
```

**Der Datensatz ist ohne dieses Projektarchiv vollständig.** Nachweis, der im
Betriebsverzeichnis liegt, wird hineinkopiert, denn `make clean` löscht dieses
Verzeichnis und der nächste Durchgang überschreibt die Paketablage; ein archivierter
Bericht würde sonst auf Dateien verweisen, die nicht mehr existieren. Das
Verzeichnis `reports/` wird von keinem Arbeitsschritt gelöscht.

Zum Nachweis gehört auch das **Prüfmittel selbst**: `provenance/rig-version.txt`
nennt Version und Stand der hier beschriebenen Prüfumgebung, die den Datensatz
erzeugt hat. Weichen zwei Datensätze voneinander ab, ist damit unterscheidbar, ob
sich der Prüfgegenstand oder die Messung geändert hat.

### Die beiden offiziellen Archive

| `test-report.zip` — eingereicht | `debug-report.zip` — nicht eingereicht |
|---|---|
| `unresolvedReports/*.json` — die Serenity-Ergebnisdatensätze je Testfall | `serenity/` — der browsbare HTML-Bericht, mit Anfrage- und Antwortinhalten |
| `serenity-summary.html` — die einseitige Zusammenfassung | `serenity-summary.json` — Serenitys eigene Zählung, hier als unabhängige Gegenprüfung verwendet |
| `pom.xml` — womit die Versionsangaben des Werkzeugs **aus der Einreichung selbst** überprüfbar sind | `config/tiger.yaml` — die von diesem Projekt erzeugte Konfiguration, so wie die Suite sie erhalten hat |
| | `logs/` — Maven- und Serverprotokolle |

Die Datei `config/tiger.yaml` im Diagnosearchiv ist genau die erzeugte
Konfigurationsdatei dieser Stufe. Sie enthält das Zugangsdatum der API — absichtlich,
siehe unten. `provenance/tiger-RENDERED.txt` führt je erzeugte Datei ihren SHA-256,
so dass die Konfiguration im Archiv gegen die von diesem Projekt erzeugte
abgeglichen werden kann.

## Wie ein Ergebnis zu lesen ist

### Die maßgebliche Bezugsgröße

Jede Zahl wird zweimal berichtet, und die beiden sind nicht austauschbar:

* **ausgeführte Fälle** — alles, was Cucumber ausgeführt hat, **einschließlich** der
  `@vorbedingung`-Blöcke. Diese Blöcke sind echte Szenarien und laufen; ihre
  Schritte halten die benötigten Testdaten als Prosa fest und sichern gegenüber dem
  Prüfgegenstand nichts zu.
* **prüfende Fälle** — die ausgeführten Fälle, die den Prüfgegenstand tatsächlich
  prüfen. **Dies ist die Bezugsgröße einer Konformitätsaussage.**

| | Stufe 3 | Stufe 5 | Summe |
|---|---:|---:|---:|
| ausgeführt | 506 | 895 | **1401** |
| prüfend | 432 | 783 | **1215** |
| Vorbedingungsblöcke | 74 | 112 | **186** |

Die 186 Vorbedingungsblöcke sind 13 % der ausgeführten Fälle und bestehen
ausnahmslos. Eine auf 1401 bezogene Erfolgsquote kann daher unabhängig vom
Verhalten des Prüfgegenstands nicht unter 13 % fallen und ist damit **zu
günstig**; ein zu günstiger Fehler ist derjenige, nach dem eine prüfende Stelle
zuerst sucht.

Die Tabelle ist **auf Testsuite 2.7.0 gezählt**, der letzten Version, die beide
Stufen enthält; 3.0.0 hat die Stufe-3-Tests entfernt. Alle drei Werte sind
Eigenschaften der Testsuite-Version und ändern sich mit ihr — auf 3.0.9 lauten
dieselben Zählungen 1 233 ausgeführt, 1 044 prüfend und 189 Vorbedingungsblöcke,
und zwar für Stufe 5 allein. Eine Bezugsgröße darf nicht von einer
Testsuite-Version in eine Aussage über eine andere übernommen werden;
`results_report.py` leitet alle drei je Durchgang aus den Ergebnisdatensätzen des
Durchgangs selbst ab.

### Die Umfangsabstimmung ist vor den Ergebnissen zu lesen

Abschnitt 2 des Ergebnisdokuments stellt **drei voneinander unabhängige Zählungen**
je Stufe nebeneinander:

1. **erwartet** — aus `provenance/requirements.json`, also aus den Feature-Dateien
   des festgelegten Images, ausgewertet gegen den Tag-Ausdruck, mit dem die Stufe
   tatsächlich gelaufen ist;
2. **Bericht** — gezählt aus den Ergebnisdatensätzen im offiziellen Archiv;
3. **unabhängig** — Serenitys eigene Zählung aus dem Diagnosearchiv, die
   ausschließlich dazu dient, einen Zählfehler dieses Projekts aufzudecken.

Stimmen sie nicht überein, wird dies als Auffälligkeit gemeldet und **keine** der
Zählungen zur maßgeblichen erklärt.

### Die Spalte „unabhängig“ liegt bei Stufe 5 planmäßig höher

In Stufe 5 weist die Spalte **unabhängig** 905 Testfälle aus, die Spalte
**Bericht** dagegen 895 — und 895 ist der Wert, der mit **erwartet**
übereinstimmt. Diese Abweichung ist erklärt und beabsichtigt; sie ist **kein
Zählfehler** und **kein Befund über das Prüfobjekt**.

Ursache ist ein Fehler der Berichtsbibliothek Serenity 5.3.10, nicht der ISiK-Suite
und nicht IRIS for Health: enthält ein Lauf zwei Feature-Dateien mit demselben
Dateinamen, ordnet Serenity einem Szenario die `Examples:`-Tabelle des jeweils
anderen Szenarios zu. Stufe 5 der Fassung 2.7.0 enthält **16** solcher
Dateinamen unter 124 Feature-Dateien — `Practitioner-Search.feature` liegt sowohl
unter `Basis/` als auch unter `Medikation/` —, Stufe 3 enthält keinen einzigen.
Deshalb ist ausschließlich Stufe 5 betroffen.

Die Auswertung dieses Projekts ordnet daher jeden ausgeführten Schritt derjenigen
`Examples:`-Zeile zu, die der Schritt in seiner eigenen Bezeichnung nennt, und
nicht der Zeile an gleicher Position. Serenitys eigene Zusammenfassung im
Diagnosearchiv gibt den Fehler mit, weshalb sie an dieser Stelle keine
Gegenprobe leisten kann; geprüft wird stattdessen die Gleichung
*Bericht + verworfene fremde Schritte = unabhängig*. Geht sie auf, verzeichnet
Abschnitt 7 dies als `totalsReconciled` und benennt die Differenz. Geht sie nicht
auf, bleibt es beim schwerwiegenden Befund `totalsDisagree`.

Dass diese Korrektur nicht bloß eine Zählfrage ist, zeigt der gravierendere Teil
des Befundes: **ein tatsächlich fehlgeschlagener Testfall wurde als bestanden
ausgewiesen** (`Stufe5/Medikation/Practitioner-Search.feature:40`, „Search for
Practitioner resource by ID“). Der Ergebnisdatensatz und sein Schritt tragen beide
`FAILURE`; die fremde Tabelle, die Serenity dem Szenario zugeordnet hatte, meldet
in ihrer ersten Zeile jedoch `SUCCESS`. Ohne diese Korrektur wäre ein echter
ISiK-Konformitätsmangel im Ergebnisdokument nicht sichtbar.

Zwei Punkte sind für die Prüfung wesentlich:

* **Das offizielle Archiv wird nicht verändert.** `test-report.zip`, die Grundlage
  des *Bestätigungsverfahrens*, bleibt Byte für Byte unangetastet. Die Korrektur
  findet ausschließlich in der abgeleiteten Auswertung statt.
* **Jede Korrektur ist einzeln belegt.** Abschnitt 7 führt jeden betroffenen
  Ergebnisdatensatz mit seinem Pfad im Archiv auf (`foreignExampleTable`,
  `foreignExampleStep`), sodass jede einzelne Zuordnung von Hand am Archiv
  nachgeprüft werden kann.

### Zwei Lesefehler, die das Dokument ausdrücklich ausschließt

* **Kein Testfall ist nicht dasselbe wie kein Fehlschlag.** Ein Bericht ohne
  Testfälle ist ein fehlender Nachweis und kein sauberes Ergebnis.
* **Ein fehlerfreies Ergebnis ist hier das unerwartete Ergebnis.** IRIS for Health
  ist nachweislich noch nicht vollständig ISiK-konform. Sind keine Fehlschläge
  verzeichnet, ist die wahrscheinlichere Erklärung, dass der Lauf nicht geprüft hat,
  was er prüfen sollte; das Dokument verweist in diesem Fall zuerst auf die
  Umfangsabstimmung und die Auffälligkeiten.

### Auffälligkeiten

Abschnitt 7 verzeichnet Eigenschaften des Laufs oder des Archivs — **keine
Konformitätsbefunde**. Eine Teilmenge von ihnen bedeutet, dass der Bericht den
vollen Zertifizierungsumfang nicht abdeckt; das Dokument stellt dies dann in einem
hervorgehobenen Absatz fest. Am schwersten wiegt der Befund, dass ein Archiv nicht
mehr der Prüfsumme entspricht, die bei Abschluss des Laufs aufgezeichnet wurde: das
offizielle Archiv ist von dem Moment an unveränderlich, in dem es aus dem Container
kopiert wird, und ein abweichender Wert bedeutet, dass der Datensatz **nicht** als
Nachweis verwendbar ist.

### Der Lauf war ein Diagnoselauf, wenn das Dokument es sagt

Lief eine Stufe mit einem anderen als dem offiziellen Tag-Ausdruck, stellt Abschnitt
1 in einem hervorgehobenen Absatz fest, dass der Durchgang **kein
Konformitätsergebnis** ist und sein Bericht nicht zum *Bestätigungsverfahren*
eingereicht werden darf.

## Nachvollziehbarkeit: jede Aussage nennt ihre Quelle

Deutsche Prüfstellen arbeiten bevorzugt mit Dokumenten, die sie kennen, und stehen
automatisch erzeugten Berichten grundsätzlich zurückhaltend gegenüber. Das
Ergebnisdokument ist entsprechend gebaut: **jede Aussage nennt die Datei im
offiziellen Bericht, aus der sie stammt.**

Je Testfall werden zwei Zitatziele geführt, sofern beide existieren:

* `<stufe>/official/extracted/test-report/unresolvedReports/<name>.json` — **im
  eingereichten Archiv**, so dass eine prüfende Stelle, die nur die Einreichung
  besitzt, dem Zitat dennoch folgen kann;
* `<stufe>/official/extracted/debug-report/serenity/<name>.html` — derselbe
  Ergebnisdatensatz als lesbare Seite mit Anfrage- und Antwortinhalten.

Serenity vergibt weder lesbare Dateinamen noch verzeichnet es die Zeile eines
Szenarios in seiner Feature-Datei. Beides wird über drei Verknüpfungen
wiederhergestellt, so dass jedes Zitat die Form `Datei:Zeile` erhält und auf den
Anforderungstext zeigt, der geprüft wurde. Der Name des HTML-Gegenstücks wird dabei
**geprüft, nicht rekonstruiert**.

Bei einem fehlgeschlagenen Prüfschritt werden beide Ebenen festgehalten: der Schritt
des Szenarios, der die *Anforderung* benennt, und der tiefste fehlgeschlagene
Teilschritt, der die *Zusicherung* benennt.

### Die Zitate werden geprüft, nicht behauptet

Nach der Erzeugung des Ergebnisdokuments wird **jede** darin enthaltene Pfadangabe
auf Auflösbarkeit geprüft — beide offiziellen Archive je Stufe, beide Zitatziele je
Testfall, jeder Nachweisdatensatz — und die Zahl der geprüften sowie der fehlenden
Pfade gemeldet. Eine nicht auflösbare Zitatangabe ist genau der Mangel, den eine
prüfende Stelle zuerst findet.

### Prüfsummen über alles

`analysis/MANIFEST.txt` enthält SHA-256 über jede Datei des Datensatzes. Genau zwei
Dateien sind ausgenommen: das Verzeichnis selbst, das seinen eigenen Hash nicht
enthalten kann, und eine interne Hilfsdatei, deren Inhalt als Tabelle in Abschnitt 6
wiedergegeben wird. Das Paketverzeichnis ist **nicht** ausgenommen: es ist Nachweis.

```sh
cd reports/<run-id> && sha256sum -c analysis/MANIFEST.txt
cd reports/<run-id>/stufe5/official && sha256sum -c SHA256SUMS
```

### Abschnitt 6 weist auch die Änderungen am Paketsatz aus

Abschnitt 6 des Ergebnisdokuments führt nicht nur die geladenen FHIR-Pakete mit den
SHA-256 ihrer Archive, sondern nennt an derselben Stelle die einzige Änderung, die
dieses Projekt *innerhalb* des Paketsatzes vorgenommen hat: IRIS löst jede angegebene
Abhängigkeit über die exakte Paketkennung auf und bricht den Ladevorgang bei der
ersten nicht auflösbaren Angabe ab, weshalb vier Angaben umgeschrieben und ein Paket
zusätzlich geladen werden mussten.

Diese Angabe steht bewusst neben den Prüfsummen. Eine prüfende Stelle hat Anspruch
darauf zu erfahren, dass die entpackten `package.json`-Dateien von den
veröffentlichten abweichen — und ebenso darauf zu erfahren, dass die **Paketarchive
selbst unberührt** sind, jeder SHA-256 der Tabelle also weiterhin die
veröffentlichten Bytes nachweist. Jede Zeile nennt die verantwortliche Stelle, denn
eine Änderung an einem Paket in einem Konformitätsbericht lädt sonst zu dem Verdacht
ein, das Ergebnis sei hergestellt worden. **Keiner der Fälle ist ein
Konformitätsmangel des Prüfgegenstands.** Die Fälle und ihre Zuordnung sind in
[02-bestaetigungsverfahren.md](02-bestaetigungsverfahren.md#am-paketsatz-vom-ladevorgang-von-iris-erzwungen)
aufgeführt; der vollständige Nachweis mit Begründung je Änderung steht in
`provenance/package-plan.json`.

### Selbstprüfung

Abschnitt 8 des Ergebnisdokuments ist eine Arbeitsanweisung: Prüfsummen der Archive
prüfen, die Archive selbst neben unseren Auszug entpacken, einer beliebigen
Zitatangabe folgen, denselben Ergebnisdatensatz als Seite mit Inhalten lesen, den
gesamten offiziellen Bericht öffnen, das Dokument neu erzeugen und vergleichen.
Anschließend wird die Form angegeben, mit der eine zitierte Anfrage von Hand gegen
eine neu aufgebaute Umgebung wiederholt werden kann.

## Zugangsdaten

**Die Zugangsdaten dieser Umgebung werden absichtlich dokumentiert.**

Abschnitt 1 des Ergebnisdokuments enthält dazu die Tabelle *Access credentials*.
Sie führt, je gelaufener Stufe, folgende Werte auf und nennt als Quelle
`<stufe>/run.json`, Schlüssel `credentials`:

| Zeile im Bericht | Inhalt |
|---|---|
| FHIR API user | das Konto, mit dem sich die Testsuite am Prüfgegenstand anmeldet |
| FHIR API password | dessen Kennwort, im Klartext |
| Authentication scheme | `HTTP Basic` |
| Management Portal user | das Konto für die prüfende Person am IRIS-Verwaltungsportal |
| Management Portal password | dessen Kennwort, im Klartext |

### Begründung

Die Umgebung ist eine Laborinstanz, die für genau einen Testdurchgang aufgebaut und
nach Bestätigung der Ergebnisse zerstört wird. Der Zugang zu ihr ist physisch und
am Netzwerkperimeter geschützt, nicht durch diese Kennwörter. Innerhalb der Umgebung
sind sie allgemein bekannt und eine formale Technikalität — sie bleiben vorhanden,
damit der Prüfgegenstand eine wirklichkeitsnahe Nachbildung einer
Produktivumgebung bleibt, nicht als Mittel der Authentifizierung.

Der fachliche Zweck der Dokumentation ist die **Nachprüfbarkeit von Hand**: eine
prüfende Person muss ein Ergebnis aus Abschnitt 4 oder 5 selbst nachvollziehen
können, und die in der Lückenanalyse wörtlich zitierten Anfragen tragen dasselbe
Zugangsdatum in ihrem `Authorization`-Kopffeld. Ohne den dokumentierten Wert wäre
das zitierte HTTP-Protokoll nicht wiederholbar.

**Jede Bereitstellung erzeugt eigene Kennwörter.** Kein hier dokumentierter Wert
gewährt Zugang zu einer anderen Instanz — auch nicht zu einer neu aufgebauten.

### Wo die Werte erscheinen

Überall absichtlich:

| Ort | Inhalt |
|---|---|
| `analysis/results.md` Abschnitt 1 | die Tabelle *Access credentials*, in jedem archivierten Bericht |
| `<stufe>/run.json`, Schlüssel `credentials` | die Quelle, aus der diese Tabelle gebildet wird, mit einem Kommentarfeld zur Begründung |
| `GAP-ANALYSIS.md` | wörtliche `Authorization: Basic`-Kopfzeilen in den zitierten HTTP-Protokollen |
| `provenance/CREDENTIALS.md` | die Zugangsdatenübersicht der Instanz, wie bei der Bereitstellung erzeugt |
| `provenance/env.resolved.txt` | die vollständige aufgelöste Konfiguration des Durchgangs, unverändert kopiert |
| `provenance/tiger-RENDERED.txt` | jeder eingesetzte Wert, zur Nachvollziehbarkeit der Erzeugung |
| `debug-report.zip`, `config/tiger.yaml` | die Konfiguration, so wie die Suite sie erhalten hat |
| `make credentials`, `make status` | die Werte der laufenden Instanz |

Zwei Dateien des Datensatzes enthalten bewusst **kein** Kennwort:

* `provenance/iris-provenance.json`. Sie ist die Selbstbeschreibung der
  Installation durch den Prüfgegenstand und führt daher nur die *Kontonamen*; der
  Kanal für die Kennwörter sind die oben genannten Dateien.
* `CASE-EVIDENCE.md`, der Anhang der Lückenanalyse. Er gibt jeden Austausch
  wörtlich wieder, hält dabei aber den Wert der Kopfzeile `Authorization: Basic`
  zurück und verweist auf `provenance/CREDENTIALS.md`. Das verbirgt nichts, was
  nicht anderswo im Datensatz steht; der Grund ist, dass dieser Anhang das
  Dokument ist, das an die Produktentwicklung weitergegeben wird, und ein
  weitergegebenes Dokument keine Zugangsdaten in einsetzbarer Form führen soll.
  Die Wiederholungsbefehle benennen stattdessen zwei Umgebungsvariablen.

### Was weiterhin gilt

Drei Regeln bleiben in Kraft und sind maschinell geprüft:

1. **Nichts davon erreicht die Versionsverwaltung.** Die Verzeichnisse und Dateien,
   die Kennwörter tragen, sind ausgenommen, und `make check` prüft dies mechanisch
   gegen die Ausnahmeliste — nicht gegen eine Behauptung im Dokument.
2. **Die Dateien der laufenden Umgebung behalten produktionsnahe restriktive
   Rechte** (Modus 0600). Das ist keine Unterdrückung, sondern Teil der Nachbildung:
   der Prüfgegenstand soll sich verhalten wie eine Produktivinstanz. Der
   *Nachweiskopie* im Datensatz werden diese Rechte nicht auferlegt, denn sie ist
   Dokumentation.
3. **Kein leeres und kein fehlendes Kennwort.** Ein Konto ohne Kennwort wäre eine
   funktionale Abweichung von einer Produktivumgebung und keine Vereinfachung.

### Die Kennwortrichtlinie wird nicht abgeschwächt

Die erzeugten Kennwörter haben die Form `Kmrf-7xtq-Vhn3-Pd2s` — vier Gruppen aus vier
Zeichen, ohne die verwechselbaren Glyphen `O 0 I l 1`, rund 92 Bit. Sie erfüllen die
Voreinstellung der IRIS-Kennwortrichtlinie **konstruktionsbedingt**, so dass die
Richtlinie der Instanz auf dem Produktstandard bleibt. Es wird kein Schreibzugriff
auf die Sicherheitskonfiguration vorgenommen, um sie zu lockern.

Die Kennwörter aller Konten sind als nicht ablaufend konfiguriert und kein Konto ist
zur Kennwortänderung aufgefordert. Das ist eine bewusste Abweichung mit genau einem
Grund: träte eine der beiden Bedingungen mitten in einem Lauf ein, erschiene sie als
Welle abgewiesener Anfragen, nicht unterscheidbar von einer Autorisierungslücke des
Prüfgegenstands. Sie wäre nicht einmal am Statuscode als Authentifizierungsproblem
erkennbar: die Endpunkte weisen eine Anfrage ohne Zugangsdaten mit 404 statt mit 401
ab (gemessen: mit korrekten Zugangsdaten 200, ohne 404, mit falschen 404). Das offizielle IRIS-Image liefert seine vordefinierten Konten mit
abgelaufenen Kennwörtern aus, und die Verwaltungsportal-Anmeldung, auf die dieses
Projekt die prüfende Person verweist, wäre andernfalls nicht benutzbar.

Das API-Konto besitzt genau eine Rolle, die ausschließlich die Endpunktressource
trägt — bewusst **nicht** `%All`: das Konto, mit dem geprüft wird, darf den geprüften
Server nicht umkonfigurieren können.

Die vollständige und begründete Aufstellung dieser Festlegung als Abweichung findet
sich unter
[../en/08-known-limitations.md](../en/08-known-limitations.md#credential-documentation).

## Die Lückenanalyse

`GAP-ANALYSIS.md` ist **keine regulatorische Dokumentation**. Sie ist
englischsprachig, richtet sich an die Produktentwicklung von InterSystems und ist
auslegend: sie ordnet jeden Fehlschlag einer Ursache und einem Ort im Produkt zu.
Für das *Bestätigungsverfahren* ist sie ohne Bedeutung.

Sie liegt bewusst unter einem anderen Namen im Wurzelverzeichnis des Datensatzes und
nicht unter `analysis/`, damit Nachweis und Auslegung auf den ersten Blick
unterscheidbar bleiben.

Drei ihrer Eigenschaften sind auch für eine prüfende Stelle von Belang:

* **Sie ist eine reine Verknüpfung.** Sie liest genau zwei Dateien — das
  maschinenlesbare Ergebnisdokument dieses Durchgangs und den bei `make packages`
  gebauten Index. Kein Netz, kein Image, kein Prüfgegenstand. Sie ist damit Monate
  später aus einem archivierten Datensatz reproduzierbar, und zwei Läufe sind bei
  festgehaltenem Zeitstempel byte-identisch.
* **Fehlschläge, die auf die Testdaten dieses Projekts zurückgehen, sind
  ausgesondert.** Sie werden aus den **vor** dem Lauf geschriebenen Ladenachweisen
  belegt, in einen deutlich gekennzeichneten Anhang verschoben und aus der Zählung
  der Produktlücken herausgenommen, damit die Kennzahl nicht durch Mängel des
  Prüfstands erhöht wird. Umgekehrt gilt: ohne positiven Beleg aus den
  Ladenachweisen bleibt ein Fehlschlag dem Prüfgegenstand zugerechnet — ein
  Fehlschlag stillschweigend zu entschuldigen ist der eine Fehler, den dieser
  Bericht nicht machen darf.
* **Sie rechnet sich selbst ab.** Jeder fehlgeschlagene Fall des offiziellen
  Berichts muss genau einen eingeordneten Befund erzeugen. Geht diese Rechnung nicht
  auf, stellt der Bericht fest, dass er unvollständig ist und nicht als Arbeitsliste
  verwendet werden darf.

Ein Hinweis zur Lesart, der auch das Ergebnisdokument betrifft: Tigers
Zusicherungsschritte beziehen sich auf eine "aktuelle Antwort", die auf die
**Feature-Datei** und nicht auf das Szenario bezogen ist. Ein früher Fehlschlag in
einer Feature-Datei kann daher spätere Szenarien derselben Datei gegen eine Antwort
prüfen lassen, die sie nicht selbst erzeugt haben. Die Lückenanalyse kennzeichnet
solche Einträge und benennt ihr Vorgängerszenario; sie sind zuerst über dieses zu
diagnostizieren. Dies ist eine Eigenschaft des Werkzeugs, kein Mangel, und wird von
diesem Projekt nicht geändert.

### Der Anhang `CASE-EVIDENCE.md`

Die Lückenanalyse ist absichtlich zusammengefasst: sie führt einen Eintrag je
Ursache und nennt die betroffenen Szenarien, ohne sie zu zeigen. Damit ist sie als
Arbeitsliste brauchbar und zur Bearbeitung eines einzelnen Falles unzureichend —
sie enthält weder Anfragekörper noch Antwort noch einen Weg, einen Fall erneut
auszuführen. Der Anhang schließt diese Lücke: eine Klausel je fehlgeschlagenem
Fall statt je Ursache, mit der verwendeten Eingabe, der erwarteten und der
erhaltenen Ausgabe und einem Befehl zur Wiederholung.

Auch er ist eine reine Verknüpfung über den archivierten Datensatz: er liest das
maschinenlesbare Ergebnisdokument, die maschinenlesbare Lückenanalyse und die
beiden offiziellen Archive. Kein Netz, kein Prüfgegenstand. Die Archive werden
unmittelbar gelesen, sodass kein Auszug vorliegen muss und der Anhang auch auf
einem Rechner erzeugt werden kann, auf dem die Suite nie gelaufen ist; liegt ein
Auszug vor, wird dieser bevorzugt, weil die Zitate des Ergebnisdokuments ihn
benennen. Das Ergebnis ist in beiden Fällen byte-identisch.

Beide Archive müssen dabei vorhanden sein. Der Austausch ist ausschließlich in
`debug-report.zip` enthalten, das die schlanken Transportarchive dieses Projekts
bewusst nicht mitführen. Über einem solchen schlanken Datensatz erzeugt der
Generator ein vollständiges Dokument, in dem jede Klausel Eingabe, Erwartung und
den aufgezeichneten Fehler nennt und keine Klausel eine Anfrage oder eine Antwort
zitiert; Abschnitt 1 des Dokuments weist darauf hin, Abschnitt 5 trennt diese
Fälle von denjenigen mit tatsächlich leerem Verkehrsprotokoll. Gemessen am
schlanken Archiv des Durchgangs K am 2026-11-12: 41 Klauseln, 0 Austausche.

Für eine prüfende Stelle ist er ohne Bedeutung. Er wird hier nur deshalb
aufgeführt, weil er im Wurzelverzeichnis des Datensatzes liegt und in
`analysis/MANIFEST.txt` geführt wird.

## Erneutes Erzeugen der abgeleiteten Dokumente

```sh
make report RUN_ID=<run-id>          # Auszug, Ergebnisdokument, Zitatprüfung,
                                     # Prüfsummen, Lückenanalyse, Fallanhang
make gap-report RUN_ID=<run-id>      # nur die Lückenanalyse
make case-evidence RUN_ID=<run-id>   # nur deren Fallanhang
```

Alle Schritte sind reine Funktionen der Archive und der abgeleiteten Anforderungen
und lassen die Archive unberührt. Sie können auf einem anderen Rechner aus einem
archivierten Datensatz erneut ausgeführt und ihre Ausgabe zwischen Durchgängen
verglichen werden.

---

Der Prüfumfang und die Beschaffenheit der Umgebung sind in
[01-uebersicht.md](01-uebersicht.md) beschrieben, der Ablauf mit dem je Schritt
entstehenden Nachweis in
[02-bestaetigungsverfahren.md](02-bestaetigungsverfahren.md). Die vollständige
Aufstellung aller Abweichungen und Grenzen dieser Umgebung — auch derjenigen, die
hier nicht wiederholt sind — findet sich in
[../en/08-known-limitations.md](../en/08-known-limitations.md).
