/// Builds the unit under test: two FHIR repository endpoints on one IRIS for
/// Health instance, one per ISiK specification level.
///
/// <p>Why two endpoints. ISiK Stufe 3 pins <code>de.basisprofil.r4#1.4.0</code>
/// while ISiK Stufe 5 pins <code>#1.5.4</code>. A single FHIR endpoint resolves
/// each canonical URL to exactly one definition, so the two levels cannot share
/// one. They are therefore installed into separate namespaces with separate
/// endpoints, each carrying only its own package set.</p>
///
/// <p>Why this runs at container start. The FHIR installation API operates on a
/// live instance, so it cannot be an image build step. Running it at start also
/// means one unmodified official image serves all three deploy modes.</p>
///
/// <p>Idempotency. Every step checks for its own result before acting, so a
/// re-run repairs a partial installation rather than failing or duplicating. The
/// shell wrapper additionally short-circuits a completed install via a marker
/// file, because a needless package re-import costs tens of minutes.</p>
///
/// <p>Configuration comes from the ISIK_* environment variables set in
/// docker-compose.yml and from the package plan JSON written by
/// <code>make packages</code>. Nothing is hardcoded, so the same class serves
/// every deployment.</p>
///
/// <p>All output goes to the current device, which bootstrap.sh tees into
/// run/iris-state/bootstrap.log - the installation record referenced by the
/// generated documentation.</p>
Class ISiK.Setup Extends %RegisteredObject
{

/// Web application resource guarding both FHIR endpoints. Created if absent.
///
/// <p>No leading <code>%</code>: that prefix is reserved for system-defined
/// security objects, and <code>Security.Roles.Create()</code> rejects a role name
/// beginning with it outright.</p>
Parameter FHIRRESOURCE = "ISiK_FHIR";

/// Role granted to the API user, holding the FHIR resource with Use permission.
Parameter FHIRROLE = "ISiKFHIRUser";

/// Storage strategy for the endpoints. The Resource Repository - the strategy
/// the IRIS documentation itself uses for a standalone FHIR server, and the one
/// under test here.
Parameter STRATEGY = "HS.FHIRServer.Storage.JsonAdvSQL.InteractionsStrategy";

// =========================================================================
// Entry point
// =========================================================================

/// Installs both endpoints and writes the provenance record.
///
/// <p>Terminates the process with a non-zero exit code on failure, which is how
/// bootstrap.sh detects it: an ObjectScript error alone would leave the session
/// exiting 0 and the container reporting a healthy but empty server.</p>
ClassMethod Run() As %Status
{
    #dim sc As %Status = $$$OK
    #dim ex As %Exception.AbstractException

    try {
        do ..Banner()

        // Read configuration first: a missing variable must fail before any
        // namespace is created, not halfway through.
        #dim config As %DynamicObject = ..ReadConfig()
        #dim plan As %DynamicObject = ..ReadPlan(config.packagePlan)

        // The API user and its role are instance-wide, so they are created once
        // before the per-level work.
        $$$ThrowOnError(..EnsureSecurity(config))

        // Per level, as the plan describes them - see PlanLevels(). Sequential
        // rather than parallel: package import is I/O and CPU bound and the levels
        // share the same instance, so overlapping them would only lengthen the
        // critical path.
        #dim levels As %DynamicArray = ..PlanLevels(plan)
        if levels.%Size() = 0 {
            // One line, see RequireEnv() on why.
            $$$ThrowStatus($$$ERROR($$$GeneralError, config.packagePlan _ ": 'endpoints' describes no known level - regenerate it with 'make packages'"))
        }
        #dim iter As %Iterator.Array = levels.%GetIterator()
        #dim key, level As %String
        while iter.%GetNext(.key, .level) {
            $$$ThrowOnError(..InstallLevel(level, config, plan))
        }

        // Written last: it is the record of what was actually installed, so it
        // must not exist unless everything above succeeded.
        $$$ThrowOnError(..WriteProvenance(config, plan))

        do ..Log("")
        do ..Log("All endpoints installed.")

    } catch ex {
        do ..Log("")
        do ..Log("ERROR: " _ ex.DisplayString())
        do ..Log("")
        // Non-zero exit is the contract with bootstrap.sh.
        do $system.Process.Terminate($job, 1)
    }

    quit sc
}

// =========================================================================
// Configuration
// =========================================================================

/// Reads the ISIK_* environment variables into a single object.
///
/// <p>Every value is required. Defaulting a missing endpoint path or namespace
/// would produce a server that installs cleanly but is not the one the test
/// suite was configured against - a failure mode that would surface only as
/// inexplicable 404s much later.</p>
ClassMethod ReadConfig() As %DynamicObject
{
    #dim config As %DynamicObject = {}

    set config.nsStufe3       = ..RequireEnv("ISIK_NS_STUFE3")
    set config.nsStufe5       = ..RequireEnv("ISIK_NS_STUFE5")
    set config.baseStufe3     = ..RequireEnv("ISIK_FHIR_BASE_STUFE3")
    set config.baseStufe5     = ..RequireEnv("ISIK_FHIR_BASE_STUFE5")
    set config.apiUser        = ..RequireEnv("ISIK_SUT_USER")
    set config.apiPassword    = ..RequireEnv("ISIK_SUT_PASSWORD")
    set config.mgmtUser       = ..RequireEnv("ISIK_MGMT_USER")
    set config.mgmtPassword   = ..RequireEnv("ISIK_MGMT_PASSWORD")
    set config.packageRoot    = ..RequireEnv("ISIK_PACKAGE_ROOT")
    set config.packagePlan    = ..RequireEnv("ISIK_PACKAGE_PLAN")
    set config.stateDir       = ..Env("ISIK_STATE_DIR", "/opt/isik/state")

    // Endpoint tuning. Defaults match .env.example; see docs/en/03-sut-iris.md
    // for why each deviates from the IRIS default.
    set config.defaultPageSize   = +..Env("ISIK_DEFAULT_PAGE_SIZE", 100)
    set config.maxPageSize       = +..Env("ISIK_MAX_PAGE_SIZE", 500)
    set config.maxSearchResults  = +..Env("ISIK_MAX_SEARCH_RESULTS", 10000)
    set config.preferHandling    = ..Env("ISIK_PREFER_HANDLING", "lenient")

    quit config
}

/// Value of an environment variable, or "" when unset.
ClassMethod Env(name As %String, default As %String = "") As %String
{
    #dim value As %String = $system.Util.GetEnviron(name)
    quit $select(value = "" : default, 1 : value)
}

/// Value of a required environment variable; throws when unset.
ClassMethod RequireEnv(name As %String) As %String
{
    #dim value As %String = $system.Util.GetEnviron(name)
    if value = "" {
        // One line, deliberately. ObjectScript has no line-continuation syntax, so
        // a macro invocation split across lines is not a long expression - the
        // preprocessor reads an unterminated argument list and the class does not
        // compile ("MPP5612: Referenced macro missing right paren"). Where a message
        // is too long for one readable line, it is concatenated into a local first.
        $$$ThrowStatus($$$ERROR($$$GeneralError, "required environment variable " _ name _ " is not set"))
    }
    quit value
}

/// Parses vendor/package-plan.json.
///
/// <p>The plan is generated from the test suite's own validator plugins, so it
/// is the authoritative statement of which packages each endpoint must carry.
/// See scripts/lib/plan_packages.py.</p>
ClassMethod ReadPlan(path As %String) As %DynamicObject
{
    #dim stream As %Stream.FileCharacter = ##class(%Stream.FileCharacter).%New()
    $$$ThrowOnError(stream.LinkToFile(path))

    #dim plan As %DynamicObject = {}.%FromJSON(stream)

    if '$isobject(plan.endpoints) {
        $$$ThrowStatus($$$ERROR($$$GeneralError, path _ ": no 'endpoints' object - regenerate it with 'make packages'"))
    }
    quit plan
}

