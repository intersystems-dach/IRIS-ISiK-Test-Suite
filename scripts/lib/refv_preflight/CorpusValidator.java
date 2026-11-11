// =============================================================================
// CorpusValidator - validate the rendered seed corpus with the test suite's own
//                   FHIR reference validator, before anything is seeded
// =============================================================================
// Why this exists
// ---------------
// Every scenario in the ISiK test suite reads a resource that this rig seeded,
// and most of them hand the server's response to the gematik reference validator
// (de.gematik.refv) against an ISiK profile. A seeded body that does not satisfy
// that profile therefore produces a FAILED scenario whose report entry is
// indistinguishable from a conformance defect in the system under test. Round F
// of 2026-11-11 charged 23 such cases to the product before the corpus was
// audited by hand; this step is the mechanisation of that audit.
//
// The check the rig already had is not this check. 50-seed.sh stage 4 POSTs
// $validate to the SUT for every body, which asks the system under test whether
// it considers the body conformant. That is the wrong oracle for two reasons:
// it is the artefact under test, and - as round F showed - it accepted all of
// the bodies this validator rejects. The oracle has to be independent of the
// SUT, and the only fully faithful oracle is the validator that will judge the
// response later in the run: same library version, same validation module, same
// FHIR package set, because all three come out of the pinned suite image.
//
// What it does
// ------------
// Loads every refv plugin shipped in the suite image's /app/refv-plugins, reads
// each rendered body's meta.profile, and validates the body with every loaded
// module that declares support for one of those profiles. A module is
// initialised only if some body needs it (initialisation loads the module's FHIR
// packages and costs seconds and hundreds of megabytes each).
//
// A body is reported as failing if ANY supporting module reports an error, not
// only if all of them do: the suite picks the module per scenario, this step
// cannot know which, and the strict reading is the one that keeps a defect in
// our data out of the product's gap count.
//
// A body with no meta.profile is not therefore unchecked. Those are the
// resources whose scenarios pass an explicit profile URL to the validation step,
// so the profile is not recoverable from the body - but it is recoverable from
// the suite, and `--asserted` supplies it: a (ResourceType/id -> profiles) map
// extracted from the requirements corpus, which records for every fixture the
// profiles the in-scope scenarios assert against it. Such a body is validated
// against those profiles and the entry records `profileSource: scenario`.
//
// Only a body that no in-scope scenario asserts any profile against is left
// unvalidated, and it is then reported as `no-profile-asserted` rather than as a
// skip - the difference being that the first is a checked statement about the
// suite and the second was an assumption. Measured on the 3.0.9 corpus: 6 of 129
// bodies carry no meta.profile and the requirements corpus asserts no profile
// against any of them, so nothing was left unchecked by the earlier skip. Absent
// `--asserted` the step falls back to the old behaviour and says so.
//
// Usage (inside the pinned suite image)
// -------------------------------------
//   java -cp "$(cat /tmp/cp.txt)" CorpusValidator.java \
//        --plugins /app/refv-plugins --corpus /corpus --out /out/report.json \
//        [--asserted /out/asserted-profiles.json]
//
// Exit status: 0 if no body produced an error, 1 if any did, 2 on a usage or
// infrastructure failure (no plugins, unreadable corpus, unwritable report,
// unreadable asserted-profile map).
// A warning never fails the step - warnings in this validator are dominated by
// terminology servers the module deliberately does not resolve.
// =============================================================================

import ca.uhn.fhir.validation.ResultSeverityEnum;
import ca.uhn.fhir.validation.SingleValidationMessage;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import de.gematik.refv.Plugin;
import de.gematik.refv.ValidationModuleFactory;
import de.gematik.refv.commons.validation.ValidationModule;
import de.gematik.refv.commons.validation.ValidationOptions;
import de.gematik.refv.commons.validation.ValidationResult;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.regex.Pattern;
import java.util.stream.Stream;
import java.util.zip.ZipFile;

