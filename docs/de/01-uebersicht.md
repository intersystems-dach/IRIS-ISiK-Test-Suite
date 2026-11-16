# 1. Übersicht: Prüfgegenstand, Prüfwerkzeug, Prüfumgebung

## Zweck dieser Umgebung

Diese Umgebung dient der Durchführung der offiziellen ISiK-Konformitätstests der
Gematik gegen ein FHIR-Repository von InterSystems IRIS for Health, für die Stufen
**ISiK Stufe 3** und **ISiK Stufe 5**.

Sie erzeugt als Ergebnis je Stufe die Datei `test-report.zip` — das Artefakt, auf
dem das *Bestätigungsverfahren* beruht und das bei TITUS eingereicht wird — sowie
einen vollständigen Nachweisdatensatz, der belegt, unter welchen Bedingungen dieser
Bericht entstanden ist.

**Es ist zu erwarten, dass Prüfschritte fehlschlagen.** IRIS for Health ist
nachweislich noch nicht vollständig ISiK-konform. Die Umgebung ist entsprechend
darauf ausgelegt, Fehlschläge belastbar zu dokumentieren und sauber zuzuordnen, und
nicht darauf, ein bestandenes Ergebnis zu erzeugen.

## Prüfgegenstand (System under Test)

| Merkmal | Wert |
|---|---|
| Produkt | InterSystems IRIS for Health |
| Version | 2026.2 |
| Bezugsquelle | `containers.intersystems.com/intersystems/irishealth:2026.2` (offizielles, lizenzpflichtiges Container-Image) |
| HTTP-Frontend | InterSystems Web Gateway 2026.2, `containers.intersystems.com/intersystems/webgateway` (offizielles Image, Apache-Variante) — **Teil des Prüfgegenstands**, siehe unten |
| Speicherstrategie | `HS.FHIRServer.Storage.JsonAdvSQL.InteractionsStrategy` (Resource Repository) |
| Endpunkt ISiK Stufe 3 | `/isik3/fhir/r4`, Namespace `ISIK3` |
| Endpunkt ISiK Stufe 5 | `/isik5/fhir/r4`, Namespace `ISIK5` |
| Authentifizierung | HTTP Basic; für das Modul `Connect` der Stufe 5 zusätzlich OAuth 2.0 Bearer Token über TLS, siehe unten |

**Beide Images werden unverändert verwendet.** Es existiert kein `Dockerfile`, kein
abgeleitetes Image und keine gepatchte Klasse. Die FHIR-Installation erfolgt beim
Containerstart ausschließlich über dokumentierte IRIS-Programmierschnittstellen.
Damit ist der Prüfgegenstand ein unveränderter Produktstand, den jede Leserin und
jeder Leser eines Ergebnisses über dieselben Registry-Digests beziehen kann.

### Der Prüfgegenstand besteht aus zwei Containern

Diese Angabe ist für die Beurteilung eines Ergebnisses wesentlich und wird deshalb
hier ausdrücklich festgehalten: **die offiziellen IRIS-for-Health-Images enthalten
keinen Webserver.** Nicht einen unkonfigurierten, sondern gar keinen — im Image ist
keine `httpd`-, `apache2`- oder `nginx`-Binärdatei vorhanden, es lauscht kein Prozess
auf Port 52773, `iris.cpf` führt `WebServer=0`, und die bildeigene `parameters.isc`
vermerkt `csp_gateway.configure_apache: N`.

Der HTTP-Zugang zum Prüfgegenstand wird daher durch das ebenfalls offizielle Image
des InterSystems Web Gateway bereitgestellt. Das ist die von InterSystems
unterstützte Betriebsform für HTTP und entspricht der Topologie, die auch ein
produktives ISiK-Deployment verwenden würde. Der Prüfgegenstand bleibt damit ein
Produktstand wie ausgeliefert und nicht eine Behelfskonstruktion.

Folgerungen für die Nachweisführung:

* Der Prüfgegenstand ist durch **zwei** Image-Digests bestimmt. Beide werden
  aufgezeichnet und in jeden Nachweisdatensatz kopiert (siehe nächster Abschnitt);
  der Digest von IRIS allein beschreibt den geprüften Stand nicht vollständig.
