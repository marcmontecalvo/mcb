package com.vulpine.mcb.body;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * The bridge's event ring, one ring per body plus one for events no body owns (arena, chat, commands), all numbered
 * from one sequence. A reader that names a body gets that body's events and the shared ones, merged in order; one that
 * names none gets everything, as the bridge always served. A busy body overflows only its own ring, so its volume can
 * never make another body's reader lose an event ("gap"). Pure and generic: the events are opaque.
 */
final class EventLog<E> {
    /** A reader's batch: what it may see after its cursor, and whether a ring it reads dropped events past the cursor. */
    record Batch<E>(boolean gap, long latest, List<E> events) {}

    private record Item<E>(long sequence, E event) {}

    private static final class Ring<E> {
        final ArrayDeque<Item<E>> items = new ArrayDeque<>();
        long evicted;   // the highest sequence this ring has dropped
        int lastTick;   // when it last took an event
    }

    private static final String SHARED = "\0shared";

    private final int capacity;
    private final Map<String, Ring<E>> rings = new HashMap<>();
    private long sequence;

    EventLog(int capacityPerRing) { this.capacity = capacityPerRing; }

    synchronized long latest() { return sequence; }

    /** The next sequence number; the event carries it, so it is taken before the event is built. */
    synchronized long reserve() { return ++sequence; }

    /** Appends to {@code scope}'s ring (a body id), or to the shared ring when {@code scope} is null. */
    synchronized void add(String scope, long number, int tick, E event) {
        var ring = rings.computeIfAbsent(scope == null ? SHARED : scope, key -> new Ring<>());
        if (ring.items.size() >= capacity) ring.evicted = ring.items.removeFirst().sequence();
        ring.items.addLast(new Item<>(number, event));
        ring.lastTick = tick;
    }

    /** Events after {@code after} for {@code body} (and the shared ones); every ring's when {@code body} is null. */
    synchronized Batch<E> batch(long after, String body) {
        var read = new ArrayList<Ring<E>>();
        if (body == null) read.addAll(rings.values());
        else {
            var shared = rings.get(SHARED);
            if (shared != null) read.add(shared);
            var own = rings.get(body);
            if (own != null) read.add(own);
        }
        boolean gap = false;
        var items = new ArrayList<Item<E>>();
        for (var ring : read) {
            if (ring.evicted > after) gap = true;
            for (var item : ring.items) if (item.sequence() > after) items.add(item);
        }
        items.sort(Comparator.comparingLong(Item::sequence));
        return new Batch<>(gap, sequence, items.stream().map(Item::event).toList());
    }

    /**
     * Drops the ring of every body that is no longer in {@code live} and has been quiet for {@code lingerTicks}: its last
     * acknowledgements (a despawn's) may still be unread when the body leaves. The shared ring is never dropped.
     */
    synchronized void sweep(int tick, Set<String> live, int lingerTicks) {
        rings.entrySet().removeIf(entry -> !entry.getKey().equals(SHARED) && !live.contains(entry.getKey())
                && tick - entry.getValue().lastTick >= lingerTicks);
    }
}