public class CorpusValidator {

    // An error that is not about the body.
    //
    // The ISiK packages define several extensions under canonicals of the form
    // `http://hl7.org/fhir/5.0/StructureDefinition/extension-Encounter.
    // plannedStartDate`, and the ISiK Encounter profiles slice
    // `Encounter.extension` on them. The validator does not resolve such a
    // canonical out of the package that defines it - it reads the
    // `hl7.org/fhir/<version>/` authority as a cross-version reference - and so
    // reports, per slice:
    //
    //   Slicing cannot be evaluated: Problem with use of resolve() - profile
    //   [CanonicalType[http://hl7.org/fhir/5.0/StructureDefinition/
    //   extension-Encounter.plannedStartDate]] on
    //   Encounter.extension:plannedStartDate could not be resolved (@char 1)
    //
    // It fires on any body that carries any extension at all and is validated
    // against such a profile, whatever that extension is: the body that first
    // produced it here carries one extension, `Aufnahmegrund`, and no planned
    // date at all. The message is the validator stating that IT could not
    // resolve a definition the PROFILE points at, so no content this project
    // could author would remove it, and reporting the body as invalid would
    // charge our own corpus with a defect it does not have.
    //
    // Such a message is therefore counted separately and reported in full under
    // `unresolvable` rather than `messages`, and does not make the body invalid.
    // The same root cause is recognised at run time by
    // gap_report.suite_conflict_verdict(), which classifies a scenario that fails
    // on it as UPSTREAM_SUITE_CONFLICT - on positive evidence from the index,
    // because there the question is whether to charge the product, and a message
    // is not evidence enough for that. Here the question is only whether to
    // charge our data, and a message that makes no claim about the body cannot.
    private static final Pattern UNRESOLVABLE_SLICE_PROFILE = Pattern.compile(
            "profile \\[CanonicalType\\[http://hl7\\.org/fhir/\\d+\\.\\d+/"
            + "StructureDefinition/extension-[A-Za-z0-9_.-]+\\]\\]"
            + "[^\\n]*could not be resolved");

    /** A loaded plugin: its module, plus whether initialize() has been paid for. */
    private static final class Mod {
        final String id;
        final ValidationModule module;
        boolean initialised;
        // The ZipFile must stay open: Plugin.createFromZipFile reads the
        // module's resources lazily through it during initialize().
        @SuppressWarnings("unused")
        final ZipFile keepOpen;

        Mod(String id, ValidationModule module, ZipFile keepOpen) {
            this.id = id;
            this.module = module;
            this.keepOpen = keepOpen;
        }
    }