/// The levels this round installs: the known levels, filtered by what the package
/// plan actually describes.
///
/// <p>Both halves matter. The literal list fixes the order, so two instances built
/// from the same plan install in the same sequence and the provenance record lists
/// the endpoints in the same order; iterating the plan's own keys would make that
/// an artefact of JSON key order. The plan is what decides which of them exist,
/// because <code>plan_packages.py</code> builds <code>endpoints</code> over
/// suite_levels_lc() - the levels the pinned test suite carries. Gematik release
/// 3.0.0 removed ISiK Level 3, so under a 3.x suite the plan has no
/// <code>endpoints.stufe3</code>, and installing a namespace nothing will test is
/// not a harmless extra: it would appear in iris-provenance.json as an endpoint of
/// this round, which the generated reports read as evidence of what was tested.</p>
///
/// <p>Returns the levels in order. The caller must treat an empty result as an
/// error: a plan that describes no endpoint is a defect in the plan, not a round
/// with nothing to do.</p>
ClassMethod PlanLevels(plan As %DynamicObject) As %DynamicArray
{
    #dim known As %DynamicArray = ["stufe3", "stufe5"]
    #dim out As %DynamicArray = []
    #dim iter As %Iterator.Array = known.%GetIterator()
    #dim key As %String
    #dim level As %String

    while iter.%GetNext(.key, .level) {
        if $isobject(plan.endpoints.%Get(level)) {
            do out.%Push(level)
        }
    }
    quit out
}

// =========================================================================
// Security
// =========================================================================

