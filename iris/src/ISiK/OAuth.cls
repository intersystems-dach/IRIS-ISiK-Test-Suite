/// Points the ISiK Stufe 5 endpoint at the round's OAuth 2.0 authorization
/// server, so that the test suite's <code>Connect</code> module can be executed
/// against it.
///
/// <p><b>What this class does and does not do.</b> It performs four
/// configuration steps, all of them on objects the product provides for exactly
/// this purpose, and all of them of the kind a customer performs when connecting
/// their FHIR server to their own identity provider:</p>
///
/// <ol>
/// <li>an outbound TLS configuration naming the trust anchor of the
///     authorization server's certificate (<class>Security.SSLConfigs</class>);</li>
/// <li>an OAuth 2.0 server definition, obtained by discovery from the
///     authorization server's published metadata
///     (<class>OAuth2.ServerDefinition</class>);</li>
/// <li>a client definition of type <i>resource server</i>, which is the identity
///     the FHIR server authenticates with when it introspects a presented token
///     (<class>OAuth2.Client</class>), associated with the endpoint through
///     <class>HS.Util.RESTCSPConfig</class>'s <property>OAuthClientName</property>;</li>
/// <li>the endpoint's expected token audience and its advertised SMART on FHIR
///     core capabilities (<class>HS.FHIRServer.API.ConfigData</class> and
///     <class>HS.FHIRServer.ServiceAdmin</class>).</li>
/// </ol>
///
/// <p>It writes no code into the system under test, overrides no
/// <code>HS.Local.*</code> extension point, and changes no behaviour of the FHIR
/// server itself. Token validation, scope evaluation and patient-compartment
/// filtering are performed entirely by the product, on the request path, exactly
/// as shipped - which is the property that makes the resulting measurement a
/// statement about InterSystems IRIS for Health and not about this rig.</p>
///
/// <p><b>Only base IRIS for Health is used.</b> Every class named above is part
/// of IRIS for Health. Nothing here is taken from, or reproduces code of,
/// HealthShare Unified Care Record, which is a separately licensed product: the
/// OAuth 2.0 framework (<code>%SYS.OAuth2.*</code>, <code>OAuth2.*</code>) is
/// part of the InterSystems platform, and the FHIR server's token handling
/// (<code>HS.FHIRServer.*</code>) is part of the FHIR repository that ships with
/// IRIS for Health.</p>
///
/// <p><b>Deliberately not configured.</b> <property>SearchFiltering</property> is
/// left at the value the product's own installer sets. It governs whether a
/// patient-scoped search is filtered to the token's patient compartment or
/// refused; the installed default filters, and measurement showed the
/// alternative to satisfy fewer of the suite's scenarios. Changing it would be a
/// configuration choice made to influence a result, which is not what this rig
/// does.</p>
///
/// <p>Runs at container start, from <class>ISiK.Setup</class>, and only when
/// <code>ISIK_CONNECT_ENABLE</code> is set - i.e. when compose/authserver.yml is
/// in play. Output goes to the current device, which bootstrap.sh tees into
/// run/iris-state/bootstrap.log.</p>
Class ISiK.OAuth Extends %RegisteredObject
{

/// Name of the outbound TLS configuration used for every connection to the
/// authorization server (metadata, signing keys, introspection).
Parameter SSLCONFIG = "ISIKAS";

/// TLS protocol bitmask for that configuration: 16 = TLSv1.2, 32 = TLSv1.3.
/// Older versions are deliberately not enabled.
Parameter SSLPROTOCOLS = 48;

/// How long to wait for the authorization server to publish its metadata, in
/// seconds, and how often to retry. IRIS reaches this step tens of minutes after
/// container start, by which time the authorization server has long been up, so
/// this is a safety net rather than the normal path - but it is a necessary one:
/// `compose up` orders the containers, it does not order their readiness.
Parameter DISCOVERYATTEMPTS = 24;

Parameter DISCOVERYINTERVAL = 5;

/// The SMART on FHIR core capabilities the endpoint advertises at
/// <code>/.well-known/smart-configuration</code>.
///
/// <p>This is a statement about the deployment, not a claim about the product:
/// the document is assembled by
/// <method>HS.FHIRServer.Util.WellKnown.GetSMARTConfiguration</method> from the
/// authorization server's metadata plus this list, and what belongs in the list
/// is what this deployment actually supports. The six capabilities named are the
/// ones the configuration in compose/authserver.yml and
/// keycloak/realm-isik.json.tmpl provides: a confidential symmetric client,
/// standalone launch with a patient context, and the three SMART v2 permission
/// classes the suite presents tokens for.</p>
///
/// <p>Comma-delimited, because that is the format
/// <method>GetSMARTCapabilities</method> parses.</p>
Parameter SMARTCAPABILITIES = "launch-standalone,client-confidential-symmetric,context-standalone-patient,permission-patient,permission-user,permission-v2";

// =========================================================================
// Entry point
// =========================================================================

/// Configures token acceptance for one endpoint. Throws on any failure: a round
/// that silently proceeds without the authorization server would run the Connect
/// module against an endpoint that cannot accept a token, and report the result
/// as a property of the product.
ClassMethod Configure(namespace As %String, endpoint As %String, config As %DynamicObject) As %Status
{
    do ..Log("")
    do ..Log("--- Authorization server (ISiK Stufe 5 Connect) -----")
    do ..Log("issuer    " _ config.asIssuer)
    do ..Log("client    " _ config.asClientName _ " (client_id " _ config.asClientId _ ")")
    do ..Log("audience  " _ config.sutAudience)

    $$$ThrowOnError(..EnsureSSLConfig(config))
    $$$ThrowOnError(..EnsureServerDefinition(config))
    $$$ThrowOnError(..EnsureClient(config))
    $$$ThrowOnError(..AssociateEndpoint(namespace, endpoint, config))

    quit $$$OK
}

// =========================================================================
// Steps
// =========================================================================

/// The outbound TLS configuration IRIS uses when it talks to the authorization
/// server.
///
/// <p><property>VerifyPeer</property> stays on and the CA file is the round's own
/// trust anchor (run/tls/ca.crt, mounted read-only). Switching verification off
/// would have been the shorter route and would have made the round's evidence
/// worthless: an unverified TLS connection to the issuer of the tokens being
/// validated is not a deployment anyone would accept, and the product would then
/// have been measured in a configuration no customer may run.</p>
ClassMethod EnsureSSLConfig(config As %DynamicObject) As %Status
{
    new $namespace
    set $namespace = "%SYS"

    if '##class(%File).Exists(config.asCAFile) {
        #dim missing As %String = "the authorization server's trust anchor is not readable at " _ config.asCAFile
        set missing = missing _ " - it is bind-mounted from run/tls/ca.crt by compose/authserver.yml; run 'make up'"
        $$$ThrowStatus($$$ERROR($$$GeneralError, missing))
    }

    #dim props As %String
    set props("Description") = "Outbound TLS to the ISiK Connect authorization server"
    set props("Enabled")     = 1
    // 0 = client configuration: IRIS is the client of the authorization server.
    set props("Type")        = 0
    set props("CAFile")      = config.asCAFile
    set props("VerifyPeer")  = 1
    set props("Protocols")   = ..#SSLPROTOCOLS

    #dim sc As %Status
    if ##class(Security.SSLConfigs).Exists(..#SSLCONFIG) {
        set sc = ##class(Security.SSLConfigs).Modify(..#SSLCONFIG, .props)
    } else {
        set sc = ##class(Security.SSLConfigs).Create(..#SSLCONFIG, .props)
    }
    $$$ThrowOnError(sc)

    do ..Log("TLS configuration " _ ..#SSLCONFIG _ ": peer verification on, CA " _ config.asCAFile)
    quit $$$OK
}

