# 2. Durchgeführte Schritte, mit Nachweis je Schritt

Dieses Dokument hält fest, welche Schritte durchgeführt werden, um die
ISiK-Konformitätstests der Stufen 3 und 5 durchzuführen, in der Reihenfolge ihrer
Ausführung, und **welcher Nachweis je Schritt entsteht**. Es ist damit die
Beschreibung, gegen die eine prüfende Stelle den Nachweisdatensatz eines Testlaufs
lesen kann.

Jeder Schritt ist ein einzelnes Skript, das auch einzeln aufrufbar ist. Es gibt
keinen Schritt, der nur innerhalb eines größeren Ablaufs existiert, und keinen, der
eine Eingabe erfragt.

## Übersicht des Ablaufs

```
make preflight  →  resolve  →  packages  →  up  →  seed  →  test  →  report
                                                            │
                                                 test-stufe3, danach
                                                 test-stufe5 — nacheinander
```

`make all` führt die Kette vollständig aus. Den größten Anteil der Laufzeit haben
die beiden Testläufe; alle vorbereitenden Schritte sind dagegen kurz. Auf dem
Labor-Host gemessen (8 vCPU, 31 GiB): Inbetriebnahme beider Namespaces 4 min 40 s
bei leerer Instanz und 7 s, wenn die Namespaces bereits vorhanden sind, Einspielen
der Testdaten unter einer Minute. Die Messwerte sind keine Zusage: der Paketimport
ist I/O-gebunden, weshalb das Zeitlimit der Inbetriebnahme bewusst großzügig auf
90 Minuten gesetzt ist (Details in `docs/en/06-execution.md`).

**Die beiden Stufen laufen nie gleichzeitig.** Das Image der Testsuite führt je
Containerstart genau ein Maven-Profil aus und leert zu Beginn jedes Laufs sein
Berichtsverzeichnis. Die Berichte einer Stufe werden daher aus dem Container
herausgeholt, bevor die nächste Stufe starten darf.

## Kennung des Durchgangs

`RUN_ID` (Form `run-20260806T093000Z`, UTC) wird je `make`-Aufruf **einmal** gebildet
und an alle Schritte weitergegeben, damit beide Stufen eines Durchgangs in demselben
Verzeichnis `reports/<run-id>/` liegen. Die Kennung kann gesetzt werden, um die
abgeleiteten Dokumente eines abgeschlossenen Durchgangs erneut zu erzeugen:

```sh
make report RUN_ID=run-20260806T093000Z
```

---

## Schritt 1 — Prüfung der Voraussetzungen (`make preflight`)

Geprüft werden Docker Engine mit Compose v2, die benötigten Kommandozeilenwerkzeuge
einzeln benannt, Python 3 mit PyYAML, das Vorhandensein der IRIS-Lizenzdatei
`secrets/iris.key`, eine bestehende Anmeldung an `containers.intersystems.com`,
freier Plattenplatz (Warnung, kein Abbruch), die Verfügbarkeit der Host-Ports
52773, 51972, 9010, 9011 und dass das Netz `172.31.0.0/24` weder mit einem
bestehenden Docker-Netz noch mit einer Route des Hosts kollidiert.

**Nachweis:** die Ausgabe des Schritts. Ein Fehlschlag benennt die einzelne nicht
erfüllte Voraussetzung.

## Schritt 2 — Festlegung der Versionen (`make resolve`)

Die zu verwendenden Image-Tags werden festgelegt und ihre **Registry-Digests**
aufgelöst. Ferner werden die Zugangsdaten dieser Instanz bereitgestellt: je
Kennwort gilt die Reihenfolge — ein nicht leerer Wert in `.env`, sonst die
vorhandene Datei unter `secrets/`, sonst ein neu erzeugtes Kennwort.

