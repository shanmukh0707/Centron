package com.centron.sentinel.security

import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_STRONG
import androidx.biometric.BiometricManager.Authenticators.DEVICE_CREDENTIAL
import androidx.biometric.BiometricPrompt
import androidx.fragment.app.FragmentActivity
import kotlinx.coroutines.suspendCancellableCoroutine
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.Signature
import kotlin.coroutines.resume

/**
 * Biometric / device-credential gate for destructive actions.
 *
 * The contract is blunt about this: the biometric gate on Take Action is not a
 * stretch goal, the demo depends on it. So it is built to actually mean
 * something rather than to look right.
 *
 * What makes it real: the approval is signed by a private key that lives in
 * the Android Keystore and is generated with setUserAuthenticationRequired(true).
 * The key is unusable until the OS has authenticated the user for this
 * operation. A callback saying "auth succeeded" can be reached by a tampered
 * build; a signature from an auth-bound Keystore key cannot be produced
 * without a fresh unlock. We send `biometric: true` on the wire only when we
 * hold that signature.
 *
 * setInvalidatedByBiometricEnrollment(true) means enrolling a new fingerprint
 * permanently invalidates the key. That is the desired behaviour: if someone
 * adds their thumb to the demo phone, previously issued approval authority
 * dies rather than silently transferring.
 */
object BiometricGate {

    private const val KEY_ALIAS = "sentinel.approval.v1"
    private const val KEYSTORE = "AndroidKeyStore"
    private const val SIGNING = "SHA256withECDSA"

    /**
     * Crypto-backed prompts with DEVICE_CREDENTIAL are only permitted from
     * API 30. Below that, a PIN/password fallback cannot carry a CryptoObject,
     * so we degrade to an unsigned prompt and say so in the result rather than
     * pretending the assertion is as strong.
     */
    private val cryptoWithCredentialSupported: Boolean
        get() = Build.VERSION.SDK_INT >= Build.VERSION_CODES.R

    sealed interface Result {
        /** Authenticated, and we hold a Keystore signature over the payload. */
        data class Signed(val signatureB64: String) : Result

        /** Authenticated, but the platform could not carry a CryptoObject.
         *  Still a real unlock; weaker evidence. */
        data object AuthenticatedUnsigned : Result

        data object Cancelled : Result
        data class Unavailable(val reason: String) : Result
        data class Failed(val reason: String) : Result
    }

    fun status(manager: BiometricManager): String? =
        when (manager.canAuthenticate(BIOMETRIC_STRONG or DEVICE_CREDENTIAL)) {
            BiometricManager.BIOMETRIC_SUCCESS -> null
            BiometricManager.BIOMETRIC_ERROR_NO_HARDWARE ->
                "This device has no biometric hardware."
            BiometricManager.BIOMETRIC_ERROR_HW_UNAVAILABLE ->
                "Biometric hardware is unavailable right now."
            BiometricManager.BIOMETRIC_ERROR_NONE_ENROLLED ->
                "No biometric or screen lock enrolled. Set one up to approve actions."
            BiometricManager.BIOMETRIC_ERROR_SECURITY_UPDATE_REQUIRED ->
                "A security update is required before biometrics can be used."
            else -> "Biometric authentication is not available."
        }

    /**
     * Prompts, then signs [payload] with the auth-bound key.
     *
     * [payload] should bind everything that matters about the decision —
     * approval_id, event_id, the decision itself, and a client timestamp — so
     * the signature cannot be replayed against a different action.
     */
    suspend fun authorize(
        activity: FragmentActivity,
        title: String,
        subtitle: String,
        payload: String,
    ): Result {
        val manager = BiometricManager.from(activity)
        status(manager)?.let { return Result.Unavailable(it) }

        val signature = try {
            prepareSignature()
        } catch (e: Exception) {
            // Key invalidated by a new biometric enrolment, or otherwise
            // unusable. Recoverable exactly once: drop it and regenerate.
            deleteKey()
            try {
                prepareSignature()
            } catch (e2: Exception) {
                return Result.Failed("Could not prepare the signing key: ${e2.message}")
            }
        }

        return promptAndSign(activity, title, subtitle, payload, signature)
    }

