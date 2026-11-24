package com.vulpine.mcb.body;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.function.Function;
import java.util.function.Predicate;

/**
 * The bodies the bridge holds, by body id, and the rules for adding, replacing and refusing one. Pure: it knows its
 * engines only through a name and a live flag, so the rules are verified without a server.
 *
 * <p>A body is either <em>shared</em> (spawned with {@code coexist: true}: several live at once, each under its own id
 * and player name) or <em>legacy</em> (spawned without it, as every client written for one body does: a fresh body id
 * per episode, {@code replace} to swap the one body for the next). There is at most one legacy engine, reused for
 * whichever id it is given, so a legacy client behaves exactly as it did when the bridge held one body; a shared
 * body is never touched by a legacy spawn and never blocks one.
 *
 * <p>The first body (in spawn order) is the default: a request that names no body reaches it.
 */
final class BodyRegistry<T> {
    /** More bodies than an arena has pads for a match (8 teams of 8 is the plan limit; the host needs far fewer). */
    static final int MAX_BODIES = 16;

    /** What a spawn does: reuse {@code target} (null: make a new engine), then bind it under the spawn's id. */
    record Decision<T>(T target) {}

    private static final class Entry<T> {
        String id; boolean coexist; final T engine;
        Entry(String id, boolean coexist, T engine) { this.id = id; this.coexist = coexist; this.engine = engine; }
    }

    private final List<Entry<T>> entries = new ArrayList<>();
    private final Function<T, String> nameOf;
    private final Predicate<T> liveOf;

    BodyRegistry(Function<T, String> nameOf, Predicate<T> liveOf) {
        this.nameOf = nameOf;
        this.liveOf = liveOf;
    }

    synchronized T get(String id) {
        var entry = entry(id);
        return entry == null ? null : entry.engine;
    }

    /** The default body: the one that has been here longest. */
    synchronized T first() { return entries.isEmpty() ? null : entries.get(0).engine; }

    synchronized List<T> all() { return entries.stream().map(entry -> entry.engine).toList(); }

    synchronized int size() { return entries.size(); }

    synchronized String idOf(T engine) {
        for (var entry : entries) if (entry.engine == engine) return entry.id;
        return null;
    }

    synchronized boolean coexists(String id) {
        var entry = entry(id);
        return entry != null && entry.coexist;
    }

    /**
     * Decides a spawn. Throws {@link IllegalStateException} with the reason the bridge reports:
     * {@code body_already_active} (a live body needs {@code replace}), {@code name_in_use}, {@code body_id_in_use},
     * {@code too_many_bodies}. Changes nothing: {@link #bind} does, once the engine has accepted the spawn.
     */
    synchronized Decision<T> admitSpawn(String bodyId, String name, boolean coexist, boolean replace) {
        var byId = entry(bodyId);
        var byName = entries.stream().filter(entry -> sameName(nameOf.apply(entry.engine), name)
                && entry != byId).findFirst().orElse(null);
        if (!coexist) {
            var legacy = entries.stream().filter(entry -> !entry.coexist).findFirst().orElse(null);
            if (legacy != null && liveOf.test(legacy.engine) && !replace) throw new IllegalStateException("body_already_active");
            if (byId != null && byId != legacy) throw new IllegalStateException("body_id_in_use");
            if (byName != null && byName != legacy) throw new IllegalStateException("name_in_use");
            if (legacy == null && entries.size() >= MAX_BODIES) throw new IllegalStateException("too_many_bodies");
            return new Decision<>(legacy == null ? null : legacy.engine);
        }
        var target = byId;
        if (byName != null) {
            // The same bot back under a new identity (a new session) replaces its own body; anything else is a clash.
            if (!replace || target != null) throw new IllegalStateException("name_in_use");
            target = byName;
        }
        if (target != null && liveOf.test(target.engine) && !replace) throw new IllegalStateException("body_already_active");
        if (target == null && entries.size() >= MAX_BODIES) throw new IllegalStateException("too_many_bodies");
        return new Decision<>(target == null ? null : target.engine);
    }

    /** Registers {@code engine} under {@code bodyId}; an engine already here is rekeyed and keeps its place in the order. */
    synchronized void bind(String bodyId, T engine, boolean coexist) {
        for (var entry : entries) {
            if (entry.engine == engine) { entry.id = bodyId; entry.coexist = coexist; return; }
        }
        entries.add(new Entry<>(bodyId, coexist, engine));
    }

    synchronized void remove(String bodyId) { entries.removeIf(entry -> entry.id.equals(bodyId)); }

    private Entry<T> entry(String id) {
        for (var entry : entries) if (entry.id.equals(id)) return entry;
        return null;
    }

    /** Minecraft finds players by name without regard to case, so two bodies may not differ only by it. */
    private static boolean sameName(String a, String b) {
        return a != null && b != null && a.toLowerCase(Locale.ROOT).equals(b.toLowerCase(Locale.ROOT));
    }
}