    public static void main(String[] args) {
        Path pluginDir = null;
        Path corpus = null;
        Path out = null;
        Path asserted = null;
        for (int i = 0; i < args.length - 1; i += 2) {
            switch (args[i]) {
                case "--plugins" -> pluginDir = Path.of(args[i + 1]);
                case "--corpus" -> corpus = Path.of(args[i + 1]);
                case "--out" -> out = Path.of(args[i + 1]);
                case "--asserted" -> asserted = Path.of(args[i + 1]);
                default -> usage("unknown argument: " + args[i]);
            }
        }
        if (pluginDir == null || corpus == null || out == null) {
            usage("--plugins, --corpus and --out are all required");
        }

        ObjectMapper mapper = new ObjectMapper();
        JsonNode assertedProfiles = loadAsserted(mapper, asserted);
        List<Mod> mods = loadPlugins(pluginDir);
        if (mods.isEmpty()) {
            fail("no refv plugin found in " + pluginDir);
        }
        System.out.printf("modules available: %s%n",
                mods.stream().map(m -> m.id).toList());

        List<Path> bodies = corpusFiles(corpus);
        if (bodies.isEmpty()) {
            fail("no .json body found under " + corpus);
        }

        ObjectNode report = mapper.createObjectNode();
        report.put("corpus", corpus.toString());
        report.put("plugins", pluginDir.toString());
        report.put("assertedProfileMap",
                asserted == null ? "(not supplied)" : asserted.toString());
        ArrayNode modIds = report.putArray("modulesAvailable");
        mods.forEach(m -> modIds.add(m.id));
        ArrayNode files = report.putArray("files");

        int errored = 0;
        int skipped = 0;
        int checked = 0;
        int fromScenario = 0;
        int unresolvableBodies = 0;

        for (Path body : bodies) {
            ObjectNode entry = files.addObject();
            String rel = corpus.relativize(body).toString();
            entry.put("file", rel);

            String text;
            try {
                text = Files.readString(body);
            } catch (IOException e) {
                entry.put("status", "unreadable");
                entry.put("detail", String.valueOf(e.getMessage()));
                errored++;
                continue;
            }

            List<String> profiles = new ArrayList<>();
            String rtype;
            String rid;
            try {
                JsonNode root = mapper.readTree(text);
                rtype = root.path("resourceType").asText("");
                rid = root.path("id").asText("");
                entry.put("resourceType", rtype);
                entry.put("id", rid);
                for (JsonNode p : root.path("meta").path("profile")) {
                    profiles.add(p.asText());
                }
            } catch (IOException e) {
                entry.put("status", "unparseable");
                entry.put("detail", String.valueOf(e.getMessage()));
                errored++;
                continue;
            }

            ArrayNode declared = entry.putArray("declaredProfiles");
            profiles.forEach(declared::add);
            String source = "meta.profile";

            if (profiles.isEmpty()) {
                // The body names no profile, so ask the suite which profiles its
                // scenarios assert against this exact fixture. Resolving it here
                // rather than skipping is the difference between a check and an
                // assumption: a fabricated fixture carries no meta.profile
                // precisely because the scenario supplies it, so a skip leaves
                // the fabricated bodies - the ones most likely to be wrong -
                // unvalidated.
                //
                // The key carries the level, because the corpus is rendered per
                // level and the same fixture id is asserted against the v3
                // profile under Stufe3 and the v5 profile under Stufe5.
                String level = rel.contains("/") ? rel.substring(0, rel.indexOf('/')) : "";
                for (JsonNode p : assertedProfiles.path(level + "/" + rtype + "/" + rid)) {
                    profiles.add(p.asText());
                }
                source = "scenario";
                ArrayNode fromReqs = entry.putArray("assertedProfiles");
                profiles.forEach(fromReqs::add);
            }

            entry.put("profileSource", source);

            if (profiles.isEmpty()) {
                entry.put("status", asserted == null ? "skipped" : "no-profile-asserted");
                entry.put("detail", asserted == null
                        ? "no meta.profile, and no asserted-profile map was supplied"
                        : "no meta.profile, and no in-scope scenario asserts a "
                          + "profile against this resource");
                skipped++;
                continue;
            }
            if (source.equals("scenario")) {
                fromScenario++;
            }

            List<Mod> supporting = new ArrayList<>();
            for (Mod m : mods) {
                if (supports(m, profiles)) {
                    supporting.add(m);
                }
            }
            if (supporting.isEmpty()) {
                // The corpus declares a profile no module in this image knows.
                // That is ours too: it means meta.profile was stamped from a
                // package the pinned suite does not validate against.
                entry.put("status", "unsupported-profile");
                entry.put("detail", "no loaded module declares support for any "
                        + ("meta.profile".equals(source) ? "declared" : "asserted")
                        + " profile");
                errored++;
                continue;
            }

            ArrayNode results = entry.putArray("results");
            boolean anyError = false;
            for (Mod m : supporting) {
                ObjectNode res = results.addObject();
                res.put("module", m.id);
                if (!initialise(m)) {
                    res.put("status", "module-initialisation-failed");
                    anyError = true;
                    continue;
                }
                ValidationResult vr;
                try {
                    ValidationOptions opts = ValidationOptions.getDefaults();
                    vr = m.module.validateString(text, opts);
                } catch (RuntimeException e) {
                    res.put("status", "validator-exception");
                    res.put("detail", e.getClass().getSimpleName() + ": " + e.getMessage());
                    anyError = true;
                    continue;
                }
                ArrayNode msgs = res.putArray("messages");
                ArrayNode unres = res.putArray("unresolvable");
                int errs = 0;
                int unresolvable = 0;
                for (SingleValidationMessage msg : vr.getValidationMessages()) {
                    ResultSeverityEnum sev = msg.getSeverity();
                    boolean isError = sev == ResultSeverityEnum.ERROR
                            || sev == ResultSeverityEnum.FATAL;
                    if (!isError) {
                        continue;
                    }
                    String detail = String.valueOf(msg.getMessage());
                    ObjectNode mo;
                    if (UNRESOLVABLE_SLICE_PROFILE.matcher(detail).find()) {
                        unresolvable++;
                        mo = unres.addObject();
                    } else {
                        errs++;
                        mo = msgs.addObject();
                    }
                    mo.put("severity", String.valueOf(sev));
                    mo.put("location", String.valueOf(msg.getLocationString()));
                    mo.put("message", detail);
                }
                res.put("errors", errs);
                res.put("unresolvableSliceProfiles", unresolvable);
                res.put("status", errs == 0
                        ? (unresolvable == 0 ? "valid" : "valid-with-unresolvable-slices")
                        : "invalid");
                if (errs > 0) {
                    anyError = true;
                }
            }
            int entryUnresolvable = 0;
            for (JsonNode res : entry.path("results")) {
                entryUnresolvable += res.path("unresolvableSliceProfiles").asInt(0);
            }
            entry.put("status", anyError ? "invalid"
                    : (entryUnresolvable == 0 ? "valid" : "valid-with-unresolvable-slices"));
            checked++;
            if (anyError) {
                errored++;
                System.out.printf("INVALID  %s%n", rel);
                for (JsonNode res : entry.path("results")) {
                    for (JsonNode mo : res.path("messages")) {
                        System.out.printf("    [%s] %s: %s%n", res.path("module").asText(),
                                mo.path("location").asText(), mo.path("message").asText());
                    }
                }
            }
            if (entryUnresolvable > 0) {
                unresolvableBodies++;
                // Printed whether or not the body is otherwise invalid, and
                // labelled as the validator's own limitation rather than as a
                // finding about this corpus. Silence here would be the wrong
                // trade: the count is the thing to watch if a future package set
                // resolves these canonicals, because then they become real
                // validation results again.
                System.out.printf("NOTE     %s: %d slice definition(s) the module "
                        + "could not resolve (not a statement about this body)%n",
                        rel, entryUnresolvable);
                for (JsonNode res : entry.path("results")) {
                    for (JsonNode mo : res.path("unresolvable")) {
                        System.out.printf("    [%s] %s: %s%n", res.path("module").asText(),
                                mo.path("location").asText(), mo.path("message").asText());
                    }
                }
            }
        }

        report.put("bodies", bodies.size());
        report.put("checked", checked);
        report.put("checkedAgainstScenarioProfile", fromScenario);
        report.put("skipped", skipped);
        report.put("invalid", errored);
        report.put("withUnresolvableSliceProfiles", unresolvableBodies);

        try {
            Path parent = out.toAbsolutePath().getParent();
            if (parent != null) {
                Files.createDirectories(parent);
            }
            Files.writeString(out, mapper.writerWithDefaultPrettyPrinter()
                    .writeValueAsString(report) + "\n");
        } catch (IOException e) {
            fail("cannot write " + out + ": " + e.getMessage());
        }

        System.out.printf("%nbodies %d   validated %d (%d against the profile the "
                + "scenario asserts)   unvalidated %d (no profile asserted)   invalid %d%n",
                bodies.size(), checked, fromScenario, skipped, errored);
        if (unresolvableBodies > 0) {
            System.out.printf("%d body/bodies carry an extension whose slice definition "
                    + "the module could not resolve; see the NOTE lines above and "
                    + "`unresolvable` in the report%n", unresolvableBodies);
        }
        System.out.printf("report: %s%n", out);
        System.exit(errored == 0 ? 0 : 1);
    }