* Die Version des Web Gateway wird bewusst an die IRIS-Version gebunden. Unterstützt
  wären auch andere Kombinationen; die Bindung schließt die Rückfrage aus, ob ein
  Versionsunterschied zwischen Gateway und Kernel ein Prüfergebnis erklärt.
* Jede Anfrage des Prüfwerkzeugs durchläuft das Gateway. Es ist damit Bestandteil
  des Prüfgegenstands und nicht Bestandteil der Prüfumgebung.

Die technischen Einzelheiten — insbesondere die vier Apache-Direktiven, die für die
Weiterleitung der FHIR-Pfade erforderlich sind — sind in
[docs/en/03-sut-iris.md](../en/03-sut-iris.md#the-http-surface-is-not-in-this-container)
dokumentiert.

### Warum zwei Endpunkte

ISiK Stufe 3 legt das Paket `de.basisprofil.r4` in Version **1.4.0** fest, ISiK
Stufe 5 in Version **1.5.4**. Ein FHIR-Endpunkt löst eine kanonische URL auf genau
eine Definition auf; ein einzelner Endpunkt kann daher nicht beide Stufen tragen.
Die beiden Stufen erhalten deshalb getrennte Namespaces und getrennte Endpunkte, in
die jeweils nur der eigene Paketsatz geladen wird.

Dies ist eine technisch erzwungene Konsequenz der Spezifikation, keine
Gestaltungsentscheidung dieses Projekts.

### Modul `Connect`: Bearer Token, TLS und Autorisierungsserver

Neun der zehn Module der Stufe 5 senden keine eigenen Zugangsdaten; die
Authentifizierung gegenüber dem Prüfgegenstand erfolgt für sie durch das
Prüfwerkzeug mittels HTTP Basic. Das zehnte Modul, `Connect`, authentifiziert
**je Anfrage aus der Testfalldatei selbst**, mit einem OAuth-2.0-Zugriffstoken, und
prüft, was der FHIR-Server mit dem vorgelegten Token tut. Dafür sind zwei Dinge
erforderlich, die kein anderes Modul benötigt.

**Erstens ein Autorisierungsserver — und dieser ist nicht Bestandteil des
Prüfgegenstands.** Die Testsuite stellt diese Voraussetzung selbst fest und zieht
die Grenze in einem Satz:

> the tests make the assumption that the System-Under-Test is up and running and
> that an Authorization Server is available. The tests do not evaluate the
> SMART-on-FHIR Authorization Flow, instead they require the Bearer Token used
> for sending authenticated requests to the System-Under-Test

— gematik/isik-testsuite, `README`, Abschnitt *Running Tests*.

Die ISiK-Spezifikation verlangt vom FHIR-Server, Token **zu konsumieren**, die an
anderer Stelle ausgegeben wurden; die Konformitätsaussage betrifft genau dieses
Konsumieren. Der Autorisierungsserver der Prüfumgebung ist daher Prüfmittel, so
wie der Container des Prüfwerkzeugs selbst: Keycloak 26.4.7, ein unabhängiges,
weit verbreitetes Open-Source-Produkt. Bewusst **kein** Produkt von InterSystems:
IRIS for Health kann selbst als Autorisierungsserver betrieben werden, doch ein
Prüfgegenstand, der die Token ausgibt, die er anschließend selbst validiert, würde
die Prüfung zirkulär machen. Ein Kundensystem bringt seinen eigenen
Identitätsanbieter mit.

**Zweitens TLS.** Der FHIR-Server weigert sich produktseitig, ein Bearer Token auf
einer Verbindung auszuwerten, die er nicht als gesichert ansieht
(`HS.FHIRServer.RestHandler`, `PrelimTokenCheck` über `%request.Secure`). Dieses
Verhalten ist korrekt — ein Bearer Token ist ein Inhabernachweis — und nicht
abschaltbar. Die Prüfumgebung stellt daher echtes TLS bereit und behauptet kein
Schema, das die Verbindung nicht hat: **in dieser Umgebung wird an keiner Stelle
ein `X-Forwarded-Proto` gesetzt.** Die Zertifikate werden von einer rundeneigenen
lokalen CA ausgegeben, da das isolierte Prüfnetz bewusst keinen öffentlich
auflösbaren Namen besitzt; Fingerabdrücke, Inhaber und Gültigkeit sämtlicher
Zertifikate werden in `provenance/tiger-TLS.txt` aufgezeichnet, private Schlüssel
nicht.

**Konfiguration des Prüfgegenstands.** Vier Schritte, sämtlich an Objekten, die
das Produkt für genau diesen Zweck bereitstellt, und sämtlich solche, die ein
Kunde bei der Anbindung seines FHIR-Servers an einen Identitätsanbieter ebenfalls
ausführt: eine ausgehende TLS-Konfiguration, eine durch Discovery ermittelte
Serverdefinition, eine Clientdefinition des Typs *Resource Server* und die
erwartete Token-Zielgruppe nebst den ausgewiesenen SMART-Fähigkeiten des
Endpunkts. **Es wird kein Code in den Prüfgegenstand geschrieben, kein
Erweiterungspunkt überschrieben und kein Verhalten des FHIR-Servers verändert.**
Tokenvalidierung, Scope-Auswertung und Filterung auf das Patientenkompartiment
leistet ausschließlich das Produkt, im Anfragepfad, im Lieferzustand — und genau
diese Eigenschaft macht die Messung zu einer Aussage über InterSystems IRIS for
Health und nicht über die Prüfumgebung.

**Authentifizierungsmodus des Endpunkts.** Eine Runde mit Autorisierungsserver
belässt den Endpunkt der Stufe 5 in dem Zustand, den das Installationsprogramm des
FHIR-Servers selbst herstellt (`AutheEnabled` 8288, keine Ressource an der
Webanwendung), statt ihn auf Passwortauthentifizierung einzuschränken. Dies ist
**keine Lockerung zugunsten eines Prüfergebnisses**, sondern der Zustand, den ein
Bearer Token voraussetzt: eine Anfrage, die ausschließlich ein Token trägt, legt
keine IRIS-Zugangsdaten vor und kann eine passwortgeschützte Anwendung nicht
passieren. Dass 8288 und eine leere Ressource die Wahl des Installationsprogramms
und nicht dieser Umgebung sind, wurde durch Messung festgestellt; unauthentifizierte
Zugriffe werden gleichwohl abgewiesen, bei zugeordnetem OAuth-Client durch den
FHIR-Server selbst mit 401. Die technische Darstellung einschließlich der Messung
steht in [../en/08-connect.md](../en/08-connect.md).

**Diese Konfiguration ist nicht voreingestellt.** `CONNECT_ENABLE=false` ist der
Vorgabewert; eine Runde ohne diesen Schalter stellt nichts davon bereit, und das
Modul `Connect` wird dann als wegen einer fehlenden Umgebungsvoraussetzung nicht
durchgeführt berichtet. Welche der beiden Konfigurationen eine Runde verwendet hat,
steht in deren eigenem Nachweisdatensatz.

### Nachweis der Identität des Prüfgegenstands

Drei voneinander unabhängige Angaben werden aufgezeichnet und in jeden
Nachweisdatensatz kopiert:

| Angabe | Ort im Nachweisdatensatz |
|---|---|
| Image-Tag | `provenance/env.resolved.txt`, `<variant>/run.json` |
| Registry-Digest (`sha256:…`) | ebenda — maßgeblich, denn ein Tag kann verschoben werden, ein Digest nicht |
| Registry-Digest des Web Gateway | ebenda, als `WEBGATEWAY_IMAGE_DIGEST` — mit demselben Gewicht aufgezeichnet, da das Gateway Bestandteil des Prüfgegenstands ist |
| Build-Kennung der Instanz (`$ZV`) | `provenance/iris-provenance.json`, von der Instanz selbst geschrieben |

`iris-provenance.json` wird vom Prüfgegenstand selbst erzeugt und enthält
ausschließlich zurückgelesene Werte — Namespaces, Endpunkte, die tatsächlich
geladenen FHIR-Pakete und die wirksamen Konfigurationswerte. Der Unterschied
zwischen der geplanten und der geladenen Konfiguration ist der Unterschied zwischen
Absicht und Nachweis; nur letzteres ist Beweis.

## Prüfwerkzeug

| Merkmal | Wert |
|---|---|
| Werkzeug | Offizielle ISiK-Testsuite der Gematik |
| Version | **2.7.0** (aktuellster Release, veröffentlicht am 06.07.2026, am 07.08.2026 unverändert aktuell) |
| Bezugsquelle | `gematik1/isik-testsuite:2.7.0`, vorkonfiguriertes Container-Image |
| Rahmenwerk | Gematik Tiger 4.3.1, Cucumber, Serenity BDD 5.3.10, JUnit 6.1.1, Java 17 |
| Referenzvalidator | `tiger-testsuite-validator` 1.1.9, `tiger.fhir` 1.2.15 |

Die Version des Prüfwerkzeugs ist **festgelegt**. Das ist keine Vorsicht, sondern
eine Anforderung an die Nachvollziehbarkeit: ein Release der Testsuite ändert den
Bestand an Testfällen, die Versionen der FHIR-Pakete, gegen die der
Referenzvalidator prüft, *und* die Gestalt der Platzhalter, die die hier
bereitgestellten Testdaten bedienen müssen. Alles drei ist Teil des Ergebnisses,
und das Letztgenannte scheitert **nicht sichtbar**: die Testdaten werden weiterhin
geladen, ein Prüffall wertet dann aber gegen Daten aus, die nicht der Erwartung
seiner Feature-Datei entsprechen — und das erscheint als Mangel des
Prüfgegenstands. Genau diese Fehlzuordnung soll dieses Projekt ausschließen.

**Prüfgegenstand und Prüfwerkzeug werden deshalb getrennt festgelegt.** Der
Prüfgegenstand wird standardmäßig auf dem neuesten Build der Linie 2026 geprüft
(`MODE=latest`) — das ist die Aussage, die ein Bestätigungsverfahren gewöhnlich
verlangt, und der geprüfte Build bleibt über Registry-Digest und Build-Kennung
eindeutig belegt. Das Prüfwerkzeug bleibt davon unberührt und wird nur durch eine
zweite, ausdrückliche Einstellung freigegeben
(`TESTSUITE_VERSION_TRACK=latest`), die eine erneute Prüfung der Testdaten nach
sich zieht. In beiden Fällen wird zusätzlich zum Tag der Registry-Digest
aufgezeichnet, ebenso, auf welchem der beiden Wege die jeweilige Version zustande
kam (`IRIS_VERSION_SOURCE`, `TESTSUITE_VERSION_SOURCE`) und welcher Release der
Testsuite zum Zeitpunkt des Laufs der neueste war
(`TESTSUITE_LATEST_AVAILABLE`) — ein veralteter Stand ist damit eine
aufgezeichnete Tatsache und keine spätere Recherche.

**Das Prüfwerkzeug wird unverändert verwendet.** Insbesondere wird **keine
Feature-Datei geändert.** Eine geänderte Testfalldatei würde den Testlauf
entwerten. Die für diese Umgebung erforderlichen Anpassungen — die Adresse des
Prüfgegenstands, die Basic-Authentifizierung, das Weiterleiten des Pfadanteils —
liegen ausschließlich in der Tiger-Konfiguration und sind dort einzeln als
`[DEVIATION]` gekennzeichnet.

Die Versionsangaben oben sind aus dem eingereichten Artefakt selbst überprüfbar:
Maven legt die `pom.xml` in `test-report.zip` ab.

## Prüfumgebung

Drei Container in einem eigenen, isolierten Docker-Netz:

| Komponente | Adresse | Rolle |
|---|---|---|
| `isik-iris` | 172.31.0.10 | Prüfgegenstand (Kern; lauscht ausschließlich auf dem Superserver-Port 1972, spricht selbst kein HTTP) |
| `isik-webgateway` | 172.31.0.30 | Prüfgegenstand (HTTP-Zugang, Port 80) — an diese Adresse richtet das Prüfwerkzeug jede Anfrage |
| `isik-testsuite-stufeN` | 172.31.0.20 | Prüfwerkzeug (genau eine Stufe je Lauf) |
| Netz | 172.31.0.0/24, Gateway 172.31.0.1, Bridge `br-isik` | eigenes Subnetz, feste Adressen |

Die ersten beiden Container bilden gemeinsam den Prüfgegenstand; der Grund ist im
Abschnitt [Der Prüfgegenstand besteht aus zwei
Containern](#der-prüfgegenstand-besteht-aus-zwei-containern) dargelegt.

Bei `CONNECT_ENABLE=true` tritt ein vierter Container hinzu:

| Komponente | Adresse | Rolle |
|---|---|---|
| `isik-authserver` | 172.31.0.40, Name `isik-as` | Autorisierungsserver (Keycloak) — **Prüfmittel, nicht Prüfgegenstand** |

Hinzu tritt außerdem eine zweite, TLS-tragende Zugangsadresse desselben
Web-Gateway-Containers. Es entsteht **keine zweite Instanz des Prüfgegenstands**:
dieselbe Webanwendung, dieselben Daten, ein zweiter Zugangsweg.

Die Adressen sind statisch vergeben. Das ist kein Selbstzweck: dieselbe
Zeichenkette erscheint in der Tiger-Konfiguration, im Protokoll der Testdatenladung,
in den im Fehlerbericht zitierten HTTP-Anfragen und in einer etwaigen Firewall-Regel
des Hosts. Eine prüfende Person muss keinen Containernamen auflösen, der nicht mehr
existiert.

Internetzugang besteht ausschließlich über NAT durch das Host-Gateway und wird nur
für das Beziehen der drei Images benötigt. Die FHIR-Pakete werden im
Standardbetrieb aus dem Image der Testsuite selbst entnommen; ein Testlauf ist damit
ohne Internetzugang durchführbar.

**Die Abschottung selbst wird nicht von dieser Umgebung durchgesetzt.** Sie stellt
eine Bridge-Schnittstelle und ein CIDR zur Adressierung bereit; das Setzen von
Firewall-Regeln ist eine Entscheidung auf Host-Ebene.

## Prüfumfang

### Testfallauswahl

| Stufe | Tag-Ausdruck |
|---|---|
| ISiK Stufe 3 | `@Stufe3 and (not @Optional)` |
| ISiK Stufe 5 | `@Stufe5 and (not @Optional)` |

Diese Ausdrücke sind die Voreinstellungen der Maven-Profile der Testsuite selbst
und **entsprechen dem offiziellen Zertifizierungsumfang**: alle verpflichtenden
Testfälle, keine optionalen.

Der zusätzliche Lauf eines Durchgangs mit `CONNECT_ENABLE=true` verwendet
`@Stufe5 and @Connect and (not @Optional)`. Das ist **eine Teilmenge** des
offiziellen Umfangs der Stufe 5 und kein anderer Umfang: der Ausdruck wählt das
Modul mit dem Tag der Suite selbst aus und behält deren `(not @Optional)` bei, so
dass kein Fall läuft, der im vollständigen Lauf nicht ebenfalls gelaufen wäre. Der
vollständige Lauf findet unverändert statt; `scopePartition` in `run.json`
unterscheidet die beiden (siehe [03-ergebnisse.md](03-ergebnisse.md)).

Jeder andere Ausdruck kennzeichnet den Lauf automatisch als Diagnoselauf
(`conformanceScope: false` in `run.json`), und das erzeugte Ergebnisdokument stellt
ausdrücklich fest, dass der Bericht **kein Konformitätsergebnis** ist und nicht zum
*Bestätigungsverfahren* eingereicht werden darf.

### Umfang in Zahlen

Gezählt auf Testsuite **2.7.0**, der letzten Version, die beide Stufen enthält.
Jede Zahl ist eine Eigenschaft der Testsuite-Version; siehe
[03-ergebnisse.md](03-ergebnisse.md) für die Zählung auf 3.0.9.

| | Stufe 3 | Stufe 5 | Summe |
|---|---:|---:|---:|
| Feature-Dateien | 96 | 124 | 220 |
| davon im Prüfumfang | 74 | 111 | 185 |
| **Ausgeführte Testfälle** | **506** | **895** | **1401** |
| davon prüfend | 432 | 783 | **1215** |
| davon Vorbedingungsblöcke | 74 | 112 | **186** |

**Zwei Bezugsgrößen, die nicht austauschbar sind.** Die 186
`@vorbedingung`-Blöcke sind echte Szenarien und werden ausgeführt, halten aber
lediglich die benötigten Testdaten als Prosa fest und prüfen den Prüfgegenstand
nicht. Die 186 Blöcke sind 13 % der ausgeführten Fälle und bestehen ausnahmslos;
eine auf 1401 bezogene Erfolgsquote kann daher unabhängig vom Verhalten des
Prüfgegenstands nicht unter 13 % fallen und ist damit **zu günstig** — ein zu
günstiger Fehler ist derjenige, nach dem eine prüfende Stelle zuerst sucht.
**Maßgebliche Bezugsgröße für eine Konformitätsaussage ist 1215.**

Ein `Scenario Outline` zählt **je Zeile des `Examples`-Blocks als ein Testfall**.
Das entspricht der Zählweise von Cucumber und Serenity
(`TestOutcome.getTestCount()`) und ist der Grund, weshalb die Summen über der Anzahl
der Szenario-Schlüsselwörter liegen.

Diese Zahlen sind aus den Feature-Dateien des festgelegten Images selbst abgeleitet
(`provenance/requirements.json`) und im Ergebnisdokument gegen drei voneinander
unabhängige Zählungen abgeglichen.

### Geprüfte Module

| Modul | Stufe 3 (ausgeführt / prüfend) | Stufe 5 (ausgeführt / prüfend) |
|---|---:|---:|
| Basismodul | 181 / 157 | 293 / 257 |
| Dokumentenaustausch | 41 / 37 | 31 / 28 |
| Medikation | 165 / 139 | 323 / 278 |
| Terminplanung | 69 / 59 | 144 / 129 |
| Vitalparameter | 47 / 38 | 104 / 91 |
| Shared | 3 / 2 | — |

Alle fünf ISiK-Module werden in beiden Stufen vollständig geprüft.

Die Generation 3.x der Testsuite gliedert die Stufe 5 feiner und führt neben den
oben genannten weitere Module, darunter `Connect` mit **32 Prüffällen**. Diese 32
Fälle sind Teil des offiziellen Umfangs der Stufe 5 und werden in jedem Durchgang
ausgeführt — `@Connect` trägt kein `@Optional`. Ohne Autorisierungsserver legen sie
dabei kein Zugriffstoken vor; welche Folge das hat und wie ein Durchgang sie
zusätzlich mit Token messen kann, steht in
[02-bestaetigungsverfahren.md](02-bestaetigungsverfahren.md) und
[03-ergebnisse.md](03-ergebnisse.md).

## Geladene FHIR-Pakete

**Die Auswahl der Pakete ist keine Entscheidung dieses Projekts.** Maßgeblich ist
die Konfiguration des Referenzvalidators im Image der Testsuite selbst
(`refv-plugins/*/config.yaml`). Aus ihr wird die Liste `name@version` abgeleitet und
in den Prüfgegenstand geladen.

Der Grund ist die Zuordenbarkeit von Fehlschlägen: würden Prüfgegenstand und
Validator unterschiedliche Paketversionen verwenden, meldete die Testsuite
Profilfehler, die auf einen Versionsunterschied und nicht auf eine Lücke im Produkt
zurückgehen — für ein Vorhaben, dessen Ergebnis eine Lückenanalyse ist, das
denkbar schlechteste Resultat.

Insgesamt sind 34 Pakete beteiligt. Die stufentragenden:

| Paket | Stufe |
|---|---|
| `de.gematik.isik-basismodul@3.1.1` | 3 |
| `de.gematik.isik-dokumentenaustausch@3.1.1` | 3 |
| `de.gematik.isik-medikation@3.1.1` | 3 |
| `de.gematik.isik-terminplanung@3.1.1` | 3 |
| `de.gematik.isik-vitalparameter@3.1.1` | 3 |
| `de.gematik.isik3-dokumentenaustausch-terminology@1.0.0` | 3 |
| `de.gematik.isik@5.1.2` | 5 |

Die übrigen bilden die Abhängigkeitshülle (`hl7.fhir.r4.core@4.0.1`,
`de.basisprofil.r4` in beiden Versionen, `hl7.terminology.r4`, `dvmd.kdl.r4*`,
`ihe.*`, `hl7.fhir.uv.*`, MII-Kerndatensatz). Vollständig aufgeführt mit SHA-256 je
Paketarchiv in `provenance/packages/MANIFEST.txt`, mit Zuordnung zum Endpunkt in
`provenance/package-plan.json`, und zurückgelesen aus der Instanz in
`provenance/iris-provenance.json`.

## Testdaten

**Die Testsuite legt keine Ressourcen an.** Jedes Szenario liest Ressourcen, die
unter genau den Kennungen bereits vorhanden sein müssen, die der eigene
Testdatenvertrag der Suite benennt (11 YAML-Dateien, 287 Schlüssel). Den *Inhalt*
dieser Ressourcen benennt die Suite ausschließlich als Prosa in den
`@vorbedingung`-Blöcken der Testfälle — 221 solcher Blöcke in 220 Feature-Dateien.

Die Erzeugung dieses Datenbestands ist damit Voraussetzung des gesamten Testlaufs,
und ihre Richtigkeit ist in einem Punkt entscheidend:

> Eine fehlende oder nicht konforme Testdatenressource erzeugt einen Fehlschlag,
> der **im offiziellen Bericht nicht von einer Konformitätslücke des
> Prüfgegenstands zu unterscheiden ist.**

Die Vermeidung dieser Fehlzuordnung ist der Grund für die Prüfungen, die dem
Testlauf vorgeschaltet sind: Abgleich gegen die aus den Feature-Dateien
extrahierten Zusicherungen, Prüfung der Referenzanforderungen der Suchszenarien,
Rücklesen jeder geschriebenen Ressource und `$validate` gegen den Validator des
Prüfgegenstands selbst. Deren Ergebnis liegt je Ressource in
`provenance/seed/<Stufe>.json` vor und wird **vor** dem Testlauf geschrieben — es ist
damit geeignet, einen Fehlschlag im Nachhinein diesem Projekt und nicht dem Produkt
zuzuordnen.

Der Testdatenvertrag der Suite selbst wird **byteweise** gegen die Dateien des
offiziellen Releases verglichen; jede Abweichung bricht den Lauf ab. Das Protokoll
liegt mit SHA-256 je Datei in `provenance/tiger-TESTDATA-PARITY.txt`.

Die Vergleichsgrundlage stammt **nicht** aus dem Image: das Image der Testsuite
enthält keinen Testdatenvertrag (`/app/testdata` existiert dort nicht; die Datei
wird zur Laufzeit vom Betreiber bereitgestellt, weshalb diese Umgebung sie selbst
einhängt). Die 11 Vertragsdateien werden daher aus dem Release-Tag des
Quellrepositoriums der Suite bezogen, festgelegt auf dieselbe Version wie das
Image. Die Herkunft ist im Kopf der Protokolldatei vermerkt.
Zulässig sind ausschließlich zwei zusätzliche Überlagerungsdateien, die
ausschließlich Schlüssel *ergänzen*, welche die Feature-Dateien der Suite
referenzieren, ihre eigenen Vertragsdateien aber nicht definieren
(siehe [02-bestaetigungsverfahren.md](02-bestaetigungsverfahren.md#festgestellte-mängel-des-prüfwerkzeugs)).

## Erzeugte Dokumente

| Dokument | Adressat | Status |
|---|---|---|
| `<variant>/official/test-report.zip` | Gematik / TITUS | **maßgeblich** — Artefakt des Bestätigungsverfahrens |
| `analysis/results.md` | prüfende Stelle | abgeleitet; jede Aussage zitiert die belegende Datei im offiziellen Bericht |
| `GAP-ANALYSIS.md` | Produktentwicklung InterSystems | abgeleitet und auslegend, englischsprachig; **keine regulatorische Dokumentation** |
| `CASE-EVIDENCE.md` | Produktentwicklung InterSystems | abgeleitet, englischsprachig; Anhang der Lückenanalyse mit einer Klausel je fehlgeschlagenem Fall; **keine regulatorische Dokumentation** |

Die Trennung ist beabsichtigt: die Auslegung liegt in einer anders benannten Datei
im Wurzelverzeichnis des Nachweisdatensatzes und nicht unter `analysis/`, damit
Nachweis und Auslegung auf den ersten Blick unterscheidbar bleiben.

Näheres zur Bewertung eines Ergebnisses in
[03-ergebnisse.md](03-ergebnisse.md), zum Ablauf in
[02-bestaetigungsverfahren.md](02-bestaetigungsverfahren.md).
