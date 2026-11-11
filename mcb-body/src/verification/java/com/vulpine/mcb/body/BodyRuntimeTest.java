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
        System.out.println("BodyRuntimeTest ok");
    }
}
