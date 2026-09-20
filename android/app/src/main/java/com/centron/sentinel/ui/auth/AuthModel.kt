package com.centron.sentinel.ui.auth

/**
 * The identity seam.
 *
 * Nothing here talks to Google, Microsoft or GitHub yet, and that is
 * deliberate rather than unfinished. Real OAuth needs three things this
 * project does not have:
 *
 *   1. A registered OAuth client per provider, each with its own redirect URI
 *      and, for Microsoft and GitHub, a client secret.
 *   2. A confidential backend to exchange the authorization code. A secret
 *      shipped inside an APK is not a secret; anyone can pull it out with
 *      apktool. The exchange has to happen server side.
 *   3. A decision about what an account even means here. CENTRON v2 says one
 *      admin identity per engine and no cloud backend — so "sign in with
 *      Google" currently has no server to sign in to.
 *
 * Until (3) is answered, this interface is the honest boundary: the UI is
 * real, the transport is not, and nothing pretends to have authenticated
 * anyone. Implement [AuthGateway] against the engine's pairing flow — or
 * against a real IdP if the account model changes — and the screen does not
 * need to change.
 */
enum class AuthProvider(val label: String) {
    GOOGLE("Google"),
    MICROSOFT("Microsoft"),
    GITHUB("GitHub"),
}

sealed interface AuthState {
    data object Idle : AuthState

    /** A provider round trip is in flight. The UI locks the other options. */
    data class Working(val provider: AuthProvider?) : AuthState

    data class Failed(val message: String) : AuthState

    data class Authenticated(val displayName: String) : AuthState
}

interface AuthGateway {
    suspend fun signIn(provider: AuthProvider): AuthState
    suspend fun continueWithEmail(email: String): AuthState
}

/**
 * Stand-in that refuses rather than fakes success.
 *
 * A mock that returned Authenticated would make the screen look finished and
 * hide the fact that there is no identity system behind it — exactly the kind
 * of thing that gets discovered on stage. This one says what is missing.
 */
class UnconfiguredAuthGateway : AuthGateway {
    override suspend fun signIn(provider: AuthProvider): AuthState =
        AuthState.Failed("${provider.label} sign-in needs an OAuth client and a backend to exchange the code. Not wired yet.")

    override suspend fun continueWithEmail(email: String): AuthState =
        AuthState.Failed("Email sign-in needs an identity backend. Pair with an engine instead.")
}