/// Creates the resource, role and user the test suite authenticates as, and makes
/// every account on the instance usable for the duration of the round.
///
/// <p>The test suite reaches the SUT with HTTP Basic authentication (configured
/// in the rendered Tiger YAML). Authenticated access is used rather than
/// unauthenticated: it is closer to a real deployment, it exercises the REST
/// layer's auth path, and it keeps the endpoints from being open to anything
/// that reaches the subnet.</p>
///
/// <p>No password ever expires here, and no account is ever left needing a
/// password change. Both are deliberate, and for the same reason: this instance
/// exists for exactly one test round, and either condition arriving mid-round
/// would present as a wave of 401s indistinguishable from an authorisation gap in
/// the SUT. The official image ships its predefined accounts (<code>_SYSTEM</code>,
/// <code>SuperUser</code>, <code>Admin</code>) with expired passwords, so without
/// step 4 the Management Portal the documentation points the tester at cannot be
/// used at all.</p>
///
/// <p>The instance-wide password policy is left at the product default. It is
/// <em>not</em> relaxed: the generated passwords satisfy the default
/// <code>PasswordPattern</code> by construction (see <code>gen_password()</code> in
/// scripts/lib.sh), so the SUT keeps a production-like security configuration.
/// Step 5 reports that pattern if a password is nonetheless rejected, because such
/// a rejection would otherwise present as an unexplained bootstrap failure.</p>
ClassMethod EnsureSecurity(config As %DynamicObject) As %Status
{
    new $namespace
    set $namespace = "%SYS"

    do ..Log("")
    do ..Log("--- Security -----")

    // 1. Resource guarding the FHIR web applications.
    //    Signature: Create(Name, Description, PublicPermission, Type)
    //    PublicPermission "" - no implicit public access, so reaching an endpoint
    //    requires the role below. Type is deliberately not passed: the class
    //    reference marks it "internal use only, user defined resources should not
    //    pass this parameter".
    if '##class(Security.Resources).Exists(..#FHIRRESOURCE) {
        $$$ThrowOnError(##class(Security.Resources).Create(..#FHIRRESOURCE, "ISiK conformance test: FHIR endpoint access", ""))
        do ..Log("created resource " _ ..#FHIRRESOURCE)
    } else {
        do ..Log("resource " _ ..#FHIRRESOURCE _ " already exists")
    }

    // 2. Role holding that resource with Use permission.
    //    Signature: Create(Name, Description, Resources, GrantedRoles, EscalationOnly)
    //    GrantedRoles is deliberately empty - in particular %All is NOT granted:
    //    the API user must not be able to reconfigure the server it is testing.
    if '##class(Security.Roles).Exists(..#FHIRROLE) {
        $$$ThrowOnError(##class(Security.Roles).Create(..#FHIRROLE, "ISiK conformance test: FHIR API client", ..#FHIRRESOURCE _ ":U", ""))
        do ..Log("created role " _ ..#FHIRROLE)
    } else {
        do ..Log("role " _ ..#FHIRROLE _ " already exists")
    }

    // 3. The API user the test suite authenticates as.
    //    Signature: Create(Username, UserRoles, Password, FullName, NameSpace,
    //                      Routine, ExpirationDate, ChangePassword, Enabled,
    //                      Comment, Flags, PhoneNumber, PhoneProvider,
    //                      Attributes, AccountNeverExpires, PasswordNeverExpires, ...)
    //    ChangePassword 0 and PasswordNeverExpires 1 matter: a forced password
    //    change or an expiry mid-round would present as a wave of 401s that looks
    //    exactly like an authorisation gap in the SUT.
    if '##class(Security.Users).Exists(config.apiUser) {
        do ..ThrowOnPasswordError(##class(Security.Users).Create(
            /* Username             */ config.apiUser,
            /* UserRoles            */ ..#FHIRROLE,
            /* Password             */ config.apiPassword,
            /* FullName             */ "ISiK conformance test: FHIR API client",
            /* NameSpace            */ "%SYS",
            /* Routine              */ "",
            /* ExpirationDate       */ "",
            /* ChangePassword       */ 0,
            /* Enabled              */ 1,
            /* Comment              */ "Created by ISiK.Setup. Throwaway credential for one test round.",
            /* Flags                */ 1,
            /* PhoneNumber          */ "",
            /* PhoneProvider        */ "",
            /* Attributes           */ ,
            /* AccountNeverExpires  */ 1,
            /* PasswordNeverExpires */ 1))
        do ..Log("created user " _ config.apiUser)
    } else {
        // A re-run with a regenerated password must update the existing user, or
        // the rendered Tiger configuration and the server would disagree and every
        // scenario would fail on authentication.
        //   Signature: Modify(Username, ByRef Properties)
        #dim props As %String
        set props("Password") = config.apiPassword
        set props("Roles") = ..#FHIRROLE
        set props("Enabled") = 1
        set props("ChangePassword") = 0
        set props("PasswordNeverExpires") = 1
        do ..ThrowOnPasswordError(##class(Security.Users).Modify(config.apiUser, .props))
        do ..Log("user " _ config.apiUser _ " already exists - credentials updated")
    }

    // 4. The predefined accounts, so the Management Portal is actually usable.
    //    The official image ships them with expired passwords, which the portal
    //    answers with a forced-password-change page - and `make manual` and
    //    run/CREDENTIALS.md both point the tester at that portal.
    //
    //    The list is a superset: which accounts a given IRIS edition predefines
    //    varies, so a missing one is logged and skipped rather than fatal.
    //
    //    CSPSystem gets the management password too, and that is deliberate: it is
    //    the account the Web Gateway authenticates with for its own service
    //    connection, and the gateway's CSP.ini is rendered from the same generated
    //    password by scripts/30-render-config.sh. The two must agree. Leaving it at
    //    the image default instead would mean the rig depends on an undocumented
    //    shipped password, and leaving it expired is worse than it sounds: the
    //    gateway then logs "Connection Validation Failed ... Access Denied" and
    //    Apache answers every FHIR request with a bare 500.
    #dim predefined As %DynamicArray = ["_SYSTEM", "SuperUser", "Admin", "CSPSystem"]
    #dim iter As %Iterator.Array = predefined.%GetIterator()
    #dim key As %String
    #dim account As %String
    while iter.%GetNext(.key, .account) {
        if '##class(Security.Users).Exists(account) {
            do ..Log("account " _ account _ " is not defined on this instance - skipped")
            continue
        }

        #dim aprops As %String
        kill aprops
        set aprops("Enabled") = 1
        set aprops("ChangePassword") = 0
        set aprops("PasswordNeverExpires") = 1
        set aprops("AccountNeverExpires") = 1

        // The account the tester logs in with, and the one the Web Gateway connects
        // as, both need the generated password: the first because it is what
        // run/CREDENTIALS.md documents, the second because the gateway's rendered
        // CSP.ini carries the same value.
        #dim needsPassword As %Boolean = (account = config.mgmtUser) || (account = "CSPSystem")
        if needsPassword {
            set aprops("Password") = config.mgmtPassword
        }

        do ..ThrowOnPasswordError(##class(Security.Users).Modify(account, .aprops))
        do ..Log("account " _ account _ " enabled, password set to never expire"
            _ $select(account = config.mgmtUser: " (management password applied)",
                      account = "CSPSystem": " (management password applied - the Web Gateway authenticates as this)",
                      1: ""))
    }

    // The configured management user need not be one of the predefined accounts.
    // If it is not, it is created here - with %All, because its whole purpose is
    // administrative access to the Management Portal.
    if '##class(Security.Users).Exists(config.mgmtUser) {
        do ..ThrowOnPasswordError(##class(Security.Users).Create(
            /* Username             */ config.mgmtUser,
            /* UserRoles            */ "%All",
            /* Password             */ config.mgmtPassword,
            /* FullName             */ "ISiK conformance test: Management Portal / tester",
            /* NameSpace            */ "%SYS",
            /* Routine              */ "",
            /* ExpirationDate       */ "",
            /* ChangePassword       */ 0,
            /* Enabled              */ 1,
            /* Comment              */ "Created by ISiK.Setup. Documented in run/CREDENTIALS.md.",
            /* Flags                */ 1,
            /* PhoneNumber          */ "",
            /* PhoneProvider        */ "",
            /* Attributes           */ ,
            /* AccountNeverExpires  */ 1,
            /* PasswordNeverExpires */ 1))
        do ..Log("created management user " _ config.mgmtUser)
    }

    // 5. Clear any expiry the instance-wide policy has already applied, for every
    //    account rather than just ours. "*" is the parameter's documented default:
    //    an account left expired that this class did not touch would still cost the
    //    tester a diagnosis, and this instance lives for one round.
    //    Signature: UnExpireUserPasswords(Names, ByRef Count)
    #dim unexpired As %Integer = 0
    $$$ThrowOnError(##class(Security.Users).UnExpireUserPasswords("*", .unexpired))
    do ..Log("un-expired the passwords of " _ +unexpired _ " account(s)")

    quit $$$OK
}

/// Throws on a failed security-API call, adding the instance's own password policy
/// to the message when the failure looks like a policy rejection.
///
/// <p>A password the instance's <code>PasswordPattern</code> rejects surfaces from
/// <code>Security.Users</code> as a generic error, which reaches the operator as a
/// bootstrap that failed for no stated reason. The pattern is deliberately left at
/// the product default (see the class comment), so if this ever fires the fix is to
/// change the generator in scripts/lib.sh - and that decision needs the pattern the
/// instance is actually enforcing, printed here.</p>
ClassMethod ThrowOnPasswordError(sc As %Status) As %Status
{
    if $$$ISOK(sc) {
        quit $$$OK
    }

    #dim text As %String = $system.Status.GetErrorText(sc)

    // Read the policy for the message only. Nothing here writes it: relaxing the
    // instance's password rules would weaken the environment under test.
    #dim pattern As %String = "(could not be read)"
    try {
        // Signature: Get(ByRef Properties) - Security.System is a singleton, so
        // there is no name argument.
        #dim props As %String
        if $$$ISOK(##class(Security.System).Get(.props)) {
            set pattern = $get(props("PasswordPattern"), "(not set)")
        }
    } catch {
        // Leave the placeholder: a failure to read the policy must not replace the
        // real error with a secondary one.
    }

    // Built up over several SET commands rather than passed inline: each SET is its
    // own command and may span as many lines as it likes, whereas the macro call
    // below must be a single line (see RequireEnv).
    #dim msg As %String = text
    set msg = msg _ " -- if this is a password-policy rejection, the instance's PasswordPattern is '" _ pattern _ "'."
    set msg = msg _ " Generated passwords come from gen_password() in scripts/lib.sh and are built to satisfy"
    set msg = msg _ " the IRIS default '3.32ANP'; adjust the generator rather than the instance policy."

    $$$ThrowStatus($$$ERROR($$$GeneralError, msg))
}

// =========================================================================
// Per-level installation
// =========================================================================

/// Installs one ISiK level: namespace, packages, endpoint, validator, tuning.
ClassMethod InstallLevel(level As %String, config As %DynamicObject, plan As %DynamicObject) As %Status
{
    #dim spec As %DynamicObject = plan.endpoints.%Get(level)
    if '$isobject(spec) {
        $$$ThrowStatus($$$ERROR($$$GeneralError, "package plan has no endpoints." _ level))
    }

    #dim namespace As %String = spec.namespace
    #dim endpoint As %String = spec.endpoint

    do ..Log("")
    do ..Log("==================================================================")
    do ..Log("ISiK " _ level _ ": namespace " _ namespace _ ", endpoint " _ endpoint)
    do ..Log("==================================================================")

    // The plan's namespace/endpoint must agree with the environment, or the test
    // suite would be pointed at an endpoint that does not exist. Comparing here
    // catches an .env edit made after `make packages`.
    #dim expectedNs As %String = $select(level = "stufe3" : config.nsStufe3, 1 : config.nsStufe5)
    #dim expectedEp As %String = $select(level = "stufe3" : config.baseStufe3, 1 : config.baseStufe5)
    if (namespace '= expectedNs) || (endpoint '= expectedEp) {
        #dim mismatch As %String = "package plan says " _ namespace _ " " _ endpoint
        set mismatch = mismatch _ " but the environment says " _ expectedNs _ " " _ expectedEp
        set mismatch = mismatch _ " - .env changed after 'make packages'; re-run it"
        $$$ThrowStatus($$$ERROR($$$GeneralError, mismatch))
    }

    $$$ThrowOnError(..EnsureNamespace(namespace))
    $$$ThrowOnError(..ImportPackages(namespace, spec, config))
    $$$ThrowOnError(..EnsureEndpoint(namespace, endpoint, spec))
    $$$ThrowOnError(..ConfigureEndpoint(namespace, endpoint, config))
    $$$ThrowOnError(..SecureEndpoint(endpoint))

    quit $$$OK
}

/// Creates a FHIR-enabled Foundation namespace if it does not exist.
ClassMethod EnsureNamespace(namespace As %String) As %Status
{
    do ..Log("")
    do ..Log("--- Namespace " _ namespace _ " -----")

    if ##class(%SYS.Namespace).Exists(namespace) {
        do ..Log("namespace already exists")
    } else {
        // A HealthShare Foundation namespace: creates the databases, maps the
        // HS* packages and installs the schema the FHIR server builds on.
        do ..Log("creating Foundation namespace (this takes a few minutes)")
        $$$ThrowOnError(##class(HS.Util.Installer.Foundation).Install(namespace))
        do ..Log("Foundation namespace created")
    }

    new $namespace
    set $namespace = namespace

    // FHIR-enables the namespace: installs the MetadataSet descriptors and the
    // external language server the profile validator runs in.
    //
    // DO, not $$$ThrowOnError. InstallNamespace is a *void* method - its
    // ReturnType in %Dictionary.CompiledMethod is empty. Invoking a void method in
    // an expression raises <COMMAND> "Function must return a value", and it does so
    // *after* the installation has run, so the failure looks like a broken
    // installer rather than a broken call. Such methods signal failure by throwing,
    // which the caller's try/catch already handles; there is no status to check.
    //
    // Which of them are void is not guessed. Every HS.* and Security.* method this
    // class calls was read out of %Dictionary.CompiledMethod on irishealth 2026.2
    // (HS.* is visible in HSLIB, Security.* in %SYS). Exactly three are void:
    //
    //   HS.FHIRServer.Installer.InstallNamespace           (here)
    //   HS.FHIRServer.Installer.InstallInstance            (EnsureEndpoint)
    //   HS.FHIRServer.API.InteractionsStrategy.SaveServiceConfigData
    //                                                      (ConfigureEndpoint)
    //
    // Everything else returns a status or a value: Foundation.Install,
    // InitializeProfileValidator and all eleven Security.* calls return %Status;
    // importPackages returns %List, GetAllPackages %ListOfObjects,
    // GetStrategyForEndpoint an object, IsPackageLoaded and EndpointExists booleans,
    // GetServiceConfigData a ConfigData. The three void ones are the only `do` calls
    // in this class that would otherwise look like a missing error check.
    if '..NamespaceIsFHIREnabled() {
        do ..Log("FHIR-enabling the namespace")
        do ##class(HS.FHIRServer.Installer).InstallNamespace(namespace)
        set ^ISiK.Setup("fhirEnabled") = $zdatetime($ztimestamp, 3, 7)
        do ..Log("namespace FHIR-enabled")
    } else {
        do ..Log("namespace is already FHIR-enabled (marker set "
            _ ^ISiK.Setup("fhirEnabled") _ ")")
    }

    quit $$$OK
}

