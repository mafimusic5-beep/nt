package com.v2ray.ang.security

/** Policy and operation are signed, not an unauthenticated routing hint. */
internal fun gatewayCanonical(
    fields: List<String>,
    regionalPolicy: String = "international",
    operation: String = "connect",
): String {
    require(regionalPolicy == "international" || regionalPolicy == "russia")
    require(operation == "connect" || (regionalPolicy == "russia" && operation == "check"))
    val version = if (regionalPolicy == "russia") 2 else 1
    val suffix = if (version == 2) listOf("regional_policy=$regionalPolicy", "operation=$operation") else emptyList()
    return (listOf("protocol=emery-device-gate-v$version") + fields + suffix).joinToString("\n")
}