/// The OAuth 2.0 server definition, obtained by discovery.
///
/// <p>Discovery rather than a hand-written definition: the endpoints, the
/// supported grant types and the signing keys then come from the authorization
/// server's own published metadata, so the configuration cannot describe a server
/// that does not exist, and the round's documentation can cite the issuer instead
/// of a list of URLs somebody typed.</p>
///
/// <p>Re-discovered on every bootstrap, after deleting any previous definition.
/// The authorization server's realm is re-imported at every start and its signing
/// keys are new each time, so a kept definition would hold stale keys and every
/// token would fail verification with a signature error.</p>
ClassMethod EnsureServerDefinition(config As %DynamicObject) As %Status
{
    new $namespace
    set $namespace = "%SYS"

    // The client first: it holds a reference to the definition.
    do ..DeleteClientIfPresent(config.asClientName)

    if ##class(OAuth2.ServerDefinition).Exists(config.asIssuer) {
        $$$ThrowOnError(##class(OAuth2.ServerDefinition).DeleteByIssuer(config.asIssuer))
        do ..Log("discarded the previous server definition for this issuer")
    }

    #dim sc As %Status = $$$OK
    #dim attempt As %Integer
    for attempt = 1:1:..#DISCOVERYATTEMPTS {
        set sc = ##class(OAuth2.ServerDefinition).DiscoverServer(config.asIssuer, ..#SSLCONFIG)
        quit:$$$ISOK(sc)
        if attempt = 1 {
            do ..Log("authorization server not ready yet; retrying discovery")
        }
        hang ..#DISCOVERYINTERVAL
    }
    if $$$ISERR(sc) {
        #dim failed As %String = "could not discover the authorization server at " _ config.asIssuer
        set failed = failed _ " after " _ (..#DISCOVERYATTEMPTS * ..#DISCOVERYINTERVAL) _ "s: " _ $system.Status.GetErrorText(sc)
        $$$ThrowStatus($$$ERROR($$$GeneralError, failed))
    }

    #dim srv As OAuth2.ServerDefinition = ##class(OAuth2.ServerDefinition).OpenByIssuer(config.asIssuer, .sc)
    $$$ThrowOnError(sc)
    if '$isobject(srv) {
        $$$ThrowStatus($$$ERROR($$$GeneralError, "discovery reported success but no server definition exists for " _ config.asIssuer))
    }

    do ..Log("discovered the authorization server (definition " _ srv.%Id() _ ")")
    do ..Log("  token endpoint         " _ srv.TokenEndpoint)
    do ..Log("  introspection endpoint " _ srv.IntrospectionEndpoint)
    do ..Log("  signing keys           " _ $length(srv.PublicJWKS) _ " bytes of JWKS")

    quit $$$OK
}