/// True when this bootstrap has already FHIR-enabled the current namespace.
///
/// <p>The guard is a marker global written by <method>EnsureNamespace</method>
/// rather than a probe of the FHIR metadata schema. IRIS exposes no documented
/// "is this namespace FHIR-enabled" predicate, and the obvious substitutes are
/// unreliable: <code>HS.FHIRMeta.Storage.Package</code> lives in HSLIB and is
/// mapped into every Foundation namespace whether FHIR is installed or not, so
/// its mere existence proves nothing, and reading its storage definition through
/// the <code>$$$comClassKeyGet</code> macro would depend on an internal API that
/// carries no compatibility guarantee across releases.</p>
///
/// <p>The tradeoff is explicit: a namespace FHIR-enabled by some other means -
/// the Management Portal, <code>HS.FHIRServer.ConsoleSetup</code> - is not
/// recognised, and <method>InstallNamespace</method> would run against it a
/// second time. That case does not arise here, because the environment is built
/// from an empty container on every test round; and if it ever did, the failure
/// would be immediate and loud rather than a silently half-configured SUT.</p>
ClassMethod NamespaceIsFHIREnabled() As %Boolean
{
    quit ''$data(^ISiK.Setup("fhirEnabled"))
}

/// Imports the level's FHIR packages, in dependency order.
///
/// <p><code>HS.FHIRMeta.Load.NpmLoader.importPackages()</code> takes a %List of
/// unpacked package directories; a package's dependencies must already be
/// present or appear earlier in the same list. The order comes from the plan
/// (scripts/lib/order_packages.py), which derives it from each package.json.</p>
///
/// <p>Already-imported packages are skipped rather than force-reimported: the
/// full ISiK 5 closure takes tens of minutes, and a re-run after a partial
/// failure should resume, not restart.</p>
ClassMethod ImportPackages(namespace As %String, spec As %DynamicObject, config As %DynamicObject) As %Status
{
    new $namespace
    set $namespace = namespace

    do ..Log("")
    do ..Log("--- FHIR packages -----")

    #dim toImport As %List = ""
    #dim skipped As %Integer = 0
    #dim iter As %Iterator.Array = spec.loadOrder.%GetIterator()
    #dim key, pkgId As %String

    while iter.%GetNext(.key, .pkgId) {
        // IsPackageLoaded() expects the registry's name@version form, while the
        // plan and the directory names use the name#version form used by the
        // FHIR package registry and by the refv configs.
        #dim atForm As %String = $replace(pkgId, "#", "@")
        if ##class(HS.FHIRMeta.Load.NpmLoader).IsPackageLoaded(atForm) {
            set skipped = skipped + 1
            continue
        }

        #dim dir As %String = config.packageRoot _ "/" _ pkgId
        if '##class(%File).DirectoryExists(dir) {
            $$$ThrowStatus($$$ERROR($$$GeneralError, "package directory missing: " _ dir _ " - re-run 'make packages' on the host"))
        }
        if '##class(%File).Exists(dir _ "/package.json") {
            $$$ThrowStatus($$$ERROR($$$GeneralError, dir _ " has no package.json at its root - NpmLoader will reject it"))
        }
        set toImport = toImport _ $listbuild(dir)
    }

    if skipped > 0 {
        do ..Log("already imported: " _ skipped _ " packages")
    }

    if $listlength(toImport) = 0 {
        do ..Log("nothing to import")
        quit $$$OK
    }

    do ..Log("importing " _ $listlength(toImport) _ " packages (this is the slow step)")
    // One call for the whole ordered list: NpmLoader resolves intra-list
    // dependencies, which a package-at-a-time loop would not.
    #dim imported As %List = ##class(HS.FHIRMeta.Load.NpmLoader).importPackages(toImport)
    do ..Log("imported " _ $listlength(imported) _ " packages")

    // Confirm rather than assume: importPackages() reports what it did, and a
    // package silently missing here would surface as unexplained profile
    // failures during the run.
    #dim missing As %String = ""
    set iter = spec.loadOrder.%GetIterator()
    while iter.%GetNext(.key, .pkgId) {
        if '##class(HS.FHIRMeta.Load.NpmLoader).IsPackageLoaded($replace(pkgId, "#", "@")) {
            set missing = missing _ $select(missing = "" : "", 1 : ", ") _ pkgId
        }
    }
    if missing '= "" {
        $$$ThrowStatus($$$ERROR($$$GeneralError, "packages did not import: " _ missing))
    }
    do ..Log("all " _ spec.loadOrder.%Size() _ " packages confirmed loaded")

    quit $$$OK
}