**Nachweis:** `.env.resolved` (Modus 0600), im Nachweisdatensatz als
`provenance/env.resolved.txt` **unverändert** enthalten. Diese Datei ist die
vollständige aufgelöste Konfiguration des Durchgangs und enthält die erzeugten
Kennwörter im Klartext — absichtlich, siehe
[03-ergebnisse.md](03-ergebnisse.md#zugangsdaten).

## Schritt 3 — FHIR-Pakete und Ableitung der Anforderungen (`make packages`)

Drei Teilschritte:

1. **Pakete beschaffen.** Der Paketsatz wird aus der Konfiguration des
   Referenzvalidators im Image der Testsuite gelesen und bezogen. Zwei Quellen sind
   wählbar: `PACKAGE_SOURCE=refv` (Voreinstellung) entnimmt die Pakete dem Image der
   Testsuite selbst und benötigt keinen Internetzugang; `PACKAGE_SOURCE=simplifier`
   lädt sie aus der Paketregistrierung. In beiden Fällen bestimmt die Testsuite,
   *welche* Versionen geladen werden. Eine Ausnahme besteht bei `refv`: ein Paket,
   das der Validator nur zur Profilberechnung führt und für das er kein Archiv
   mitliefert, wird von einem geladenen Paket als Abhängigkeit angegeben und muss
   deshalb aus der Paketregistrierung bezogen werden. Der Kopf von `MANIFEST.txt`
   weist es aus; Begründung im Abschnitt
   [Am Paketsatz](#am-paketsatz-vom-ladevorgang-von-iris-erzwungen).
2. **Anforderungen aus den Feature-Dateien ableiten.** Aus den Feature-Dateien des
   festgelegten Images werden alle Szenarien, alle `@vorbedingung`-Blöcke und alle
   Zusicherungen extrahiert.
3. **Index der Lückenanalyse bauen.** Die Zuordnung Szenario → Validierungsmodul →
   lieferndes FHIR-Paket wird erstellt, solange Feature-Dateien und entpackte Pakete
   beide vorliegen. Die spätere Berichterstellung ist dadurch eine reine Verknüpfung
   ohne Netz- oder Image-Zugriff.

**Nachweis:**

| Datei | Inhalt |
|---|---|
| `provenance/packages/MANIFEST.txt` | jedes geladene Paket mit SHA-256 seines Archivs |
| `provenance/packages/PARITY.txt` | Vergleich der bezogenen Pakete gegen die von der Suite festgelegten Versionen |
| `provenance/package-plan.json` | welche Paketversion welchem Endpunkt zugeordnet ist, mit Begründung |
| `provenance/requirements.json` | jedes Szenario, jeder Vorbedingungsblock, jede Zusicherung, aus den Feature-Dateien der Suite |
| `docs/en/generated/REQUIREMENTS-Stufe3.md`, `-Stufe5.md` | die Anforderungstexte **wörtlich und unübersetzt**, je Modul, mit Zitat `Datei:Zeile` |
| `docs/en/generated/RESOURCE-INVENTORY.md` | die Arbeitsliste der zu erzeugenden Ressourcen |

Die drei erzeugten Dokumente sind nicht in der Versionsverwaltung: sie sind eine
Funktion des festgelegten Images und werden bei jedem Durchgang neu abgeleitet, damit
sie nicht gegenüber dem Prüfwerkzeug veralten können.

## Schritt 4 — Konfiguration erzeugen und Prüfgegenstand starten (`make up`)

**Konfiguration erzeugen.** Aus zwei kommentierten Vorlagen werden die
Tiger-Konfigurationsdateien beider Stufen erzeugt, die Testdatenvertragsdateien der
Suite bereitgestellt und die Zugangsdatenübersicht der Instanz geschrieben. Die
Vorlagen sind aus den von der Suite gelieferten Originaldateien abgeleitet, und
**jede Abweichung vom Original ist mit `[DEVIATION]` gekennzeichnet**; die Liste der
Abweichungen ist unten aufgeführt.

**Prüfgegenstand starten.** Der IRIS-Container wird gestartet und richtet beim
Start über dokumentierte Programmierschnittstellen ein: die beiden Namespaces, die
beiden FHIR-Endpunkte, den Import der FHIR-Pakete je Endpunkt, die
Sicherheitskonfiguration (Ressource, Rolle, API-Konto, vordefinierte Konten) und die
Konfiguration der Endpunkte. Anschließend werden die Endpunkte vom Host aus
verifiziert.

**Nachweis:**

| Datei | Inhalt |
|---|---|
| `provenance/iris-bootstrap.log` | das Installationsprotokoll des Prüfgegenstands, wörtlich |
| `provenance/iris-provenance.json` | die Selbstbeschreibung der Instanz: `$ZV`, Speicherstrategie, Kontonamen, je Stufe Namespace, Endpunkt, **zurückgelesene** Paketliste und die fünf zurückgelesenen Konfigurationswerte |
| `provenance/tiger-RENDERED.txt` | je erzeugte Datei ihr SHA-256 und die eingesetzten Werte |
| `provenance/tiger-TESTDATA-PARITY.txt` | zeilenweiser Vergleich der bereitgestellten Vertragsdateien gegen die der Suite |
| `provenance/CREDENTIALS.md` | die Zugangsdaten dieses Durchgangs, wie bei der Bereitstellung erzeugt |

## Schritt 5 — Testdaten laden (`make seed`)

Fünf Teilschritte, in dieser Reihenfolge:

1. Die verfasste Testdatensammlung wird gerendert — die Kennungen und Profilangaben
   werden aus dem Testdatenvertrag und dem Paketsatz eingesetzt.
2. Die gerenderte Sammlung wird gegen die aus den Feature-Dateien extrahierten
   Zusicherungen geprüft.
3. Die Referenzanforderungen der Suchszenarien werden gegen die Sammlung geprüft.
4. Die Sammlung wird in beide Endpunkte geschrieben (`PUT` mit fester Kennung, also
   wiederholbar), jede Ressource anschließend **zurückgelesen** und mit `$validate`
   gegen den Validator des Prüfgegenstands geprüft.
5. Das Ergebnis wird je Ressource aufgezeichnet.

Die Schritte 2 und 3 finden **vor** dem Start eines Containers statt; Schritt 4
schreibt seinen Nachweis **vor** dem Testlauf. Das ist der Zweck: ein späterer
Fehlschlag ist damit nachträglich entweder den Testdaten oder dem Prüfgegenstand
zuzuordnen. Ein Testdatenmangel bricht diesen Schritt ab — anders als ein
fehlgeschlagener Prüfschritt, der das erwartete Ergebnis ist.

**Nachweis:**

| Datei | Inhalt |
|---|---|
| `provenance/seed/SEED.txt` | lesbare Zusammenfassung dessen, was in beide Endpunkte geladen wurde |
| `provenance/seed/Stufe3.json`, `Stufe5.json` | je Ressource: Status des `PUT`, Rücklesen, `$validate`-Befunde |
| `provenance/seed/assertions-Stufe3.json`, `-Stufe5.json` | die gerenderte Sammlung gegen die Zusicherungen der Feature-Dateien |
| `provenance/seed/search-references.json` | die Referenzanforderungen der Suchszenarien gegen die Sammlung |

## Schritt 6 — Testläufe (`make test`)

Je Stufe ein Containerstart. **Vor** dem Start jeder Stufe wird geprüft: beide
erzeugten Konfigurationsdateien und die Testdaten liegen vor; der IRIS-Container
läuft; der Ladenachweis der Stufe liegt vor und weist **null** unvollständige
Ressourcen aus; der Tag-Ausdruck benennt die Stufe, die gerade läuft; es existiert
kein stehengebliebener Container der Testsuite. Jede dieser Bedingungen ist
abbrechend, weil ihr Fehlschlag sonst nicht als Fehler, sondern als *plausibel
aussehender falscher Bericht* auftritt.

Während des Laufs wird die vollständige Ausgabe des Containers nach
`reports/<run-id>/<stufe>/run.log` geschrieben. `TESTSUITE_TIMEOUT` (Voreinstellung
10800 s = 3 h) ist die Obergrenze je Stufe.

Auf dem Labor-Host gemessen (8 vCPU, 31 GiB): Stufe 3 **1250 s** (20 min 50 s, 506
Prüffälle), Stufe 5 **2050 s** (34 min 10 s, 895 Prüffälle). Beide Werte stehen als
`durationSeconds` in `run.json` des jeweiligen Durchgangs und sind damit für jeden
weiteren Durchgang unmittelbar vergleichbar. Die Messung für Stufe 5 gilt **nur mit
`TESTSUITE_HEAP_MAX=16g`**; ohne diese Einstellung erreicht die Stufe das Zeitlimit,
ohne einen Bericht zu erzeugen (Begründung und Messwerte in
`docs/en/06-execution.md`).

Nach dem Lauf werden die Berichtsarchive **unverändert und unverzüglich** aus dem
Container geholt — unverzüglich, weil der nächste Containerstart das
Berichtsverzeichnis leert. Jedes Archiv wird mit `unzip -t` auf Vollständigkeit
geprüft: ein unvollständiges Archiv ist schlechter als ein fehlendes, weil es wie ein
Nachweis aussieht und keiner ist.

**Abbrechend ist genau eine Bedingung: fehlt `test-report.zip`, ist der Lauf ein
Fehler des Prüfstands.** Selbst ein Lauf, in dem jeder Prüfschritt fehlschlägt,
erzeugt diese Datei. Fehlt hingegen nur `debug-report.zip`, ist das eine Warnung:
das Artefakt des Bestätigungsverfahrens liegt vor, aber die Lückenanalyse verliert
die Anfrage- und Antwortinhalte.

**Nachweis:**

| Datei | Inhalt |
|---|---|
| `<stufe>/official/test-report.zip` | das offizielle Berichtsarchiv, unverändert — **maßgeblich** |
| `<stufe>/official/debug-report.zip` | das Diagnosearchiv der Suite, unverändert |
| `<stufe>/official/SHA256SUMS` | SHA-256 über die Archive, wie empfangen |
| `<stufe>/run.log` | die vollständige Ausgabe des Containers |
| `<stufe>/run.json` | was gelaufen ist: Kennung, Profil, Tag-Ausdruck, ob offizieller Umfang, Zeiten, Prüfgegenstand mit Digest und `$ZV`, Prüfwerkzeug mit Digest, Bereitstellungsentscheidungen, **Zugangsdaten**, Zusammenfassung des Ladenachweises, Archive mit SHA-256 |

`run.json` wird auch dann geschrieben, wenn der Lauf kein Archiv erzeugt hat: ein
Lauf ohne Ergebnis ist selbst eine Tatsache, über die der Durchgang Rechenschaft
ablegen muss.

## Schritt 7 — Nachweisdatensatz und Bewertung (`make report`)

Die Archive werden **zusätzlich zu ihrer unveränderten Form** ausgepackt (Original
und Auszug liegen nebeneinander), der Nachweis aus Schritten 2 bis 5 in den
Datensatz kopiert, das zitierende Ergebnisdokument erzeugt, jede darin enthaltene
Pfadangabe auf Auflösbarkeit **geprüft**, ein Prüfsummenverzeichnis über den ganzen
Datensatz geschrieben und anschließend die Lückenanalyse für die
Produktentwicklung erzeugt.

**Nachweis:** der Datensatz selbst. Aufbau und Lesart in
[03-ergebnisse.md](03-ergebnisse.md).

Dieser Schritt ist eine reine Funktion der Archive und der abgeleiteten
Anforderungen. Er kann auf einem anderen Rechner aus einem archivierten Datensatz
erneut ausgeführt und seine Ausgabe zwischen Durchgängen verglichen werden.

---

## Festgestellte Mängel des Prüfwerkzeugs

Bei der Ableitung der Testdatenanforderungen aus den Feature-Dateien der Version
2.7.0 wurden Mängel **im Prüfwerkzeug** festgestellt. Sie sind hier aufgeführt, weil
sie andernfalls als Konformitätslücken des Prüfgegenstands erschienen wären. Jeder
ist an genau einer Stelle maschinell behandelt, jede Behandlung ist an ihrer Stelle
mit Datei und Zeile belegt, und **keiner wird als Lücke des Prüfgegenstands
gezählt**.

**Keine Feature-Datei wird geändert.** Alle Behandlungen liegen außerhalb der
Testfälle.

### 1. Drei überzählige Regex-Anker

Drei Schritte setzen ein `$` **nach** der schließenden Klammer des Platzhalters, und
zwar in der Form der Zusicherung, die den Wert als *Zeichenkette* vergleicht:

| Feature-Datei | Zeile |
|---|---|
| `Stufe3/Medikation/MedicationRequest-Search.feature` | 71 |
| `Stufe3/Medikation/MedicationStatement-Search.feature` | 83 |
| `Stufe5/Medikation/MedicationStatement-Search.feature` | 57 |

In der FHIRPath-Form gehört das `$` dorthin — es verankert einen Ausdruck in
`matches()`. In dieser Form ist es ein Zeichen der erwarteten Kennung, das keine
Ressource tragen kann. Die Autoren der Suite hatten denselben Tippfehler bereits
selbst bemerkt: `Stufe3/Basis/Encounter-Read-In-Progress.feature:44` enthält ihn
**auskommentiert**, ersetzt in Zeile 45 durch denselben Schritt ohne `$`.

*Behandlung:* Der Anker wird bei der Prüfung der Referenzanforderungen entfernt, so
dass die zugrunde liegende Datenanforderung dennoch geprüft wird — die Referenz muss
tatsächlich vorhanden sein, und sie ist es in allen drei Fällen —, und die Tatsache
wird am Eintrag festgehalten.

### 2. Vier fehlerhaft geschriebene `@Precondition:`-Tags

`Stufe5/Basis/Organization-Read.feature`, `Location-Read.feature`,
`Location-Room-Read.feature` und `Location-Bed-Placement-Read.feature` schreiben das
Tag mit Doppelpunkt. Für Cucumber ist das ein *anderes* Tag. Zur Laufzeit ändert es
nichts, weil kein Tag-Ausdruck `@Precondition` erwähnt und die Blöcke ohnehin über
das Tag der Feature-Datei ausgeführt werden, ohne etwas zuzusichern.

Es ändert die *Ableitung*: wörtlich gelesen wären diese vier Blöcke als **prüfende
Testfälle** gezählt worden, und ihre Prosa — die die Feldspezifikation für vier
Testdatenschlüssel ist — wäre in den Anforderungstabellen fehlgeschlagen.

*Behandlung:* Der Parser normalisiert einen angehängten Doppelpunkt und vergleicht
ohne Beachtung der Groß-/Kleinschreibung. Die vier Dateien sind in
`RESOURCE-INVENTORY.md` unter *Malformed precondition tags* aufgeführt.

### 3. Drei nicht definierte Testdatenschlüssel

Ein nicht definierter Schlüssel wird als **die Zeichenkette selbst** in den Schritt
eingesetzt. In einer Fehlermeldung ist das rein kosmetisch; in einer Anfrage-URL
erzeugt es eine Suche nach dieser Zeichenkette, ein leeres Bundle und einen
Fehlschlag, dessen Ursache ein Mangel der Suite ist, dessen Erscheinungsbild aber
eine fehlende Ressource im Prüfgegenstand ist.

| Schlüssel | Stufe / Umfang | Wirkung, wenn nicht definiert |
|---|---|---|
| `practitioner-id` | Stufe 3, im Prüfumfang | rein kosmetisch — nur der begleitende Meldungstext verwendet diese Schreibweise, der FHIRPath verwendet den korrekten Schlüssel |
| `Location-read-active-id` | Stufe 5, `@Optional`, **nicht** im Standardumfang | rein kosmetisch — drei Verwendungen, alle in einem Meldungstext |
| `RiskAssessment-read-id` | Stufe 5, im Prüfumfang | **ändert ein Ergebnis** — verwendet in der Anfrage-URL `/RiskAssessment/?_id=…`; nicht definiert sucht sie nach der Zeichenkette, erhält ein leeres Bundle und lässt einen verpflichtenden Prüfschritt fehlschlagen |

*Behandlung:* Die drei Schlüssel werden in je einer Überlagerungsdatei
`testdata/Stufe{3,5}/zz-upstream-key-defects.yaml` als **Alias auf den Wert eines
bereits vorhandenen Schlüssels der Suite** definiert. Diese Dateien werden unter
demselben Basisschlüssel **zuletzt** geladen, können also ausschließlich ergänzen und
niemals einen Wert der Suite überschreiben. **Es wird keine Ressource erfunden**:
die verwiesenen Instanzen sind diejenigen, die die Schlüssel der Suite ohnehin
verlangen.

Beim dritten Schlüssel unterscheidet sich der Name vom Schlüssel der Suite nur in
der Groß-/Kleinschreibung; Tigers Konfigurationsschlüssel sind
groß-/kleinschreibungsempfindlich, die beiden Namen lösen also nicht aufeinander
auf. Der Wert ist bewusst identisch gehalten: beide Namen müssen dieselbe geladene
Instanz bezeichnen.

Zwei weitere nicht definierte Schlüssel werden **bewusst nicht** mit einem Alias
versehen und sind in der Überlagerungsdatei als solche vermerkt, damit eine
künftige Prüfung sie nicht "behebt": der eine wird zur Laufzeit von der Feature-Datei
selbst gesetzt und in der nächsten Zeile gelesen — korrektes Verhalten —, der andere
gehört zu einem unfertigen Testfall, der mit `@Disabled` und `@Optional` versehen ist,
dessen Vorbedingungsblock wörtlich "TODO" lautet und der nie ausgeführt wird.

### 4. Zwei schemainkonsistente kanonische URLs

Die Feature-Dateien fordern das Profil `https://hl7.org/fhir/StructureDefinition/Bundle`,
während die Pakete es als `http://…` deklarieren. Der Index der Lückenanalyse
vergleicht die kanonische URL ohne Beachtung des Schemas und hält die Tatsache fest,
statt jede Bundle-Zusicherung als nicht zuordenbar zu melden.

### Erneute Prüfung bei Versionswechsel

Alle vier Feststellungen beziehen sich auf Version 2.7.0 und sind mit Datei und
Zeile belegt. Bei einem Wechsel der Version der Testsuite sind sie erneut zu prüfen:
eine behobene Stelle macht die zugehörige Behandlung überflüssig, eine verschobene
Zeile macht ihren Beleg falsch.

---

## Abweichungen von einer Produktivumgebung

Vollständig aufgeführt und begründet in
[../en/08-known-limitations.md](../en/08-known-limitations.md). Für die Bewertung
eines Ergebnisses sind die folgenden maßgeblich.

### Am Prüfwerkzeug

Die Testfälle selbst sind unverändert. Die Anpassungen liegen ausschließlich in der
Tiger-Konfiguration und sind dort einzeln als `[DEVIATION]` gekennzeichnet:

| Abweichung | Grund |
|---|---|
| Adresse des Prüfgegenstands eingesetzt | die Vorlage der Suite enthält an dieser Stelle einen Platzhalter |
| `healthcheckUrl` mit Startzeitüberschreitung ergänzt | ohne Erreichbarkeitsprüfung wartet Tiger die Startzeit ab und beginnt dennoch; "IRIS nicht erreichbar" erschiene damit als 1401 fehlgeschlagene Prüfschritte |
| `tigerProxy.proxyRoutes` aktiviert | von der Suite selbst so vorgesehen, wenn die Adresse des Prüfgegenstands einen Pfadanteil enthält — hier `/isik3/fhir/r4` |
| Basic-Authentifizierung an der Route | der IRIS-Endpunkt verlangt Authentifizierung. An der *Route* und nicht in den Schritten, damit **die Feature-Dateien byte-identisch bleiben** |
| `workflowUiPort` auf den veröffentlichten Port festgelegt | die Suite setzt hier einen anderen Wert als ihre eigene Compose-Datei veröffentlicht |
| eine zusätzliche, zuletzt geladene Testdatendatei | die oben unter Mangel 3 beschriebenen Aliase |

Alles Übrige ist unverändert die Konfiguration der Suite.

### Am Prüfgegenstand

| Einstellung | Wert hier | Wirkung auf die Messung |
|---|---|---|
| `DefaultPreferHandling` | `lenient` | die von FHIR vorgesehene und von ISiK erwartete Voreinstellung. Sie **verdeckt jedoch nicht unterstützte Suchparameter**: eine Suche mit einem nicht indizierten Parameter liefert ein ungefiltertes und damit falsches Ergebnis statt eines Fehlers, und der Prüfschritt scheitert am *Inhalt* des Bundles |
| `DefaultSearchPageSize`, `MaxSearchPageSize`, `MaxSearchResults` | 100 / 500 / 10000 | gegenüber den Produktvoreinstellungen **erhöht, nie verringert**. Damit kann kein Prüfschritt bestehen, der bei der Voreinstellung aus einem Konformitätsgrund fehlgeschlagen wäre; ein abgeschnittenes Suchergebnis erschiene sonst als Lücke der Suchsemantik |
| `DebugMode` | 0 | Diagnosemodus würde die aufgezeichneten Antworten von denen einer Produktivumgebung abweichen lassen — und diese Antworten sind der Nachweis |
| Authentifizierung des Endpunkts | Kennwort statt der Installationsvoreinstellung "unauthentifiziert" | näher an einer echten Bereitstellung: eine Anfrage ohne Zugangsdaten wird abgewiesen und nicht stillschweigend anonym beantwortet. Abgewiesen wird sie allerdings mit **404 und nicht mit 401**, weil der CSP-Verteiler die Anfrage gar nicht weiterleitet, anstatt zur Authentifizierung aufzufordern. Gemessen: mit korrekten Zugangsdaten 200, ohne 404, mit falschen 404 (Einzelheiten in `docs/en/03-sut-iris.md`) |
| Kennwörter aller Konten | nicht ablaufend, keine Änderungsaufforderung | beide Bedingungen träten mitten im Lauf als Welle abgewiesener Anfragen auf, nicht unterscheidbar von einer Autorisierungslücke des Prüfgegenstands — und wegen des oben beschriebenen Verhaltens nicht einmal am Statuscode als Authentifizierungsproblem erkennbar. Das offizielle Image liefert seine vordefinierten Konten mit abgelaufenen Kennwörtern aus |
| Angaben zu Abhängigkeiten in den entpackten Paketen | vier Angaben umgeschrieben, ein Paket zusätzlich geladen | vom Ladevorgang von IRIS erzwungen, nicht gewählt — siehe den folgenden Abschnitt. **Keine dieser Änderungen ist ein Konformitätsmangel des Prüfgegenstands**, und keine berührt die Paketarchive selbst: jeder SHA-256 in `provenance/packages/MANIFEST.txt` weist weiterhin die veröffentlichten Bytes nach |

**Die Kennwortrichtlinie wird nicht abgeschwächt.** `PasswordPattern` bleibt auf der
Produktvoreinstellung; die erzeugten Kennwörter erfüllen sie konstruktionsbedingt.

### Am Paketsatz, vom Ladevorgang von IRIS erzwungen

`HS.FHIRMeta.Load.NpmLoader` löst jede in einem Paket angegebene Abhängigkeit über
die **exakte Paketkennung** auf. Es findet keine Auflösung von
Versionsbereichen statt, und eine einzige nicht auflösbare Angabe bricht den
gesamten Ladevorgang ab (`ERROR #5809`) — und zwar erst, nachdem der Namensraum
bereits angelegt wurde. Um den Paketsatz zu laden, den der Referenzvalidator
verwendet, waren daher die folgenden Änderungen erforderlich.

Verantwortlich ist in keinem der Fälle der Prüfgegenstand. Die Fälle unterscheiden
sich in der Zuordnung, weshalb sie einzeln geführt werden:

| Fall | Angabe | Verantwortlich |
|---|---|---|
| Versionsbereich (`wildcard`) | vier ISiK-Module der Stufe 3 geben `de.gematik.isik-basismodul: 3.1.x` an | **InterSystems IRIS.** Der Versionsbereich ist nach der FHIR-Paketspezifikation zulässig; die Pakete sind korrekt veröffentlicht. IRIS löst ihn nicht auf |
| nicht veröffentlichte Version | `hl7.fhir.uv.subscriptions-backport.r4#1.1.0` gibt `hl7.fhir.r4.core: 4.0.0` an | **der vorgelagerte Leitfaden.** Diese Version wurde nie veröffentlicht; R4 ist 4.0.1 |
| bewusst nicht geladenes Paket | `hl7.fhir.uv.sdc#3.0.0` gibt `hl7.fhir.r4.examples#4.0.1` an | **dieses Projekt.** Die Beispielsammlung wird bewusst nicht in den Prüfgegenstand geladen; die Kante wird entfernt und nicht umgelenkt |
| nur zur Profilberechnung geführtes Paket | `ihe.formatcode.fhir#1.2.0` gibt `hl7.terminology.r4: 5.3.0` an, das der Validator nur unter `snapshotDependencies` festlegt und für das er kein Archiv mitliefert | **die Konfiguration von ISiK.** Das Paket wird deshalb zusätzlich in den Prüfgegenstand geladen und, da im Image nicht enthalten, auch bei `PACKAGE_SOURCE=refv` aus der Paketregistrierung bezogen. Der Kopf von `MANIFEST.txt` weist diese Ausnahme aus |

Geändert werden ausschließlich die **entpackten** `package.json`-Dateien, die IRIS
beim Laden liest. Die Paketarchive bleiben unberührt. Jede einzelne Änderung ist mit
Begründung in `provenance/package-plan.json` unter `dependencyRewrites` bzw.
`snapshotDependenciesPromoted` festgehalten und wird in Abschnitt 6 des
Ergebnisdokuments wiedergegeben.

### An der Dokumentation der Zugangsdaten

Die Zugangsdaten dieser Umgebung werden **absichtlich** in den Berichten
dokumentiert. Begründung, Umfang und die weiterhin geltenden Regeln in
[03-ergebnisse.md](03-ergebnisse.md#zugangsdaten).

---

## Was ein Durchgang nicht leistet

* **Ein bestandenes Ergebnis ist hier das unerwartete Ergebnis.** Sind keine
  Fehlschläge verzeichnet, ist die wahrscheinlichere Erklärung, dass der Lauf nicht
  geprüft hat, was er prüfen sollte. Das Ergebnisdokument verweist die Leserin und
  den Leser in diesem Fall zuerst auf die Umfangsabstimmung und die Auffälligkeiten.
* **Kein Testfall ist nicht dasselbe wie kein Fehlschlag.** Ein Bericht ohne
  Testfälle ist ein fehlender Nachweis und kein sauberes Ergebnis.
* Der Umfang ist der der Version 2.7.0 der Testsuite. Ein anderer Release prüft
  anderes.
* Es wird ausschließlich geprüft, was die Testsuite prüft. Konformität in Bereichen,
  die sie nicht abdeckt, ist damit weder festgestellt noch widerlegt.
