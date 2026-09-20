package com.centron.sentinel.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.centron.sentinel.data.EventEntity
import com.centron.sentinel.data.SentinelDatabase
import com.centron.sentinel.net.ConnectionState
import com.centron.sentinel.net.ConnectionStatus
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn

class SentinelViewModel(app: Application) : AndroidViewModel(app) {

    private val dao = SentinelDatabase.get(app).events()

    val events: StateFlow<List<EventEntity>> =
        dao.observeAll().stateIn(
            scope = viewModelScope,
            started = SharingStarted.WhileSubscribed(5_000),
            initialValue = emptyList(),
        )

    val connection: StateFlow<ConnectionState> = ConnectionStatus.state
}