/// Creates the FHIR endpoint (web application + service instance) if absent.
ClassMethod EnsureEndpoint(namespace As %String, endpoint As %String, spec As %DynamicObject) As %Status
{
    new $namespace
    set $namespace = namespace

    do ..Log("")
    do ..Log("--- Endpoint " _ endpoint _ " -----")

    if ##class(HS.FHIRServer.ServiceAdmin).EndpointExists(endpoint) {
        do ..Log("endpoint already exists")
        quit $$$OK
    }

    // The endpoint's package list: which IGs this endpoint's canonical map, its
    // profile validation and its generated CapabilityStatement are built from.
    // The plan's endpoint_package_order excludes example-only packages and
    // resolves multi-version pins - see scripts/lib/plan_packages.py.
    #dim packages As %List = ""
    #dim iter As %Iterator.Array = spec.endpointPackageOrder.%GetIterator()
    #dim key, pkgId As %String
    while iter.%GetNext(.key, .pkgId) {
        set packages = packages _ $listbuild($replace(pkgId, "#", "@"))
    }

    do ..Log("installing endpoint with " _ $listlength(packages) _ " packages")
    do ..Log("  strategy: " _ ..#STRATEGY)

    // DO, not $$$ThrowOnError - InstallInstance is void, like InstallNamespace.
    // See the note in EnsureNamespace for why that distinction matters here.
    // Failure surfaces as a thrown exception, and independently as the
    // web-application check below, which is why nothing is lost by not having a
    // status to inspect.
    do ##class(HS.FHIRServer.Installer).InstallInstance(endpoint, ..#STRATEGY, packages)
    do ..Log("endpoint installed")

    // Loads the endpoint's profiles into the FHIR_Validation_Server external
    // language server. Required for $validate - which the seeder uses to prove
    // the test data is profile-conformant before the run, so that data defects
    // cannot be reported as SUT gaps.
    do ..Log("initialising the profile validator")
    #dim errorMsg As %String = ""
    // Returns %Status *and* fills errorMsg - not void, unlike the two installer
    // calls above. The status is folded into errorMsg rather than thrown, for the
    // reason given below: this step is not load-bearing for the conformance run.
    #dim vsc As %Status = ##class(HS.FHIRServer.Installer).InitializeProfileValidator(.errorMsg)
    if $$$ISERR(vsc) {
        set errorMsg = errorMsg _ $select(errorMsg '= "": "; ", 1: "")
            _ $system.Status.GetErrorText(vsc)
    }
    if errorMsg '= "" {
        // Not fatal: the conformance run itself is validated by Tiger's own
        // reference validator, not by IRIS. Only the seeder's pre-flight
        // $validate check depends on this, and 50-seed.sh degrades gracefully.
        do ..Log("WARNING: profile validator initialisation reported: " _ errorMsg)
        do ..Log("  $validate may be unavailable; the seeder will report this.")
    } else {
        do ..Log("profile validator initialised")
    }

    quit $$$OK
}