    /**
     * `ResourceType/id` -> the profiles in-scope scenarios assert against it.
     *
     * An empty object when no map was supplied, so every lookup below answers
     * "nothing asserted" and the step behaves as it did before the map existed.
     * An unreadable or malformed map is an infrastructure failure rather than an
     * empty map: it would silently turn validation back into skipping.
     */
    private static JsonNode loadAsserted(ObjectMapper mapper, Path path) {
        if (path == null) {
            return mapper.createObjectNode();
        }
        try {
            JsonNode node = mapper.readTree(Files.readString(path));
            if (!node.isObject()) {
                fail(path + " is not a JSON object of ResourceType/id -> profiles");
            }
            System.out.printf("asserted-profile map: %d fixture(s) from %s%n",
                    node.size(), path);
            return node;
        } catch (IOException e) {
            fail("cannot read " + path + ": " + e.getMessage());
            return mapper.createObjectNode();
        }
    }

    /** Does this module declare a configuration for any of these profiles? */
    private static boolean supports(Mod m, List<String> profiles) {
        try {
            return m.module.getConfiguration()
                    .findFirstSupportedProfileWithExistingConfiguration(profiles) != null;
        } catch (RuntimeException e) {
            return false;
        }
    }

    private static boolean initialise(Mod m) {
        if (m.initialised) {
            return true;
        }
        try {
            System.out.printf("initialising module %s ...%n", m.id);
            m.module.initialize();
            m.initialised = true;
        } catch (Exception e) {
            System.out.printf("module %s failed to initialise: %s%n", m.id, e.getMessage());
            return false;
        }
        return true;
    }

