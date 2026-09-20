package com.centron.sentinel.net

import java.lang.ref.WeakReference

/**
 * Handle on the live socket, published by SentinelService so the UI can send
 * an approval without binding to the service.
 *
 * Weak on purpose: the UI must never be the reason a destroyed service's
 * client stays alive. A null here means "not connected", which the approval
 * path has to handle anyway — the socket can drop between rendering a pending
 * action and the user's thumb landing on it.
 */
object ActiveClient {
    private var ref: WeakReference<SentinelClient>? = null

    fun publish(client: SentinelClient?) {
        ref = client?.let { WeakReference(it) }
    }

    fun get(): SentinelClient? = ref?.get()
}