/// Applies the endpoint tuning the test suite needs.
///
/// <p>Each deviation from the IRIS default is deliberate and is documented in
/// docs/en/03-sut-iris.md. They are configuration, not code changes: the SUT is
/// stock IRIS, and a reader of the results must be able to see exactly which
/// knobs were turned.</p>
ClassMethod ConfigureEndpoint(namespace As %String, endpoint As %String, config As %DynamicObject) As %Status
{
    new $namespace
    set $namespace = namespace

    do ..Log("")
    do ..Log("--- Endpoint configuration -----")

    #dim strategy As HS.FHIRServer.API.InteractionsStrategy
    set strategy = ##class(HS.FHIRServer.API.InteractionsStrategy).GetStrategyForEndpoint(endpoint)
    if '$isobject(strategy) {
        $$$ThrowStatus($$$ERROR($$$GeneralError, "no interactions strategy for endpoint " _ endpoint))
    }

    #dim cfg As HS.FHIRServer.API.ConfigData = strategy.GetServiceConfigData()

    // Search result sizing. The suite issues searches with explicit _count values
    // and several deliberately broad searches; the IRIS defaults would truncate
    // them and the resulting "resource not in bundle" failures would look like
    // search-semantics gaps rather than pagination.
    set cfg.DefaultSearchPageSize = config.defaultPageSize
    set cfg.MaxSearchPageSize     = config.maxPageSize
    set cfg.MaxSearchResults      = config.maxSearchResults

    // Prefer: handling. `lenient` makes IRIS ignore search parameters it does not
    // recognise instead of rejecting the request outright. This is the FHIR
    // default behaviour and the one ISiK expects; `strict` is available via .env
    // for gap-hunting, where surfacing every unsupported parameter as an error is
    // the point.
    set cfg.DefaultPreferHandling = config.preferHandling

    // Off. Debug mode adds diagnostic detail to OperationOutcome, which would
    // make the recorded responses differ from a production deployment's - and
    // those responses are the evidence in the gap report.
    set cfg.DebugMode = 0

    // DO, not $$$ThrowOnError - SaveServiceConfigData is the third void method in
    // this class, alongside the two installer entry points. See the note in
    // EnsureNamespace for the census this comes from. It throws on rejection, and
    // the values are read back below out of the object it saved rather than out of
    // our own configuration, so a silent no-op could not pass unnoticed.
    do strategy.SaveServiceConfigData(cfg)

    // Read back from the strategy rather than trusting `cfg`: this is the endpoint's
    // stored configuration as the FHIR service will see it, and it is what the
    // provenance record and the reports cite.
    set cfg = strategy.GetServiceConfigData()

    do ..Log("DefaultSearchPageSize = " _ cfg.DefaultSearchPageSize)
    do ..Log("MaxSearchPageSize     = " _ cfg.MaxSearchPageSize)
    do ..Log("MaxSearchResults      = " _ cfg.MaxSearchResults)
    do ..Log("DefaultPreferHandling = " _ cfg.DefaultPreferHandling)
    do ..Log("DebugMode             = " _ cfg.DebugMode)

    quit $$$OK
}