    private static List<Mod> loadPlugins(Path dir) {
        List<Mod> mods = new ArrayList<>();
        ValidationModuleFactory factory = new ValidationModuleFactory();
        try (Stream<Path> s = Files.list(dir)) {
            List<Path> zips = s.filter(p -> p.getFileName().toString().endsWith(".zip"))
                    .sorted(Comparator.comparing(Path::toString)).toList();
            for (Path zip : zips) {
                try {
                    ZipFile zf = new ZipFile(zip.toFile());
                    Plugin plugin = Plugin.createFromZipFile(zf);
                    ValidationModule module = factory.createValidationModuleFromPlugin(plugin);
                    mods.add(new Mod(module.getId(), module, zf));
                } catch (Exception e) {
                    System.out.printf("cannot load plugin %s: %s%n", zip, e.getMessage());
                }
            }
        } catch (IOException e) {
            fail("cannot list " + dir + ": " + e.getMessage());
        }
        return mods;
    }

    private static List<Path> corpusFiles(Path root) {
        try (Stream<Path> s = Files.walk(root)) {
            return s.filter(Files::isRegularFile)
                    .filter(p -> p.getFileName().toString().endsWith(".json"))
                    .sorted(Comparator.comparing(Path::toString))
                    .toList();
        } catch (IOException e) {
            fail("cannot walk " + root + ": " + e.getMessage());
            return List.of();
        }
    }

    private static void usage(String why) {
        System.err.printf("CorpusValidator: %s%n", why);
        System.err.println("usage: CorpusValidator --plugins <dir> --corpus <dir> --out <file.json>");
        System.exit(2);
    }

    private static void fail(String why) {
        System.err.printf("CorpusValidator: %s%n", why);
        System.exit(2);
    }
}
