package com.vulpine.mcb.body;

import java.util.List;

/**
 * Which body a spawn adds, replaces or refuses, and which one a request without a body id reaches. Pure: the
 * engines are stand-ins with a name and a live flag.
 */
public final class BodyRegistryTest {
    /** A stand-in for one body's runtime. */
    private static final class Engine {
        final String name; boolean live = true;
        Engine(String name) { this.name = name; }
    }

    private static void check(boolean yes, String label) { if (!yes) throw new AssertionError(label); }

    private static BodyRegistry<Engine> registry() { return new BodyRegistry<>(e -> e.name, e -> e.live); }

    private static String refusal(Runnable work) {
        try { work.run(); return null; } catch (IllegalStateException | IllegalArgumentException error) { return error.getMessage(); }
    }

    /** Admit a spawn the way the hub does: decide, then bind the engine it names (or a new one). */
    private static Engine spawn(BodyRegistry<Engine> r, String id, String name, boolean coexist, boolean replace) {
        var decision = r.admitSpawn(id, name, coexist, replace);
        var engine = decision.target() != null ? decision.target() : new Engine(name);
        // a replaced body keeps its engine but takes the new name, as a respawn under a new identity does
        r.bind(id, engine, coexist);
        engine.live = true;
        return engine;
    }

    public static void main(String[] args) {
        // --- one legacy body, exactly as before
        var r = registry();
        check(r.first() == null && r.size() == 0 && r.get("a") == null, "empty");
        var lab = spawn(r, "body-1", "Lab1", false, false);
        check(r.get("body-1") == lab && r.first() == lab && r.size() == 1, "a first spawn adds the body");
        check("body_already_active".equals(refusal(() -> r.admitSpawn("body-2", "Lab2", false, false))),
                "a live legacy body refuses another legacy spawn");
        var again = r.admitSpawn("body-2", "Lab2", false, true);
        check(again.target() == lab, "replace reuses the legacy engine, whatever the new id");
        r.bind("body-2", lab, false);
        check(r.get("body-1") == null && r.get("body-2") == lab && r.size() == 1, "rekeyed, not duplicated");
        lab.live = false;
        check(r.admitSpawn("body-3", "Lab3", false, false).target() == lab, "a despawned legacy body is spawned over");

        // --- bodies that share the server
        var shared = registry();
        var a = spawn(shared, "MCBot", "MCBot", true, false);
        var b = spawn(shared, "MCBot2", "MCBot2", true, false);
        check(shared.size() == 2 && shared.get("MCBot") == a && shared.get("MCBot2") == b, "a new id adds a body");
        check(shared.first() == a && shared.all().equals(List.of(a, b)), "the first body is the default, in spawn order");
        check("body_already_active".equals(refusal(() -> shared.admitSpawn("MCBot2", "MCBot2", true, false))),
                "the same id, live, needs replace");
        check(shared.admitSpawn("MCBot2", "MCBot2", true, true).target() == b, "replace the same id in place");
        check("name_in_use".equals(refusal(() -> shared.admitSpawn("other", "mcbot2", true, false))),
                "a name is one body's, whatever its case");
        // the same bot back under a fresh identity (a new session): replace takes over its engine
        var back = shared.admitSpawn("session-2", "MCBot2", true, true);
        check(back.target() == b, "replace by name finds the bot's engine");
        shared.bind("session-2", b, true);
        check(shared.get("MCBot2") == null && shared.get("session-2") == b && shared.size() == 2, "rekeyed by name");
        check(shared.first() == a, "rekeying keeps the order");
        check("name_in_use".equals(refusal(() -> shared.admitSpawn("MCBot", "MCBot2", true, true))),
                "an id and a name that belong to two bodies is a conflict, not a takeover");

        // --- legacy and shared bodies do not touch each other
        var mixed = registry();
        var shared1 = spawn(mixed, "MCBot", "MCBot", true, false);
        var legacy = spawn(mixed, "body-1", "Lab1", false, false);
        check(mixed.size() == 2, "a legacy spawn leaves a shared body alone");
        var replaced = mixed.admitSpawn("body-9", "Lab9", false, true);
        check(replaced.target() == legacy, "a legacy replace takes the legacy body, never the shared one");
        check("name_in_use".equals(refusal(() -> mixed.admitSpawn("body-9", "MCBot", false, true))),
                "a legacy body cannot take a shared body's name");
        check("body_id_in_use".equals(refusal(() -> mixed.admitSpawn("MCBot", "Lab9", false, true))),
                "a legacy spawn cannot take a shared body's id");
        check(shared1.live, "the shared body is untouched");

        // --- removal and the cap
        mixed.remove("MCBot");
        check(mixed.get("MCBot") == null && mixed.size() == 1 && mixed.first() == legacy, "removed");
        mixed.remove("missing");
        var full = registry();
        for (int i = 0; i < BodyRegistry.MAX_BODIES; i++) spawn(full, "id" + i, "Bot" + i, true, false);
        check("too_many_bodies".equals(refusal(() -> full.admitSpawn("extra", "Extra", true, false))), "the cap");
        check(full.admitSpawn("id3", "Bot3", true, true).target() != null, "replacing at the cap is allowed");
        check(full.idOf(full.get("id5")).equals("id5") && full.idOf(new Engine("x")) == null, "idOf");
        System.out.println("BodyRegistryTest ok");
    }
}