/// The client definition the FHIR server authenticates with.
///
/// <p>Type <i>resource</i>: this client never requests a token of its own, it
/// presents its credentials in order to introspect tokens other clients were
/// issued. That is the role the ISiK specification gives the FHIR server.</p>
///
/// <p><property>AuthenticationType</property> is set explicitly, and that is not
/// cosmetic. A client created through <method>%New</method> leaves it empty
/// despite the property's initial expression, and an empty value makes IRIS send
/// the introspection request with no client authentication at all. The
/// authorization server then answers <code>401 invalid_request</code>, the FHIR
/// server turns that into a bare <code>HTTP 401</code> with an empty body, and
/// the only trace is an audit record - i.e. the one failure mode in this whole
/// configuration that is invisible from the outside. Measured on
/// 2026.2; see docs/en/08-connect.md.</p>
ClassMethod EnsureClient(config As %DynamicObject) As %Status
{
    new $namespace
    set $namespace = "%SYS"

    #dim sc As %Status
    #dim srv As OAuth2.ServerDefinition = ##class(OAuth2.ServerDefinition).OpenByIssuer(config.asIssuer, .sc)
    $$$ThrowOnError(sc)

    do ..DeleteClientIfPresent(config.asClientName)

    #dim client As OAuth2.Client = ##class(OAuth2.Client).%New()
    set client.ApplicationName    = config.asClientName
    set client.ClientType         = "resource"
    set client.Description        = "ISiK Stufe 5 Connect resource server"
    set client.Enabled            = 1
    set client.ServerDefinition   = srv
    set client.SSLConfiguration   = ..#SSLCONFIG
    set client.ClientId           = config.asClientId
    set client.ClientSecret       = config.asClientSecret
    // See the class method's documentation: without this, introspection is sent
    // unauthenticated and every request fails with an empty 401.
    set client.AuthenticationType = "basic"
    $$$ThrowOnError(client.%Save())

    // Read back through the product's own accessor, and prove the configuration
    // works rather than that it was stored: GetServerMetadataByClient is the call
    // HS.FHIRServer.Util.WellKnown makes, so a non-empty result here means the
    // endpoint's SMART configuration document will be servable and that client
    // authentication against the authorization server succeeds.
    #dim meta As %DynamicObject = ##class(%ZHS.HC.OAuth2.Utils).GetServerMetadataByClient(config.asClientName)
    if '$isobject(meta) || 'meta.%Size() {
        #dim nometa As %String = "the client definition " _ config.asClientName _ " was stored but the authorization server's"
        set nometa = nometa _ " metadata cannot be retrieved through it - check the client secret and run/tls/ca.crt"
        $$$ThrowStatus($$$ERROR($$$GeneralError, nometa))
    }

    do ..Log("client definition " _ config.asClientName _ ": resource server, basic client authentication")
    do ..Log("  server metadata retrieved through it: " _ meta.%Size() _ " fields")

    quit $$$OK
}

