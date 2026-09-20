# kotlinx.serialization keeps generated serializers
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.**
-keepclassmembers class com.centron.sentinel.contract.** {
    *** Companion;
}
-keepclasseswithmembers class com.centron.sentinel.contract.** {
    kotlinx.serialization.KSerializer serializer(...);
}