    private suspend fun promptAndSign(
        activity: FragmentActivity,
        title: String,
        subtitle: String,
        payload: String,
        signature: Signature,
    ): Result = suspendCancellableCoroutine { cont ->

        val callback = object : BiometricPrompt.AuthenticationCallback() {

            override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                val sig = result.cryptoObject?.signature
                if (sig == null) {
                    // Device-credential path below API 30 — genuine unlock,
                    // no CryptoObject available.
                    cont.resume(Result.AuthenticatedUnsigned)
                    return
                }
                val outcome = try {
                    sig.update(payload.toByteArray())
                    Result.Signed(android.util.Base64.encodeToString(sig.sign(), android.util.Base64.NO_WRAP))
                } catch (e: Exception) {
                    Result.Failed("Signing failed after authentication: ${e.message}")
                }
                cont.resume(outcome)
            }

            override fun onAuthenticationError(code: Int, message: CharSequence) {
                val res = when (code) {
                    BiometricPrompt.ERROR_USER_CANCELED,
                    BiometricPrompt.ERROR_NEGATIVE_BUTTON,
                    BiometricPrompt.ERROR_CANCELED -> Result.Cancelled
                    else -> Result.Failed(message.toString())
                }
                cont.resume(res)
            }

            // onAuthenticationFailed fires on a single bad read. The prompt
            // stays up and the user retries, so it is not terminal — do not
            // resume the continuation here.
        }

        val prompt = BiometricPrompt(activity, callback)

        val info = BiometricPrompt.PromptInfo.Builder()
            .setTitle(title)
            .setSubtitle(subtitle)
            .setAllowedAuthenticators(
                if (cryptoWithCredentialSupported) BIOMETRIC_STRONG or DEVICE_CREDENTIAL
                else BIOMETRIC_STRONG
            )
            .apply {
                // A negative button is required when DEVICE_CREDENTIAL is not
                // among the allowed authenticators, and forbidden when it is.
                if (!cryptoWithCredentialSupported) setNegativeButtonText("Cancel")
            }
            .setConfirmationRequired(true)
            .build()

        prompt.authenticate(info, BiometricPrompt.CryptoObject(signature))

        cont.invokeOnCancellation { prompt.cancelAuthentication() }
    }

    // ------------------------------------------------------------ keystore

    private fun prepareSignature(): Signature {
        val store = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        val entry = store.getEntry(KEY_ALIAS, null) as? KeyStore.PrivateKeyEntry
            ?: run { generateKey(); store.load(null); store.getEntry(KEY_ALIAS, null) as KeyStore.PrivateKeyEntry }

        return Signature.getInstance(SIGNING).apply { initSign(entry.privateKey) }
    }

    private fun generateKey() {
        val generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, KEYSTORE)
        val spec = KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_SIGN)
            .setDigests(KeyProperties.DIGEST_SHA256)
            .setUserAuthenticationRequired(true)
            .apply {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                    setInvalidatedByBiometricEnrollment(true)
                }
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                    // 0 == every use needs a fresh authentication. No window
                    // during which a second approval rides on the first unlock.
                    setUserAuthenticationParameters(
                        0,
                        KeyProperties.AUTH_BIOMETRIC_STRONG or KeyProperties.AUTH_DEVICE_CREDENTIAL,
                    )
                }
            }
            .build()
        generator.initialize(spec)
        generator.generateKeyPair()
    }

    private fun deleteKey() {
        runCatching {
            KeyStore.getInstance(KEYSTORE).apply { load(null) }.deleteEntry(KEY_ALIAS)
        }
    }

    /** Binds the decision so a signature cannot be replayed onto another action. */
    fun payloadFor(approvalId: String, eventId: String, decision: String, clientTs: String): String =
        "$approvalId|$eventId|$decision|$clientTs"
}
