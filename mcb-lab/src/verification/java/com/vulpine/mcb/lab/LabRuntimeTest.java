package com.vulpine.mcb.lab;

/** Queue aging in ticks and the watchdog after the drain, as in BodyRuntime (run 20261111T135214868983Z). */
public final class LabRuntimeTest {
    private static void check(boolean yes, String label) { if (!yes) throw new AssertionError(label); }

    public static void main(String[] args) {
        check(!LabRuntime.staleQueued(5999, 6000), "one long tick does not age a request");
        check(LabRuntime.staleQueued(5999, 6000 + LabRuntime.STALE_QUEUED_TICKS), "older than the limit is stale");
        long ms = 1_000_000L;
        check(!LabRuntime.watchdogDue(2_100 * ms, 2_250 * ms), "a request queued in a long tick is contact");
        check(LabRuntime.watchdogDue(0, 2_250 * ms), "real silence over two seconds still fails the episode");
        System.out.println("LabRuntimeTest ok");
    }
}
