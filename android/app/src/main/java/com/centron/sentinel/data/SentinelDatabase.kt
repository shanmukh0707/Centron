package com.centron.sentinel.data

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

@Database(entities = [EventEntity::class], version = 1, exportSchema = false)
abstract class SentinelDatabase : RoomDatabase() {

    abstract fun events(): EventDao

    companion object {
        @Volatile private var instance: SentinelDatabase? = null

        fun get(context: Context): SentinelDatabase =
            instance ?: synchronized(this) {
                instance ?: Room.databaseBuilder(
                    context.applicationContext,
                    SentinelDatabase::class.java,
                    "sentinel.db",
                )
                    // Demo-day posture: a schema change must never brick the
                    // app on the phone we are presenting from.
                    .fallbackToDestructiveMigration()
                    .build()
                    .also { instance = it }
            }
    }
}