/// Puts the endpoint's web application behind password authentication.
///
/// <p><method>EnsureEndpoint</method>'s InstallInstance call creates the CSP
/// application, with unauthenticated access enabled - the IRIS default
/// (<code>AutheEnabled</code> initial expression 64 = AutheUnauthenticated). This
/// switches it to password-only (bit 5, value 32), so a missing or wrong
/// credential is a 401 rather than a silently anonymous request.</p>
///
/// <p>The application's own <property>Resource</property> is preserved when the
/// installer set one, and granted to the API user's role instead of being
/// replaced. Overwriting it would change how a stock IRIS FHIR endpoint is
/// protected, and the SUT must stay stock: an inspector comparing this
/// installation to a normal one should find the difference to be authentication
/// mode and nothing else.</p>
///
/// <p><property>MatchRoles</property> is deliberately untouched. That is where
/// InstallInstance puts the database and SQL privileges the FHIR service needs, and
/// they apply to the request by virtue of the application, not of the caller - so
/// the API user needs no database roles of its own and cannot use them anywhere
/// else.</p>
ClassMethod SecureEndpoint(endpoint As %String) As %Status
{
    new $namespace
    set $namespace = "%SYS"

    #dim app As Security.Applications
    #dim exists As %Boolean = ##class(Security.Applications).Exists(endpoint, .app)
    if 'exists || '$isobject(app) {
        // The endpoint installer creates this; its absence means the install did
        // not complete, which must not pass silently.
        $$$ThrowStatus($$$ERROR($$$GeneralError, "web application " _ endpoint _ " does not exist after InstallInstance"))
    }

    #dim installerResource As %String = app.Resource

    // 32 = AuthePassword. Explicitly not 64 (AutheUnauthenticated).
    set app.AutheEnabled = 32
    set app.Enabled = 1
    if installerResource = "" {
        set app.Resource = ..#FHIRRESOURCE
    }
    $$$ThrowOnError(app.%Save())

    if installerResource = "" {
        do ..Log("secured " _ endpoint _ ": password authentication, resource "
            _ ..#FHIRRESOURCE _ " (the installer set none)")
    } else {
        // Grant the installer's resource to our role so the API user can still
        // reach the endpoint. Use permission is what a CSP application resource
        // is checked for.
        #dim props As %String
        #dim roleProps As %String
        $$$ThrowOnError(##class(Security.Roles).Get(..#FHIRROLE, .roleProps))
        #dim resources As %String = $get(roleProps("Resources"), "")
        if '$find("," _ resources _ ",", "," _ installerResource _ ":") {
            set props("Resources") = resources
                _ $select(resources = "" : "", 1 : ",")
                _ installerResource _ ":U"
            $$$ThrowOnError(##class(Security.Roles).Modify(..#FHIRROLE, .props))
        }
        do ..Log("secured " _ endpoint _ ": password authentication, resource "
            _ installerResource _ " (set by InstallInstance, granted to "
            _ ..#FHIRROLE _ ")")
    }

    quit $$$OK
}

// =========================================================================
// Verification
// =========================================================================

/// Independently confirms that both endpoints are installed and will serve.
///
/// <p>Called by bootstrap.sh after <method>Run</method> has reported success, and
/// deliberately re-reads everything from the instance rather than trusting what
/// the installer just did.</p>
///
/// <p>This is not an HTTP check, because IRIS serves no HTTP: the official images
/// contain no web server (see the <code>webgateway</code> service in
/// docker-compose.yml), and the container that provides one cannot start until
/// IRIS is already listening. So what is verified here is the state the Web
/// Gateway will serve - the web application's existence, its dispatch class,
/// authentication method, resource and namespace binding - plus a
/// CapabilityStatement produced through the FHIR interactions layer, which is the
/// same code path an HTTP GET on <code>/metadata</code> reaches once the gateway
/// hands the request over. The end-to-end HTTP path is checked from outside by
/// healthcheck.sh.</p>
///
/// <p>Writes <code>VERIFY OK</code> as its last line on success; bootstrap.sh
/// requires that string, so an ObjectScript error that aborts this method
/// mid-way cannot read as a pass.</p>
ClassMethod VerifyEndpoints() As %Status
{
    #dim sc As %Status = $$$OK
    try {
        #dim config As %DynamicObject = ..ReadConfig()
        // The plan is read here too, for its endpoint list only: verifying an
        // endpoint the round did not install would report a correct installation as
        // a failure. See PlanLevels().
        #dim plan As %DynamicObject = ..ReadPlan(config.packagePlan)
        #dim levels As %DynamicArray = ..PlanLevels(plan)
        #dim iter As %Iterator.Array = levels.%GetIterator()
        #dim key As %String
        #dim level As %String

        while iter.%GetNext(.key, .level) {
            #dim namespace As %String = $select(level = "stufe3": config.nsStufe3, 1: config.nsStufe5)
            #dim endpoint As %String = $select(level = "stufe3": config.baseStufe3, 1: config.baseStufe5)

            // --- The web application, as the gateway will find it ---
            new $namespace
            set $namespace = "%SYS"

            if '##class(Security.Applications).Exists(endpoint) {
                set sc = $$$ERROR($$$GeneralError, "web application " _ endpoint _ " does not exist")
                quit
            }

            #dim props As %String
            kill props
            $$$ThrowOnError(##class(Security.Applications).Get(endpoint, .props))

            if $get(props("Enabled")) '= 1 {
                set sc = $$$ERROR($$$GeneralError, endpoint _ " is disabled (Enabled=" _ $get(props("Enabled")) _ ")")
                quit
            }
            // The diagnostic text is built into `msg` first, then passed to the macro
            // on a single line. $$$ERROR is a preprocessor macro, and the preprocessor
            // does not honour ObjectScript's line continuation inside a macro
            // argument list: splitting one across lines compiles to "MPP5612:
            // Referenced macro missing right paren". Ordinary multi-line calls such as
            // ..Log() below are unaffected, which is why only these five sites need it.
            #dim msg As %String
            if $get(props("DispatchClass")) '= "HS.FHIRServer.RestHandler" {
                set msg = endpoint _ " dispatches to '" _ $get(props("DispatchClass"))
                set msg = msg _ "', expected HS.FHIRServer.RestHandler - the gateway would route the request to the wrong handler"
                set sc = $$$ERROR($$$GeneralError, msg)
                quit
            }
            if $get(props("NameSpace")) '= namespace {
                set msg = endpoint _ " is bound to namespace '" _ $get(props("NameSpace"))
                set msg = msg _ "', expected " _ namespace
                set sc = $$$ERROR($$$GeneralError, msg)
                quit
            }
            // 32 = AuthePassword. The suite authenticates with HTTP Basic, and an
            // endpoint that also allowed unauthenticated access would silently make
            // every authorisation-related scenario pass for the wrong reason.
            if $get(props("AutheEnabled")) '= 32 {
                set msg = endpoint _ " has AutheEnabled=" _ $get(props("AutheEnabled"))
                set msg = msg _ ", expected 32 (password only)"
                set sc = $$$ERROR($$$GeneralError, msg)
                quit
            }

            do ..Log(endpoint _ ": application enabled, dispatch HS.FHIRServer.RestHandler, ns "
                _ namespace _ ", password auth, resource " _ $get(props("Resource")))

            // --- The FHIR service itself ---
            // Reached through the strategy, which is what RestHandler resolves the
            // request to. A CapabilityStatement that cannot be produced here would
            // produce an HTTP 500 through the gateway.
            new $namespace
            set $namespace = namespace

            #dim strategy As HS.FHIRServer.API.InteractionsStrategy
            set strategy = ##class(HS.FHIRServer.API.InteractionsStrategy).GetStrategyForEndpoint(endpoint)
            if '$isobject(strategy) {
                set msg = "no FHIR service is installed at " _ endpoint
                set msg = msg _ " - the web application exists but GetStrategyForEndpoint() returned nothing"
                set sc = $$$ERROR($$$GeneralError, msg)
                quit
            }

            // GetMetadataResource() on the strategy, not GetMetadata() on an
            // interactions instance: the latter does not exist. `Interactions` carries
            // LoadMetadata()/SetMetadata(), which are the storage-side accessors for the
            // stored CapabilityStatement, while the resource an HTTP GET on /metadata
            // returns is produced by the strategy - verified against the class
            // dictionary of irishealth:2026.2, where the only zero-argument method
            // returning it is HS.FHIRServer.*.InteractionsStrategy::GetMetadataResource.
            #dim caps As %DynamicObject = strategy.GetMetadataResource()
            if '$isobject(caps) {
                set msg = endpoint _ ": GetMetadataResource() returned no CapabilityStatement"
                set sc = $$$ERROR($$$GeneralError, msg)
                quit
            }
            if caps.resourceType '= "CapabilityStatement" {
                set msg = endpoint _ ": /metadata yields resourceType '"
                set msg = msg _ caps.resourceType _ "', expected CapabilityStatement"
                set sc = $$$ERROR($$$GeneralError, msg)
                quit
            }

            #dim resourceCount As %Integer = 0
            if $isobject(caps.rest), caps.rest.%Size() > 0, $isobject(caps.rest.%Get(0).resource) {
                set resourceCount = caps.rest.%Get(0).resource.%Size()
            }
            do ..Log(endpoint _ ": CapabilityStatement fhirVersion " _ caps.fhirVersion
                _ ", " _ resourceCount _ " resource types")
        }

        $$$ThrowOnError(sc)
        do ..Log("VERIFY OK")
    } catch ex {
        set sc = ex.AsStatus()
        do ..Log("VERIFY FAILED: " _ $system.Status.GetErrorText(sc))
    }
    quit sc
}