/// Removes an existing client definition of this name, if there is one.
///
/// <p><method>OAuth2.Client.DeleteId</method> rather than
/// <method>%DeleteId</method>: it is the framework's own entry point and also
/// discards the tokens cached against the client, which a kept-around token
/// store would otherwise survive a re-import of the realm with.</p>
ClassMethod DeleteClientIfPresent(name As %String)
{
    new $namespace
    set $namespace = "%SYS"

    #dim existing As OAuth2.Client
    #dim sc As %Status
    if '##class(OAuth2.Client).Exists(name, .existing, .sc) quit
    $$$ThrowOnError(sc)
    if '$isobject(existing) quit

    $$$ThrowOnError(##class(OAuth2.Client).DeleteId(existing.%Id()))
    do ..Log("discarded the previous client definition " _ name)
    quit
}

/// Associates the endpoint with the client, and states what a token must carry.
///
/// <p>Three settings, in the namespace the endpoint lives in:</p>
///
/// <ul>
/// <li><property>OAuthClientName</property> on the endpoint's
///     <class>HS.Util.RESTCSPConfig</class> record. This is the switch that turns
///     token handling on: with it empty, <method>ValidateOAuthToken</method> is
///     never reached and <code>/.well-known/smart-configuration</code> answers
///     with an error. The record itself is created by the FHIR installer; only
///     this one field is set.</li>
/// <li><property>Audiences</property>: the value a presented token's
///     <code>aud</code> claim must carry, which is the endpoint's own HTTPS URL.
///     Without it, a token issued for any other resource server by the same
///     authorization server would be accepted here - the audience check is the
///     product's defence against exactly that, and leaving it unset would be a
///     configuration no deployment should run.</li>
/// <li><code>smart_capabilities</code> on the strategy configuration: what the
///     endpoint advertises at <code>/.well-known/smart-configuration</code>. See
///     the SMARTCAPABILITIES parameter.</li>
/// </ul>
ClassMethod AssociateEndpoint(namespace As %String, endpoint As %String, config As %DynamicObject) As %Status
{
    new $namespace
    set $namespace = namespace

    // --- 1. the endpoint's OAuth client ---
    #dim sc As %Status
    #dim rest As HS.Util.RESTCSPConfig = ##class(HS.Util.RESTCSPConfig).URLIndexOpen(endpoint, , .sc)
    if '$isobject(rest) {
        #dim norec As %String = "no HS.Util.RESTCSPConfig record for " _ endpoint _ ": " _ $system.Status.GetErrorText(sc)
        set norec = norec _ " - the FHIR installer creates it, so this means the endpoint is not fully installed"
        $$$ThrowStatus($$$ERROR($$$GeneralError, norec))
    }
    set rest.OAuthClientName = config.asClientName
    $$$ThrowOnError(rest.%Save())

    // --- 2. the expected token audience ---
    #dim strategy As HS.FHIRServer.API.InteractionsStrategy
    set strategy = ##class(HS.FHIRServer.API.InteractionsStrategy).GetStrategyForEndpoint(endpoint)
    if '$isobject(strategy) {
        $$$ThrowStatus($$$ERROR($$$GeneralError, "no interactions strategy for endpoint " _ endpoint))
    }
    #dim cfg As HS.FHIRServer.API.ConfigData = strategy.GetServiceConfigData()
    do cfg.Audiences.Clear()
    do cfg.Audiences.Insert(config.sutAudience)
    // DO, not $$$ThrowOnError: SaveServiceConfigData is a void method. See the
    // note in ISiK.Setup.ConfigureEndpoint.
    do strategy.SaveServiceConfigData(cfg)

    // --- 3. the advertised SMART capabilities ---
    #dim instanceId As %String = ##class(HS.FHIRServer.ServiceAdmin).GetInstanceIdForEndpoint(endpoint)
    if instanceId = "" {
        $$$ThrowStatus($$$ERROR($$$GeneralError, "no FHIR service instance is registered for endpoint " _ endpoint))
    }
    #dim strategyCfg As %DynamicObject = ##class(HS.FHIRServer.ServiceAdmin).GetInstanceStrategyConfigData(instanceId)
    set strategyCfg."smart_capabilities" = ..#SMARTCAPABILITIES
    do ##class(HS.FHIRServer.ServiceAdmin).SetInstanceStrategyConfigData(instanceId, strategyCfg)

    // --- read everything back from the instance, not from our own variables ---
    #dim restBack As HS.Util.RESTCSPConfig = ##class(HS.Util.RESTCSPConfig).URLIndexOpen(endpoint, , .sc)
    set cfg = strategy.GetServiceConfigData()
    set strategyCfg = ##class(HS.FHIRServer.ServiceAdmin).GetInstanceStrategyConfigData(instanceId)

    if '$isobject(restBack) || (restBack.OAuthClientName '= config.asClientName) {
        $$$ThrowStatus($$$ERROR($$$GeneralError, endpoint _ ": OAuthClientName did not take effect"))
    }
    if (cfg.Audiences.Count() '= 1) || (cfg.Audiences.GetAt(1) '= config.sutAudience) {
        $$$ThrowStatus($$$ERROR($$$GeneralError, endpoint _ ": the token audience did not take effect"))
    }
    if strategyCfg."smart_capabilities" '= ..#SMARTCAPABILITIES {
        $$$ThrowStatus($$$ERROR($$$GeneralError, endpoint _ ": smart_capabilities did not take effect"))
    }

    do ..Log("endpoint " _ endpoint _ " now accepts tokens from this authorization server")
    do ..Log("  OAuthClientName    = " _ restBack.OAuthClientName)
    do ..Log("  Audiences          = " _ cfg.Audiences.GetAt(1))
    do ..Log("  SearchFiltering    = " _ cfg.SearchFiltering _ " (product default, deliberately not changed)")
    do ..Log("  smart_capabilities = " _ strategyCfg."smart_capabilities")

    quit $$$OK
}

// =========================================================================
// Output
// =========================================================================

/// Same format as ISiK.Setup's log, because both write to the one bootstrap log
/// that the generated documentation cites.
ClassMethod Log(message As %String)
{
    write $zdatetime($ztimestamp, 3, 1, 0), "  ", message, !
}

}
