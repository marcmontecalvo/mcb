package com.vulpine.mcb.arena;

/**
 * A refused arena request or an arena rule that stops an action. {@link #reason} is the short code the bridge
 * reports (bad_plan, bad_request, name_exists, overlap, out_of_world, arena_protected, ...); the detail says where.
 */
public final class ArenaFail extends RuntimeException {
    public final String reason;
    public final String detail;

    public ArenaFail(String reason) { this(reason, null); }

    public ArenaFail(String reason, String detail) {
        super(detail == null ? reason : reason + ": " + detail);
        this.reason = reason;
        this.detail = detail;
    }
}
