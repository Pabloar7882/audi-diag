package pt.fisafn

/** The only group IDs the Android transport may send to the ECU. */
internal object PhysicalGroupPolicy {
    val allowedGroups: List<Int> = listOf(1, 3, 11)

    fun isAllowed(groupId: Int): Boolean = groupId in allowedGroups
}
