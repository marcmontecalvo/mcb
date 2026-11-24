package com.vulpine.mcb.body;

import java.util.List;
import java.util.Set;

/**
 * The bridge's event ring, one per body plus one for events no body owns: a body's reader sees its own events and the
 * shared ones, never another body's, and a busy body cannot push another's events out.
 */
public final class EventLogTest {
    private static void check(boolean yes, String label) { if (!yes) throw new AssertionError(label); }

    private static long add(EventLog<String> log, String scope, String text, int tick) {
        long sequence = log.reserve();
        log.add(scope, sequence, tick, text);
        return sequence;
    }

    public static void main(String[] args) {
        var log = new EventLog<String>(4);
        check(log.latest() == 0 && log.batch(0, null).events().isEmpty() && !log.batch(0, null).gap(), "empty");
        add(log, "a", "a1", 1);
        add(log, "b", "b1", 1);
        add(log, null, "g1", 2);
        add(log, "a", "a2", 3);
        check(log.latest() == 4, "one sequence across every ring");

        check(log.batch(0, null).events().equals(List.of("a1", "b1", "g1", "a2")), "an unscoped reader sees everything in order");
        check(log.batch(0, "a").events().equals(List.of("a1", "g1", "a2")), "a body's reader: its events and the shared ones");
        check(log.batch(0, "b").events().equals(List.of("b1", "g1")), "another body's reader never sees a's events");
        check(log.batch(2, "a").events().equals(List.of("g1", "a2")), "after a cursor");
        check(log.batch(0, "nobody").events().equals(List.of("g1")), "an unknown body sees only the shared events");
        check(log.batch(0, "a").latest() == 4, "the batch carries the latest sequence, not the reader's last");

        // A busy body overflows its own ring and nobody else's.
        for (int i = 0; i < 10; i++) add(log, "a", "a-n" + i, 4);
        check(log.batch(0, "a").gap(), "a lost events its ring dropped");
        check(!log.batch(0, "b").gap(), "b lost nothing: a's volume is a's own");
        check(log.batch(0, "b").events().equals(List.of("b1", "g1")), "b still has its events");
        check(log.batch(0, "a").events().size() == 4 + 1, "a keeps its newest 4, and the shared event");
        long caught = log.latest();
        check(!log.batch(caught, "a").gap(), "a reader at the head has lost nothing");
        check(log.batch(0, null).gap(), "an unscoped reader reads every ring and sees a's gap");

        // Rings of bodies that left are dropped after a while, never at once (their last acks are still being read).
        var swept = new EventLog<String>(4);
        add(swept, "gone", "bye", 100);
        add(swept, "here", "hi", 100);
        swept.sweep(150, Set.of("here"), 100);
        check(swept.batch(0, "gone").events().isEmpty() == false, "a ring lingers after its body left");
        swept.sweep(300, Set.of("here"), 100);
        check(swept.batch(0, "gone").events().isEmpty(), "and is dropped once it has been quiet long enough");
        check(swept.batch(0, "here").events().equals(List.of("hi")), "a live body's ring stays");
        swept.sweep(100_000, Set.of("here"), 100);
        check(swept.batch(0, "here").events().equals(List.of("hi")), "however quiet");
        System.out.println("EventLogTest ok");
    }
}
