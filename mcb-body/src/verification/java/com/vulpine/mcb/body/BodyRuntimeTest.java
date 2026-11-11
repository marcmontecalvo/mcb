package com.vulpine.mcb.body;

/** Queued-request staleness counts server ticks, not wall time (run 20261111T135214868983Z). */
public final class BodyRuntimeTest {
    private static void check(boolean yes, String label) { if (!yes) throw new AssertionError(label); }

    public static void main(String[] args) {
        // Received during tick 5999, which took 2.25 s of wall time; drained at tick 6000: still fresh.
        check(!BodyRuntime.staleQueued(5999, 6000), "one long tick does not age a request");
        check(!BodyRuntime.staleQueued(5999, 5999 + BodyRuntime.STALE_QUEUED_TICKS), "fresh up to the limit");
        check(BodyRuntime.staleQueued(5999, 6000 + BodyRuntime.STALE_QUEUED_TICKS), "older than the limit is stale");
        check(BodyRuntime.STALE_QUEUED_TICKS == 40, "two seconds of game time at 20 TPS");
        // The watchdog judges after the inbox drain: a heartbeat queued during the long tick is contact.
        long ms = 1_000_000L;
        check(!BodyRuntime.watchdogDue(2_100 * ms, 2_250 * ms), "a heartbeat queued in a long tick keeps the route");
        check(BodyRuntime.watchdogDue(0, 2_250 * ms), "real silence over two seconds still cuts the client off");
        System.out.println("BodyRuntimeTest ok");
    }
}