// =========================================================================
// Provenance
// =========================================================================

/// Writes state/iris-provenance.json: what this instance actually is.
///
/// <p>Read by the documentation generators and by the results analysis. It is
/// deliberately produced by the SUT itself rather than restated from .env, so
/// that the documented configuration is the observed one.</p>
ClassMethod WriteProvenance(config As %DynamicObject, plan As %DynamicObject) As %Status
{
    do ..Log("")
    do ..Log("--- Provenance -----")

    #dim out As %DynamicObject = {}
    set out."_comment" = "GENERATED by ISiK.Setup on the SUT. Describes the FHIR server as installed, read back from the instance."
    set out.generatedAt = $zdatetime($ztimestamp, 3, 7)
    set out.irisVersion = $zv
    set out.strategyClass = ..#STRATEGY
    // Account names only. The passwords are documented on purpose, but not here:
    // this file is the SUT's self-description of its installation, and the credential
    // channel is run/CREDENTIALS.md plus the run.json "credentials" block, which the
    // results analysis renders. One channel, so there is one thing to look at.
    set out.apiUser = config.apiUser
    set out.mgmtUser = config.mgmtUser
    set out.packagePlanSource = plan.sourceImage

    set out.endpoints = []

    // Only what was installed, which is what the plan describes - see PlanLevels().
    // This file is read by the report generators as the record of the endpoints
    // this round tested, so an entry for a level the suite does not carry would be
    // a false statement about the round, not a spare row.
    #dim levels As %DynamicArray = ..PlanLevels(plan)
    #dim iter As %Iterator.Array = levels.%GetIterator()
    #dim key, level As %String

    while iter.%GetNext(.key, .level) {
        #dim spec As %DynamicObject = plan.endpoints.%Get(level)
        #dim entry As %DynamicObject = {}
        set entry.level = level
        set entry.namespace = spec.namespace
        set entry.endpoint = spec.endpoint

        new $namespace
        set $namespace = spec.namespace

        // Read the loaded packages back from IRIS rather than echoing the plan:
        // this is the difference between "what we asked for" and "what is
        // installed", and only the latter is evidence.
        set entry.loadedPackages = []
        #dim packages As %ListOfObjects = ##class(HS.FHIRMeta.Storage.Package).GetAllPackages()
        #dim i As %Integer
        for i = 1:1:packages.Count() {
            do entry.loadedPackages.%Push(packages.GetAt(i).id)
        }

        // The endpoint's declared package list and its configuration, as stored.
        #dim strategy As HS.FHIRServer.API.InteractionsStrategy
        set strategy = ##class(HS.FHIRServer.API.InteractionsStrategy).GetStrategyForEndpoint(spec.endpoint)
        if $isobject(strategy) {
            #dim cfg As HS.FHIRServer.API.ConfigData = strategy.GetServiceConfigData()
            set entry.configuration = {}
            set entry.configuration.DefaultSearchPageSize = cfg.DefaultSearchPageSize
            set entry.configuration.MaxSearchPageSize     = cfg.MaxSearchPageSize
            set entry.configuration.MaxSearchResults      = cfg.MaxSearchResults
            set entry.configuration.DefaultPreferHandling = cfg.DefaultPreferHandling
            set entry.configuration.DebugMode             = cfg.DebugMode
        }

        do out.endpoints.%Push(entry)
        do ..Log(level _ ": " _ entry.loadedPackages.%Size() _ " packages loaded in " _ spec.namespace)
    }

    #dim path As %String = config.stateDir _ "/iris-provenance.json"
    #dim stream As %Stream.FileCharacter = ##class(%Stream.FileCharacter).%New()
    $$$ThrowOnError(stream.LinkToFile(path))
    $$$ThrowOnError(stream.Clear())

    // %ToJSON returns a *%String*, not a %Status: with no argument it returns the
    // serialised text, and with a stream argument it writes to the stream and
    // returns "". Passing that to $$$ThrowOnError raises ERROR #5034 "Invalid
    // status code structure" on success - the empty string is not a status - which
    // reads as a serialisation failure when nothing is wrong. It signals real
    // failure by throwing, and the %Save below is what can actually fail.
    // (ReturnType read from %Dictionary.CompiledMethod on irishealth 2026.2, like
    // the census in EnsureNamespace; %Stream.FileCharacter's LinkToFile, Clear and
    // %Save do all return %Status.)
    do out.%ToJSON(stream)
    $$$ThrowOnError(stream.%Save())

    do ..Log("wrote " _ path)

    quit $$$OK
}

// =========================================================================
// Output
// =========================================================================

ClassMethod Banner()
{
    do ..Log("ISiK.Setup - building the unit under test")
    do ..Log("IRIS: " _ $zv)
}

/// Timestamped line to the current device. bootstrap.sh tees this into
/// run/iris-state/bootstrap.log, which is archived as part of the evidence.
ClassMethod Log(message As %String)
{
    // WRITE with a trailing ! only. There is deliberately no explicit flush call:
    // $system.Device does not exist in IRIS for Health 2026.2 (verified by probing
    // the image - %Library.Device does, %SYSTEM.Device does not), and calling it
    // raised <CLASS DOES NOT EXIST> on the very first log line, which took the whole
    // bootstrap down before it had installed anything. The line-terminated write is
    // sufficient in practice: this method's output goes to the session's principal
    // device, which bootstrap.sh pipes through `tee` into
    // run/iris-state/bootstrap.log, so progress is visible as it happens.
    write $zdatetime($ztimestamp, 3, 7), "  ", message, !
}

}
