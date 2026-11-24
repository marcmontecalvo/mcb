package com.vulpine.mcb.body;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.vulpine.mcb.arena.ArenaBox;
import com.vulpine.mcb.arena.ArenaCursor;
import com.vulpine.mcb.arena.ArenaFail;
import com.vulpine.mcb.arena.ArenaLogic;
import com.vulpine.mcb.arena.ArenaPlan;
import com.vulpine.mcb.arena.ArenaRecord;
import com.vulpine.mcb.arena.ArenaRegistry;
import com.vulpine.mcb.arena.ArenaRequests;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** The arena runtime's pure rules: plan, bounds, protection, waves, teams, registry, build cursor, requests. */
public final class ArenaLogicTest {
    private static void check(boolean yes, String label) { if (!yes) throw new AssertionError(label); }

    private static JsonObject json(String text) { return JsonParser.parseString(text).getAsJsonObject(); }

    private static void rejects(Runnable run, String reason, String label) {
        try { run.run(); }
        catch (ArenaFail fail) { check(fail.reason.equals(reason), label + " -> " + fail.getMessage()); return; }
        throw new AssertionError("accepted: " + label);
    }

    static final String PLAN = "{\"schema\":1,\"name\":\"vault\",\"size\":[8,6,8],"
            + "\"fills\":[{\"from\":[0,0,0],\"to\":[7,5,7],\"block\":\"minecraft:air\"},"
            + "{\"from\":[7,0,7],\"to\":[0,0,0],\"block\":\"minecraft:stone_bricks\"},"
            + "{\"from\":[3,5,3],\"to\":[3,5,3],\"block\":\"minecraft:lantern[hanging=true]\"}],"
            + "\"markers\":{\"teams\":{\"red\":{\"pads\":[[1,1,1],[2,1,1]],\"entry\":[0,1,1]},"
            + "\"blue\":{\"pads\":[[6,1,6]],\"entry\":[7,1,6]}},"
            + "\"mob_spawns\":[[1,1,6],[6,1,1]],\"spectator\":[4,5,4]},"
            + "\"capacity\":{\"teams\":2,\"per_team\":10,\"mobs\":15}}";

    public static void main(String[] args) throws Exception {
        box();
        plan();
        protection();
        derived();
        clock();
        game();
        tally();
        misc();
        cursor();
        registry();
        requests();
        spawn();
        System.out.println("ArenaLogicTest ok");
    }

    private static void spawn() {
        // Outside adventure mode the game lifts a joining player to the top block above the world spawn: in the void
        // world that is the lobby's roof. A spawn inside a built arena (the lobby) is kept exactly.
        check(ArenaLogic.keepsSpawnExact("built"), "a spawn inside a built arena is kept exactly");
        check(!ArenaLogic.keepsSpawnExact(null), "a spawn outside every arena is the game's own");
        check(!ArenaLogic.keepsSpawnExact("building") && !ArenaLogic.keepsSpawnExact("incomplete")
                && !ArenaLogic.keepsSpawnExact("clearing"), "a half-built or clearing arena is no room to start in");
        // A player on the death screen is neither kitted, healed nor sent to a pad: healed, the game took them for
        // alive and ignored their respawn click; their team was out before the match began.
        ArenaLogic.requireAlive("Ann", false);
        rejects(() -> ArenaLogic.requireAlive("Ann", true), "participant_dead", "a dead participant is refused");
    }

    private static void box() {
        var box = ArenaBox.of(new int[] {10, 60, 10}, new int[] {5, 4, 5}); // 10..14, 60..63, 10..14
        check(box.contains(10, 60, 10) && box.contains(14, 63, 14), "both corner cells are inside");
        check(!box.contains(15, 60, 10) && !box.contains(9, 60, 10) && !box.contains(10, 64, 10)
                && !box.contains(10, 59, 10) && !box.contains(10, 60, 15) && !box.contains(10, 60, 9), "one past is outside");
        check(box.containsPoint(14.99, 63.5, 10.0) && !box.containsPoint(15.0, 61, 11) && !box.containsPoint(9.99, 61, 11),
                "an entity point is inside up to the far face of the last cell");
        check(box.inflate(2).contains(8, 58, 8) && !box.inflate(2).contains(7, 58, 8), "inflate grows every side");
        check(box.volume() == 100, "5 x 4 x 5");
        check(box.intersects(ArenaBox.of(new int[] {14, 63, 14}, new int[] {3, 3, 3})), "sharing a corner cell overlaps");
        check(!box.intersects(ArenaBox.of(new int[] {15, 60, 10}, new int[] {3, 3, 3})), "touching faces do not overlap");
        check(!box.intersects(ArenaBox.of(new int[] {10, 64, 10}, new int[] {3, 3, 3})), "above does not overlap");
        // chunks covered: x 10..14 is chunk 0; a box over x 14..17 spans chunks 0 and 1
        var wide = ArenaBox.of(new int[] {14, 0, -2}, new int[] {4, 2, 3});  // x 14..17, z -2..0
        var chunks = wide.chunks();
        check(chunks.length == 4, "2 x 2 chunks: " + chunks.length);
        check(java.util.Arrays.stream(chunks).anyMatch(c -> c == ArenaBox.chunkKey(1, -1)), "chunk (1,-1) is covered");
        check(ArenaBox.chunkKey(1, -1) == ((1L & 0xFFFFFFFFL) | ((-1L & 0xFFFFFFFFL) << 32)), "same packing as ChunkPos.asLong");
        check(ArenaBox.of(new int[] {0, 0, 0}, new int[] {1, 1, 1}).chunks().length == 1, "one cell, one chunk");
        // world height
        ArenaLogic.checkHeight(ArenaBox.of(new int[] {0, -64, 0}, new int[] {1, 384, 1}), -64, 319);
        rejects(() -> ArenaLogic.checkHeight(ArenaBox.of(new int[] {0, -65, 0}, new int[] {1, 2, 1}), -64, 319),
                "out_of_world", "below the world");
        rejects(() -> ArenaLogic.checkHeight(ArenaBox.of(new int[] {0, 300, 0}, new int[] {1, 21, 1}), -64, 319),
                "out_of_world", "above the world");
        rejects(() -> ArenaLogic.checkHeight(ArenaBox.of(new int[] {30_000_000, 0, 0}, new int[] {1, 1, 1}), -64, 319),
                "out_of_world", "past the world border");
    }

    private static void plan() {
        var plan = ArenaPlan.parse(json(PLAN));
        check(plan.name().equals("vault") && plan.sx() == 8 && plan.sy() == 6 && plan.sz() == 8, "name and size");
        check(plan.fills().size() == 3 && plan.fills().get(0).block().equals("minecraft:air"), "fills in order");
        var flipped = plan.fills().get(1);
        check(flipped.min()[0] == 0 && flipped.max()[0] == 7 && flipped.min()[2] == 0, "from and to are normalised to min and max");
        check(plan.fills().get(2).block().equals("minecraft:lantern[hanging=true]"), "a block state stays in the string");
        check(plan.teams().keySet().equals(java.util.Set.of("red", "blue")), "two teams");
        check(plan.teams().get("red").pads().size() == 2 && plan.teams().get("red").entry()[0] == 0, "red pads and entry");
        check(plan.mobSpawns().size() == 2 && plan.spectator()[1] == 5, "mob spawns and spectator");
        check(plan.capacity().teams() == 2 && plan.capacity().perTeam() == 10 && plan.capacity().mobs() == 15, "capacity");
        check(plan.totalCells() == 8 * 6 * 8 + 8 * 8 + 1, "cells to place: " + plan.totalCells());
        check(plan.box(new int[] {100, 70, -20}).contains(107, 75, -13), "the plan's box at an origin");

        var minimal = ArenaPlan.parse(json("{\"schema\":1,\"name\":\"a\",\"size\":[2,2,2],\"fills\":[]}"));
        check(minimal.teams().isEmpty() && minimal.mobSpawns().isEmpty() && minimal.spectator() == null, "markers optional");
        check(minimal.capacity().mobs() == 64 && minimal.capacity().teams() == 0, "capacity defaults");
        var derived = ArenaPlan.parse(json("{\"schema\":1,\"name\":\"a\",\"size\":[9,9,9],\"fills\":[],"
                + "\"markers\":{\"teams\":{\"x\":{\"pads\":[[1,1,1],[2,1,1],[3,1,1]]},\"y\":{\"pads\":[[5,1,5]]}}}}"));
        check(derived.capacity().teams() == 2 && derived.capacity().perTeam() == 3, "capacity from the teams when absent");
        check(derived.teams().get("x").entry() == null, "entry is optional");
        check(ArenaPlan.parse(json("{\"schema\":1,\"name\":\"a\",\"size\":[2,2,2],\"fills\":[],\"description\":\"x\"}")) != null,
                "unknown top-level keys such as a description are ignored");

        String ok = "{\"schema\":1,\"name\":\"a\",\"size\":[4,4,4],\"fills\":[";
        rejects(() -> ArenaPlan.parse(json("{\"schema\":2,\"name\":\"a\",\"size\":[2,2,2],\"fills\":[]}")), "bad_plan", "schema 2");
        rejects(() -> ArenaPlan.parse(json("{\"name\":\"a\",\"size\":[2,2,2],\"fills\":[]}")), "bad_plan", "no schema");
        rejects(() -> ArenaPlan.parse(json("{\"schema\":1,\"name\":\"Vault X\",\"size\":[2,2,2],\"fills\":[]}")), "bad_plan", "name chars");
        rejects(() -> ArenaPlan.parse(json("{\"schema\":1,\"size\":[2,2,2],\"fills\":[]}")), "bad_plan", "no name");
        rejects(() -> ArenaPlan.parse(json("{\"schema\":1,\"name\":\"a\",\"size\":[0,2,2],\"fills\":[]}")), "bad_plan", "zero size");
        rejects(() -> ArenaPlan.parse(json("{\"schema\":1,\"name\":\"a\",\"size\":[257,2,2],\"fills\":[]}")), "bad_plan", "size over 256");
        rejects(() -> ArenaPlan.parse(json("{\"schema\":1,\"name\":\"a\",\"size\":[2,2],\"fills\":[]}")), "bad_plan", "size arity");
        rejects(() -> ArenaPlan.parse(json("{\"schema\":1,\"name\":\"a\",\"size\":[2.5,2,2],\"fills\":[]}")), "bad_plan", "fractional size");
        rejects(() -> ArenaPlan.parse(json("{\"schema\":1,\"name\":\"a\",\"size\":[2,2,2]}")), "bad_plan", "no fills");
        rejects(() -> ArenaPlan.parse(json(ok + "{\"from\":[0,0,0],\"to\":[4,0,0],\"block\":\"minecraft:stone\"}]}")),
                "bad_plan", "a fill past the size");
        rejects(() -> ArenaPlan.parse(json(ok + "{\"from\":[-1,0,0],\"to\":[1,0,0],\"block\":\"minecraft:stone\"}]}")),
                "bad_plan", "a fill before the min corner");
        rejects(() -> ArenaPlan.parse(json(ok + "{\"from\":[0,0,0],\"to\":[1,0,0],\"block\":\"stone\"}]}")),
                "bad_plan", "a block id needs a namespace");
        rejects(() -> ArenaPlan.parse(json(ok + "{\"from\":[0,0,0],\"to\":[1,0,0],\"block\":\"minecraft:chest{Items:[]}\"}]}")),
                "bad_plan", "block entity data is refused");
        rejects(() -> ArenaPlan.parse(json(ok + "{\"from\":[0,0,0],\"to\":[1,0,0],\"block\":\"minecraft:stone;say hi\"}]}")),
                "bad_plan", "no command text in a block string");
        rejects(() -> ArenaPlan.parse(json(ok + "{\"from\":[0,0,0],\"to\":[1,0,0]}]}")), "bad_plan", "a fill needs a block");
        rejects(() -> ArenaPlan.parse(json(ok + "{\"from\":[0,0],\"to\":[1,0,0],\"block\":\"minecraft:stone\"}]}")), "bad_plan", "fill arity");
        rejects(() -> ArenaPlan.parse(json(ok + "]," + "\"markers\":{\"mob_spawns\":[[9,0,0]]}}")), "bad_plan", "a spawn outside");
        rejects(() -> ArenaPlan.parse(json(ok + "]," + "\"markers\":{\"spectator\":[0,9,0]}}")), "bad_plan", "a spectator outside");
        rejects(() -> ArenaPlan.parse(json(ok + "]," + "\"markers\":{\"teams\":{\"r\":{\"pads\":[]}}}}")), "bad_plan", "a team needs a pad");
        rejects(() -> ArenaPlan.parse(json(ok + "]," + "\"markers\":{\"teams\":{\"R!\":{\"pads\":[[0,0,0]]}}}}")), "bad_plan", "team name");
        rejects(() -> ArenaPlan.parse(json(ok + "]," + "\"markers\":{\"teams\":{\"r\":{\"pads\":[[0,0,9]]}}}}")), "bad_plan", "a pad outside");
        rejects(() -> ArenaPlan.parse(json(ok + "]," + "\"markers\":{\"teams\":{\"r\":{\"pads\":[[0,0,0]],\"entry\":[9,9,9]}}}}")),
                "bad_plan", "an entry outside");
        rejects(() -> ArenaPlan.parse(json(ok + "]," + "\"capacity\":{\"mobs\":100000}}")), "bad_plan", "capacity range");
        rejects(() -> ArenaPlan.parse(json(ok + "]," + "\"capacity\":{\"mobs\":\"many\"}}")), "bad_plan", "capacity type");
        // volume cap: a fill list that would place more than the cap is refused before anything is placed
        var many = new StringBuilder("{\"schema\":1,\"name\":\"big\",\"size\":[256,256,256],\"fills\":[");
        for (int i = 0; i < 3; i++) many.append(i == 0 ? "" : ",")
                .append("{\"from\":[0,0,0],\"to\":[255,255,255],\"block\":\"minecraft:stone\"}");
        rejects(() -> ArenaPlan.parse(json(many + "]}")), "bad_plan", "too many cells");
    }

    private static void protection() {
        for (var id : List.of("minecraft:bucket", "minecraft:water_bucket", "minecraft:lava_bucket", "minecraft:powder_snow_bucket",
                "minecraft:pufferfish_bucket", "minecraft:flint_and_steel", "minecraft:fire_charge", "minecraft:bone_meal"))
            check(ArenaLogic.restrictedItem(id), id + " is restricted in an arena");
        for (var id : List.of("minecraft:milk_bucket", "minecraft:stick", "minecraft:iron_sword", "minecraft:bread",
                "minecraft:crossbow", "minecraft:bonemeal_extra", "minecraft:shield"))
            check(!ArenaLogic.restrictedItem(id), id + " is free");
        for (var id : List.of("minecraft:oak_boat", "minecraft:bamboo_raft", "minecraft:minecart", "minecraft:tnt_minecart",
                "minecraft:armor_stand", "minecraft:end_crystal"))
            check(ArenaLogic.entityItem(id), id + " puts an entity in the arena");
        for (var id : List.of("minecraft:stick", "minecraft:boat_part", "minecraft:minecart_rail", "minecraft:crystal"))
            check(!ArenaLogic.entityItem(id), id + " is no entity item");
        check(ArenaLogic.INTERACT_MARGIN >= 4, "an item used near the walls counts as used on the arena");
    }

    private static void derived() {
        check(ArenaLogic.derived("MOB_SUMMONED", "minecraft:vex", "minecraft:evoker"), "a vex from an evoker");
        check(!ArenaLogic.derived("MOB_SUMMONED", "minecraft:vex", "minecraft:zombie"), "a vex only from an evoker");
        check(!ArenaLogic.derived("NATURAL", "minecraft:vex", "minecraft:evoker"), "a natural spawn is never derived");
        check(!ArenaLogic.derived("COMMAND", "minecraft:vex", "minecraft:evoker"), "a command spawn is never derived");
        check(!ArenaLogic.derived("SPAWN_ITEM_USE", "minecraft:vex", "minecraft:evoker"), "a spawn egg is never derived");
        check(ArenaLogic.derived("REINFORCEMENT", "minecraft:zombie", "minecraft:husk"), "zombie reinforcement near a husk");
        check(ArenaLogic.derived("REINFORCEMENT", "minecraft:zombie", "minecraft:zombie"), "zombie reinforcement near a zombie");
        check(!ArenaLogic.derived("REINFORCEMENT", "minecraft:zombie", "minecraft:skeleton"), "reinforcements come from zombies");
        check(ArenaLogic.derived("TRIGGERED", "minecraft:slime", "minecraft:slime"), "a split slime");
        check(ArenaLogic.derived("TRIGGERED", "minecraft:magma_cube", "minecraft:magma_cube"), "a split magma cube");
        check(!ArenaLogic.derived("TRIGGERED", "minecraft:slime", "minecraft:magma_cube"), "slimes split into slimes");
        check(ArenaLogic.derived("EVENT", "minecraft:pillager", "minecraft:ravager"), "a raid joins a wave of raiders");
        check(ArenaLogic.derived("PATROL", "minecraft:pillager", "minecraft:evoker"), "a patrol captain joins");
        check(!ArenaLogic.derived("EVENT", "minecraft:zombie", "minecraft:zombie"), "a siege zombie is no raider");
        check(ArenaLogic.derived("JOCKEY", "minecraft:skeleton", "minecraft:spider"), "a spider's skeleton rider");
        check(ArenaLogic.derived("JOCKEY", "minecraft:chicken", "minecraft:husk"), "a chicken jockey");
        check(ArenaLogic.DERIVED_RANGE >= 16, "the parent is looked for near the child");
        check(ArenaLogic.rule("minecraft:vex") != null && ArenaLogic.rule("minecraft:cow") == null, "rule lookup by child type");
    }

    private static void clock() {
        var clock = new ArenaLogic.WaveClock(List.of(3, 2));
        check(clock.waves() == 2 && clock.nextIn() == 3 && clock.index() == 0, "waiting for wave 0");
        check(clock.tick(0, true).kind() == ArenaLogic.Kind.NONE && clock.nextIn() == 2, "delay ticks down");
        check(clock.tick(0, true).kind() == ArenaLogic.Kind.NONE, "still waiting");
        var spawn = clock.tick(0, true);
        check(spawn.kind() == ArenaLogic.Kind.SPAWN && spawn.wave() == 0, "wave 0 spawns when its delay is up");
        check(clock.tick(5, true).kind() == ArenaLogic.Kind.NONE, "a live mob keeps the wave going");
        check(clock.tick(1, true).kind() == ArenaLogic.Kind.NONE, "even the last one");
        var cleared = clock.tick(0, true);
        check(cleared.kind() == ArenaLogic.Kind.CLEARED && cleared.wave() == 0, "the last death clears the wave");
        check(clock.index() == 1 && clock.nextIn() == 2, "wave 1 waits its own delay from the clear");
        check(clock.tick(0, true).kind() == ArenaLogic.Kind.NONE, "delay 2: one");
        check(clock.tick(0, true).kind() == ArenaLogic.Kind.SPAWN, "delay 2: two, spawn wave 1");
        check(clock.tick(0, true).kind() == ArenaLogic.Kind.CLEARED, "an empty wave is cleared at once");
        var won = clock.tick(0, true);
        check(won.kind() == ArenaLogic.Kind.WON && clock.finished(), "the last wave cleared wins");
        check(clock.tick(3, false).kind() == ArenaLogic.Kind.NONE, "nothing after the end");

        var zero = new ArenaLogic.WaveClock(List.of(0));
        check(zero.tick(0, true).kind() == ArenaLogic.Kind.SPAWN, "delay 0 spawns on the first tick");

        var lost = new ArenaLogic.WaveClock(List.of(5));
        check(lost.tick(0, false).kind() == ArenaLogic.Kind.LOST && lost.finished(), "everyone down while waiting loses");
        var lost2 = new ArenaLogic.WaveClock(List.of(0, 0));
        lost2.tick(0, true);
        check(lost2.tick(4, false).kind() == ArenaLogic.Kind.LOST, "everyone down mid-wave loses");
        check(lost2.tick(0, true).kind() == ArenaLogic.Kind.NONE, "a lost match stays lost");

        var both = new ArenaLogic.WaveClock(List.of(0));
        both.tick(0, true);
        check(both.tick(0, false).kind() == ArenaLogic.Kind.LOST, "the last mob and the last participant dying together is a loss");
        rejects(() -> new ArenaLogic.WaveClock(List.of()), "bad_request", "no waves");
        rejects(() -> new ArenaLogic.WaveClock(List.of(-1)), "bad_request", "negative delay");
    }

    private static Map<String, Integer> sides(int red, int blue) {
        var out = new LinkedHashMap<String, Integer>();
        out.put("red", red); out.put("blue", blue);
        return out;
    }

    private static ArenaLogic.Step step(ArenaLogic.Kind kind) { return new ArenaLogic.Step(kind, 0); }

    /** How each game ends: co-op waves, team against team (waves or not), and the kill race. */
    private static void game() {
        check(ArenaLogic.MODES.equals(java.util.Set.of("waves", "teams", "race")), "three games");
        check(!ArenaLogic.sided("waves") && ArenaLogic.sided("teams") && ArenaLogic.sided("race"), "teams and the race start on team pads");
        check(ArenaLogic.pvp("teams") && !ArenaLogic.pvp("waves") && !ArenaLogic.pvp("race"),
                "players hurt each other only team against team");

        var none = sides(0, 0);
        check(ArenaLogic.decide("waves", step(ArenaLogic.Kind.SPAWN), none, none) == null, "co-op: a wave spawning plays on");
        var won = ArenaLogic.decide("waves", step(ArenaLogic.Kind.WON), none, none);
        check(won.won() && won.winner() == null && won.reason() == null, "co-op: the last wave cleared wins");
        var lost = ArenaLogic.decide("waves", step(ArenaLogic.Kind.LOST), none, none);
        check(!lost.won() && lost.reason().equals("participants_down"), "co-op: everyone down loses");

        check(ArenaLogic.decide("teams", null, sides(1, 2), none) == null, "teams, no waves: two sides alive play on");
        var blue = ArenaLogic.decide("teams", null, sides(0, 2), none);
        check(blue.won() && blue.winner().equals("blue"), "the last team standing wins");
        var draw = ArenaLogic.decide("teams", null, sides(0, 0), none);
        check(!draw.won() && draw.reason().equals("draw") && draw.winner() == null, "nobody left is a draw");
        check(ArenaLogic.decide("teams", step(ArenaLogic.Kind.WON), sides(1, 1), none) == null,
                "teams with waves: the waves running out ends nothing while two sides stand");
        check(ArenaLogic.decide("teams", step(ArenaLogic.Kind.CLEARED), sides(3, 1), none) == null, "a wave cleared plays on");
        var mobbed = ArenaLogic.decide("teams", step(ArenaLogic.Kind.LOST), sides(0, 0), none);
        check(!mobbed.won() && mobbed.reason().equals("draw"), "the mobs taking everyone is a draw");
        check(ArenaLogic.decide("teams", step(ArenaLogic.Kind.NONE), sides(0, 1), none).winner().equals("blue"),
                "the mobs can finish a team off");

        check(ArenaLogic.decide("race", step(ArenaLogic.Kind.CLEARED), sides(1, 1), sides(4, 2)) == null, "race: plays on between waves");
        check(ArenaLogic.decide("race", step(ArenaLogic.Kind.NONE), sides(0, 1), sides(4, 2)) == null,
                "race: a team out of fighters does not end it while another kills on");
        var race = ArenaLogic.decide("race", step(ArenaLogic.Kind.WON), sides(1, 1), sides(4, 2));
        check(race.won() && race.winner().equals("red") && race.reason() == null, "race: the most kills when the waves are done");
        var tie = ArenaLogic.decide("race", step(ArenaLogic.Kind.WON), sides(1, 1), sides(3, 3));
        check(!tie.won() && tie.reason().equals("draw"), "race: level kills are a draw");
        var down = ArenaLogic.decide("race", step(ArenaLogic.Kind.LOST), sides(0, 0), sides(1, 5));
        check(down.won() && down.winner().equals("blue") && down.reason().equals("participants_down"),
                "race: everyone down ends it, the kills so far decide");
        rejects(() -> ArenaLogic.decide("duel", null, none, none), "bad_request", "an unknown game");

        check(ArenaLogic.raceWinner(sides(2, 5)).equals("blue"), "the highest tally");
        check(ArenaLogic.raceWinner(sides(0, 0)).isEmpty() && ArenaLogic.raceWinner(sides(2, 2)).isEmpty(), "level is no winner");
        var three = new LinkedHashMap<String, Integer>();
        three.put("red", 4); three.put("blue", 4); three.put("green", 1);
        check(ArenaLogic.raceWinner(three).isEmpty(), "a tie at the top is a draw, whatever is below");
    }

    /** Who killed what: mobs per player and team, and players in a team match. */
    private static void tally() {
        var teamOf = new LinkedHashMap<String, String>();
        teamOf.put("Ann", "red"); teamOf.put("Bob", "blue"); teamOf.put("Cy", "red");
        var tally = new ArenaLogic.Tally(teamOf, List.of("red", "blue"));
        check(tally.byTeam().equals(sides(0, 0)), "every team starts at nothing, in plan order");
        check(tally.mob("Ann") && tally.mob("Ann") && tally.mob("Bob") && tally.mob("Cy"), "participants' mob kills count");
        check(!tally.mob("Zed") && !tally.mob(null), "a kill by anyone else (or no one) is nobody's");
        check(tally.mobs("Ann") == 2 && tally.mobs("Bob") == 1 && tally.mobs("Zed") == 0, "per player");
        check(tally.byTeam().equals(sides(3, 1)), "per team: its players' mob kills");
        check(tally.player("Bob", "Ann"), "Bob took Ann down");
        check(!tally.player("Cy", "Ann") && !tally.player("Ann", "Ann") && !tally.player("Zed", "Ann") && !tally.player(null, "Ann"),
                "a teammate, yourself, an outsider or no one scores nothing");
        check(tally.players("Bob") == 1 && tally.score("Bob") == 2 && tally.score("Ann") == 2, "score: mobs and players");
        check(tally.byTeam().equals(sides(3, 1)), "the race counts mobs only");
        var rows = tally.rows();
        check(rows.get("Ann").get("mobs").equals(2) && rows.get("Bob").get("players").equals(1)
                && rows.get("Cy").get("team").equals("red") && rows.keySet().equals(teamOf.keySet()), "rows for the reports");

        var coop = new LinkedHashMap<String, String>();
        coop.put("Ann", ""); coop.put("MCBot", "");
        var solo = new ArenaLogic.Tally(coop, List.of());
        check(solo.mob("MCBot") && solo.byTeam().isEmpty() && !solo.rows().get("Ann").containsKey("team"), "co-op: no teams");
        check(!solo.player("Ann", "MCBot"), "co-op: no player kills");

        check(ArenaLogic.objectiveName("vault").length() <= 16 && ArenaLogic.objectiveName("vault").startsWith("mcbk_"), "objective name fits");
        check(!ArenaLogic.objectiveName("vault").equals(ArenaLogic.objectiveName("hall")), "one per arena");
    }

    private static void misc() {
        check(ArenaLogic.spawnMarker(0, 3) == 0 && ArenaLogic.spawnMarker(4, 3) == 1 && ArenaLogic.spawnMarker(5, 1) == 0,
                "mobs go round the spawn markers in turn");
        var offsets = ArenaLogic.searchOffsets();
        check(offsets.get(0)[0] == 0 && offsets.get(0)[1] == 0 && offsets.get(0)[2] == 0, "the marker itself first");
        check(offsets.size() > 20 && offsets.stream().noneMatch(o -> Math.abs(o[0]) > 2 || Math.abs(o[2]) > 2 || o[1] < 0 || o[1] > 2),
                "a few cells round and above");
        check(ArenaLogic.followRange(ArenaBox.of(new int[] {0, 0, 0}, new int[] {30, 10, 40})) == 67.0, "diagonal 50.99 -> 51, plus 16");
        check(ArenaLogic.followRange(ArenaBox.of(new int[] {0, 0, 0}, new int[] {256, 256, 256})) == 460.0, "big arena");
        check(ArenaLogic.followRange(ArenaBox.of(new int[] {0, 0, 0}, new int[] {1, 1, 1})) == 20.0, "never below 20");

        double[][] where = {{0, 0, 0}, {5, 0, 0}, {2, 0, 0}};
        check(ArenaLogic.nearest(4, 0, 0, where, new boolean[] {true, true, true}) == 1, "the closest participant");
        check(ArenaLogic.nearest(4, 0, 0, where, new boolean[] {true, false, true}) == 2, "a dead one is skipped");
        check(ArenaLogic.nearest(4, 0, 0, where, new boolean[] {false, false, false}) == -1, "nobody to target");

        var teams = ArenaLogic.assignTeams(List.of("red", "blue"), List.of("a", "b", "c", "d", "e"));
        check(teams.get("red").equals(List.of("a", "c", "e")) && teams.get("blue").equals(List.of("b", "d")), "round robin");
        rejects(() -> ArenaLogic.assignTeams(List.of("red"), List.of("a", "b")), "not_enough_teams", "one team is no match");
        rejects(() -> ArenaLogic.assignTeams(List.of("red", "blue"), List.of("a")), "not_enough_participants", "one player is no match");
        var explicit = new LinkedHashMap<String, List<String>>();
        explicit.put("red", List.of("a")); explicit.put("blue", List.of("b", "c"));
        ArenaLogic.checkTeams(explicit, java.util.Set.of("red", "blue"), Map.of("red", 2, "blue", 2), 2);
        rejects(() -> ArenaLogic.checkTeams(explicit, java.util.Set.of("red", "green"), Map.of("red", 2, "green", 2), 2),
                "unknown_team", "a team the arena lacks");
        rejects(() -> ArenaLogic.checkTeams(explicit, java.util.Set.of("red", "blue"), Map.of("red", 2, "blue", 1), 5),
                "not_enough_pads", "more members than pads");
        rejects(() -> ArenaLogic.checkTeams(explicit, java.util.Set.of("red", "blue"), Map.of("red", 2, "blue", 2), 1),
                "over_capacity", "more than per_team");
        var dup = new LinkedHashMap<String, List<String>>();
        dup.put("red", List.of("a")); dup.put("blue", List.of("a"));
        rejects(() -> ArenaLogic.checkTeams(dup, java.util.Set.of("red", "blue"), Map.of("red", 2, "blue", 2), 2),
                "duplicate_participant", "one player on two teams");
        var lonely = new LinkedHashMap<String, List<String>>();
        lonely.put("red", List.of("a", "b")); lonely.put("blue", List.of());
        rejects(() -> ArenaLogic.checkTeams(lonely, java.util.Set.of("red", "blue"), Map.of("red", 2, "blue", 2), 2),
                "not_enough_teams", "a team with nobody");

        // a waves match puts its players on pads, all one side: each team's pads in plan order
        var padCounts = new LinkedHashMap<String, Integer>();
        padCounts.put("red", 2); padCounts.put("blue", 2);
        check(ArenaLogic.wavePads(padCounts, 3, 8).equals(List.of(new ArenaLogic.Pad("red", 0),
                new ArenaLogic.Pad("red", 1), new ArenaLogic.Pad("blue", 0))), "red's pads, then blue's");
        check(ArenaLogic.wavePads(padCounts, 1, 8).equals(List.of(new ArenaLogic.Pad("red", 0))), "one player, the first pad");
        rejects(() -> ArenaLogic.wavePads(padCounts, 5, 8), "not_enough_pads", "more players than pads");
        rejects(() -> ArenaLogic.wavePads(padCounts, 3, 2), "over_capacity", "more players than the arena takes");
        rejects(() -> ArenaLogic.wavePads(padCounts, 0, 8), "no_participants", "nobody to place");

        check(ArenaLogic.teamResult(Map.of("red", 2, "blue", 1)) == null, "two teams alive: play on");
        check(ArenaLogic.teamResult(Map.of("red", 0, "blue", 3)).equals("blue"), "one team left wins");
        check(ArenaLogic.teamResult(Map.of("red", 0, "blue", 0)).isEmpty(), "none left is a draw");
        check(ArenaLogic.teamResult(new LinkedHashMap<>(Map.of("red", 1, "blue", 0, "green", 0))).equals("red"), "three teams, one left");

        check(ArenaLogic.teamName("vault", "red").length() <= 16 && ArenaLogic.teamName("a-very-long-arena", "crimson").length() <= 16,
                "scoreboard team names fit");
        check(!ArenaLogic.teamName("vault", "red").equals(ArenaLogic.teamName("vault", "blue")), "one per team");
        check(!ArenaLogic.teamName("vault", "red").equals(ArenaLogic.teamName("vaulx", "red")), "one per arena");
        check(ArenaLogic.waveTag("vault").equals("mcb_arena:vault"), "the wave tag the spec names");
        check("vault".equals(ArenaLogic.arenaOfTag("mcb_arena:vault")) && ArenaLogic.arenaOfTag("mcb_trial") == null
                && ArenaLogic.arenaOfTag("mcb_arena:") == null, "tag parsing");
        check(ArenaLogic.percent(0, 0) == 100 && ArenaLogic.percent(1, 4) == 25 && ArenaLogic.percent(4, 4) == 100, "progress percent");
        check(ArenaLogic.isOperationName("arena_build") && ArenaLogic.isOperationName("arena_stop")
                && !ArenaLogic.isOperationName("trial") && !ArenaLogic.isOperationName("arena_bogus"), "the ops the runtime owns");
        check(ArenaLogic.isOperationName("arena_spawn") && ArenaLogic.isOperationName("arena_op")
                && ArenaLogic.isOperationName("arena_kit"), "the world's spawn, ops and kits");
        check(ArenaLogic.OPS.size() == 9, "nine ops");
    }

    private static void cursor() {
        var plan = ArenaPlan.parse(json("{\"schema\":1,\"name\":\"c\",\"size\":[3,2,2],\"fills\":["
                + "{\"from\":[0,0,0],\"to\":[2,1,1],\"block\":\"minecraft:air\"},"
                + "{\"from\":[1,0,0],\"to\":[1,0,0],\"block\":\"minecraft:stone\"}]}"));
        var cursor = new ArenaCursor(plan.fills());
        check(cursor.total() == 13 && cursor.placed() == 0 && !cursor.done(), "13 cells to place");
        var seen = new ArrayList<int[]>();
        int n = cursor.next(5, (x, y, z, fill) -> seen.add(new int[] {x, y, z, fill}));
        check(n == 5 && cursor.placed() == 5, "a batch of five");
        check(java.util.Arrays.equals(seen.get(0), new int[] {0, 0, 0, 0}) && java.util.Arrays.equals(seen.get(1), new int[] {1, 0, 0, 0})
                && java.util.Arrays.equals(seen.get(3), new int[] {0, 0, 1, 0}), "x runs fastest, then z");
        n = cursor.next(100, (x, y, z, fill) -> seen.add(new int[] {x, y, z, fill}));
        check(n == 8 && cursor.done() && cursor.placed() == 13, "the rest, no more than are left");
        check(seen.get(6)[1] == 1, "a layer at a time, bottom up");
        check(java.util.Arrays.equals(seen.get(12), new int[] {1, 0, 0, 1}), "the second fill comes after the first, tagged");
        check(cursor.next(5, (x, y, z, fill) -> { throw new AssertionError("nothing left"); }) == 0, "a done cursor yields nothing");
        check(ArenaLogic.percent(cursor.placed(), cursor.total()) == 100, "progress at the end");
        check(new ArenaCursor(List.of()).done() && new ArenaCursor(List.of()).total() == 0, "an empty plan is done");
        var cursor2 = new ArenaCursor(plan.fills());
        int[] counter = {0};
        while (!cursor2.done()) cursor2.next(1, (x, y, z, fill) -> counter[0]++);
        check(counter[0] == 13, "one at a time reaches the same total");
    }

    private static ArenaRecord record(String name, int[] origin, int[] size) {
        var plan = ArenaPlan.parse(json(PLAN));
        var record = ArenaRecord.of(plan, name, "minecraft:overworld", origin);
        check(record.size()[0] == plan.sx(), "size from the plan");
        return record;
    }

    private static void registry() throws Exception {
        var dir = Files.createTempDirectory("arena-registry");
        var file = dir.resolve("mcb_arenas.json");
        var registry = ArenaRegistry.load(file);
        check(registry.all().isEmpty(), "a missing file is an empty registry");
        var a = record("a", new int[] {0, 64, 0}, new int[] {8, 6, 8});
        registry.add(a);
        rejects(() -> registry.add(record("a", new int[] {500, 64, 0}, new int[] {8, 6, 8})), "name_exists", "same name twice");
        rejects(() -> registry.add(record("b", new int[] {7, 64, 7}, new int[] {8, 6, 8})), "overlap", "overlapping another arena");
        check(registry.overlapping("minecraft:overworld", ArenaBox.of(new int[] {7, 64, 7}, new int[] {2, 2, 2})).equals("a"), "overlap names it");
        check(registry.overlapping("minecraft:the_nether", ArenaBox.of(new int[] {7, 64, 7}, new int[] {2, 2, 2})) == null,
                "another dimension never overlaps");
        check(registry.overlapping("minecraft:overworld", ArenaBox.of(new int[] {8, 64, 0}, new int[] {2, 2, 2})) == null, "next to it is fine");
        var c = record("c", new int[] {20, 64, 0}, new int[] {8, 6, 8});
        registry.add(c);
        check(registry.at("minecraft:overworld", 3, 66, 3) == a && registry.at("minecraft:overworld", 21, 64, 1) == c, "block lookup");
        check(registry.at("minecraft:overworld", 8, 66, 3) == null && registry.at("minecraft:overworld", 3, 70, 3) == null, "outside every arena");
        check(registry.at("minecraft:the_nether", 3, 66, 3) == null, "dimension matters");
        check(registry.atPoint("minecraft:overworld", 7.99, 69.9, 0.0) == a && registry.atPoint("minecraft:overworld", 8.0, 66, 0.0) == null,
                "point lookup uses the continuous box");
        check(registry.get("c") == c && registry.get("zzz") == null, "by name");
        a.setState("built");
        a.setForced(List.of(1L, 2L));
        registry.save();
        check(Files.exists(file) && !Files.exists(dir.resolve("mcb_arenas.json.tmp")), "saved atomically: no temp left");

        var again = ArenaRegistry.load(file);
        check(again.all().size() == 2, "both arenas survive a restart");
        var back = again.get("a");
        check(back.state().equals("built") && back.origin()[1] == 64 && back.size()[2] == 8, "state, origin and size");
        check(back.forced().equals(List.of(1L, 2L)), "the chunks it held forced");
        check(back.teams().get("red").pads().get(1)[0] == 2 && back.teams().get("blue").entry()[0] == 7, "team markers survive");
        check(back.mobSpawns().size() == 2 && back.spectator()[0] == 4, "mob spawns and spectator survive");
        check(back.capacity().perTeam() == 10, "capacity survives");
        check(back.world(new int[] {1, 1, 6})[0] == 1 && back.world(new int[] {1, 1, 6})[1] == 65 && back.world(new int[] {1, 1, 6})[2] == 6,
                "a marker in world coordinates");
        check(back.box().contains(7, 69, 7) && !back.box().contains(8, 69, 7), "the box from origin and size");
        check(again.at("minecraft:overworld", 3, 66, 3) != null, "protection is rebuilt from the file");
        check(again.remove("a") == back && again.get("a") == null, "remove returns the record");
        again.save();
        check(ArenaRegistry.load(file).all().size() == 1, "the removal persisted");
        check(again.remove("a") == null, "removing twice is a no-op");

        Files.writeString(file, "{not json");
        try { ArenaRegistry.load(file); throw new AssertionError("a corrupt file loaded"); }
        catch (IllegalStateException expected) { check(expected.getMessage().contains("mcb_arenas"), "names the file"); }
        Files.writeString(file, "{\"schema\":9,\"arenas\":[]}");
        try { ArenaRegistry.load(file); throw new AssertionError("a future schema loaded"); }
        catch (IllegalStateException expected) { check(expected.getMessage().contains("schema"), "refuses an unknown schema"); }
    }

    private static void requests() {
        var build = ArenaRequests.parseBuild(json("{\"plan\":" + PLAN + ",\"origin\":[100,70,-20]}"));
        check(build.plan().name().equals("vault") && build.name().equals("vault"), "the arena is named for the plan");
        check(build.origin()[0] == 100 && build.origin()[2] == -20 && build.dimension().equals("minecraft:overworld"), "origin, default dimension");
        var named = ArenaRequests.parseBuild(json("{\"plan\":" + PLAN + ",\"origin\":[1,2,3],\"name\":\"vault-2\",\"dimension\":\"minecraft:the_nether\"}"));
        check(named.name().equals("vault-2") && named.dimension().equals("minecraft:the_nether"), "name and dimension override");
        rejects(() -> ArenaRequests.parseBuild(json("{\"origin\":[1,2,3]}")), "bad_request", "no plan");
        rejects(() -> ArenaRequests.parseBuild(json("{\"plan\":" + PLAN + "}")), "bad_request", "no origin");
        rejects(() -> ArenaRequests.parseBuild(json("{\"plan\":" + PLAN + ",\"origin\":[1,2]}")), "bad_request", "origin arity");
        rejects(() -> ArenaRequests.parseBuild(json("{\"plan\":" + PLAN + ",\"origin\":[1,2,\"x\"]}")), "bad_request", "origin type");
        rejects(() -> ArenaRequests.parseBuild(json("{\"plan\":" + PLAN + ",\"origin\":[1,2,3],\"name\":\"No Way\"}")), "bad_request", "bad name");
        rejects(() -> ArenaRequests.parseBuild(json("{\"plan\":" + PLAN + ",\"origin\":[1,2,3],\"bogus\":1}")), "bad_request", "unknown field");
        rejects(() -> ArenaRequests.parseBuild(json("{\"plan\":" + PLAN + ",\"origin\":[1,2,3],\"dimension\":\"nether\"}")), "bad_request", "bad dimension");
        rejects(() -> ArenaRequests.parseBuild(json("{\"plan\":{\"schema\":3},\"origin\":[1,2,3]}")), "bad_plan", "a bad plan says so");
        var envelope = ArenaRequests.parseBuild(json("{\"schema_version\":1,\"run_id\":\"r\",\"episode_id\":\"e\",\"body_id\":\"b\","
                + "\"sequence\":4,\"op\":\"arena_build\",\"request_id\":\"x\",\"plan\":" + PLAN + ",\"origin\":[0,0,0]}"));
        check(envelope.name().equals("vault"), "the bridge envelope fields are allowed");

        check(ArenaRequests.parseName(json("{\"name\":\"vault\"}")).equals("vault"), "a name");
        rejects(() -> ArenaRequests.parseName(json("{}")), "bad_request", "name missing");
        rejects(() -> ArenaRequests.parseName(json("{\"name\":\"a b\"}")), "bad_request", "name chars");
        rejects(() -> ArenaRequests.parseName(json("{\"name\":\"a\",\"x\":1}")), "bad_request", "name takes nothing else");
        check(!ArenaRequests.parseRemove(json("{\"name\":\"a\"}")).restore() && ArenaRequests.parseRemove(json("{\"name\":\"a\",\"restore\":true}")).restore(),
                "restore is optional");
        rejects(() -> ArenaRequests.parseRemove(json("{\"name\":\"a\",\"restore\":\"yes\"}")), "bad_request", "restore is a flag");

        var waves = ArenaRequests.parseStart(json("{\"name\":\"vault\",\"mode\":\"waves\",\"waves\":["
                + "{\"mobs\":{\"minecraft:ravager\":5,\"minecraft:pillager\":3},\"delay_ticks\":100},{\"mobs\":{\"minecraft:witch\":1}}],"
                + "\"participants\":[\"Alice\",\"Bot_1\"]}"));
        check(waves.mode().equals("waves") && waves.waves().size() == 2, "two waves");
        check(waves.waves().get(0).delay() == 100 && waves.waves().get(1).delay() == 0, "delay defaults to none");
        check(waves.waves().get(0).mobs().keySet().iterator().next().equals("minecraft:ravager"), "mob order is kept");
        check(waves.waves().get(0).total() == 8, "mobs in a wave");
        check(waves.participants().equals(List.of("Alice", "Bot_1")) && waves.teams() == null, "participants by name");
        var open = ArenaRequests.parseStart(json("{\"name\":\"v\",\"mode\":\"waves\",\"waves\":[{\"mobs\":{\"minecraft:zombie\":1}}]}"));
        check(open.participants() == null, "no participants: everyone inside at the start");
        var teams = ArenaRequests.parseStart(json("{\"name\":\"v\",\"mode\":\"teams\",\"participants\":[\"a\",\"b\"]}"));
        check(teams.mode().equals("teams") && teams.waves().isEmpty(), "teams mode takes no waves");
        var mapped = ArenaRequests.parseStart(json("{\"name\":\"v\",\"mode\":\"teams\",\"teams\":{\"red\":[\"a\"],\"blue\":[\"b\"]}}"));
        check(mapped.teams().get("red").equals(List.of("a")) && mapped.participants().equals(List.of("a", "b")), "explicit teams");
        rejects(() -> ArenaRequests.parseStart(json("{\"name\":\"v\",\"mode\":\"teams\",\"teams\":{\"red\":[\"a\"]},\"participants\":[\"a\"]}")),
                "bad_request", "teams and participants together");
        var mobbed = ArenaRequests.parseStart(json("{\"name\":\"v\",\"mode\":\"teams\",\"waves\":[{\"mobs\":{\"minecraft:zombie\":1}}],"
                + "\"teams\":{\"red\":[\"a\"],\"blue\":[\"b\"]}}"));
        check(mobbed.waves().size() == 1 && mobbed.teams().size() == 2, "team against team, with mob waves too");
        check(mobbed.exit() == null, "no exit: participants stay where the match ends");
        var race = ArenaRequests.parseStart(json("{\"name\":\"v\",\"mode\":\"race\",\"waves\":[{\"mobs\":{\"minecraft:zombie\":2}}],"
                + "\"participants\":[\"a\",\"b\"],\"exit\":[0,101,-3]}"));
        check(race.mode().equals("race") && race.participants().size() == 2, "a kill race, teams dealt out");
        check(race.exit()[0] == 0 && race.exit()[1] == 101 && race.exit()[2] == -3, "the exit cell");
        rejects(() -> ArenaRequests.parseStart(json("{\"name\":\"v\",\"mode\":\"race\",\"participants\":[\"a\",\"b\"]}")),
                "bad_request", "a race needs waves to race on");
        rejects(() -> ArenaRequests.parseStart(json("{\"name\":\"v\",\"mode\":\"race\",\"waves\":[]}")), "bad_request", "empty race waves");
        rejects(() -> ArenaRequests.parseStart(json("{\"name\":\"v\",\"mode\":\"waves\",\"waves\":[{\"mobs\":{\"minecraft:zombie\":1}}],"
                + "\"exit\":[1,2]}")), "bad_request", "exit arity");
        rejects(() -> ArenaRequests.parseStart(json("{\"name\":\"v\",\"mode\":\"waves\",\"waves\":[{\"mobs\":{\"minecraft:zombie\":1}}],"
                + "\"teams\":{\"red\":[\"a\"],\"blue\":[\"b\"]}}")), "bad_request", "co-op has no teams");

        var spawn = ArenaRequests.parseSpawn(json("{\"position\":[0,102,-1]}"));
        check(spawn[0] == 0 && spawn[1] == 102 && spawn[2] == -1, "the world spawn cell");
        rejects(() -> ArenaRequests.parseSpawn(json("{}")), "bad_request", "spawn needs a position");
        rejects(() -> ArenaRequests.parseSpawn(json("{\"position\":[0,1,2],\"radius\":3}")), "bad_request", "spawn takes a position only");

        var op = ArenaRequests.parseOp(json("{\"player\":\"Ann_2\",\"operator\":true}"));
        check(op.player().equals("Ann_2") && op.op(), "op a player");
        check(!ArenaRequests.parseOp(json("{\"player\":\"Ann\",\"operator\":false}")).op(), "or take it away");
        rejects(() -> ArenaRequests.parseOp(json("{\"player\":\"no way\",\"operator\":true}")), "bad_request", "op: a player name");
        rejects(() -> ArenaRequests.parseOp(json("{\"player\":\"Ann\"}")), "bad_request", "op: say which");
        rejects(() -> ArenaRequests.parseOp(json("{\"player\":\"Ann\",\"operator\":\"yes\"}")), "bad_request", "op: a flag");

        var kit = ArenaRequests.parseKit(json("{\"players\":[\"Ann\",\"Bob\"],\"kit\":{\"give\":[{\"item\":\"minecraft:iron_sword\","
                + "\"count\":1}],\"clear_inventory\":true,\"heal\":true,\"wear\":true}}"));
        check(kit.players().equals(List.of("Ann", "Bob")) && kit.kit().getAsJsonArray("give").size() == 1, "a kit for two");
        rejects(() -> ArenaRequests.parseKit(json("{\"players\":[\"Ann\"],\"kit\":{\"summon\":[]}}")), "bad_request",
                "a kit gives, heals, clears and dresses; nothing else");
        rejects(() -> ArenaRequests.parseKit(json("{\"players\":[],\"kit\":{}}")), "bad_request", "a kit for nobody");
        rejects(() -> ArenaRequests.parseKit(json("{\"players\":[\"Ann\"]}")), "bad_request", "no kit");
        var parsed = TrialLogic.parse(kit.kit());
        check(parsed.give().size() == 1 && parsed.heal() && parsed.wear() && parsed.clearInventory(), "the kit reads as a trial's");
        rejects(() -> ArenaRequests.parseStart(json("{\"name\":\"v\",\"mode\":\"waves\"}")), "bad_request", "waves need waves");
        rejects(() -> ArenaRequests.parseStart(json("{\"name\":\"v\",\"mode\":\"waves\",\"waves\":[]}")), "bad_request", "empty waves");
        rejects(() -> ArenaRequests.parseStart(json("{\"name\":\"v\",\"mode\":\"duel\"}")), "bad_request", "unknown mode");
        rejects(() -> ArenaRequests.parseStart(json("{\"name\":\"v\"}")), "bad_request", "no mode");
        rejects(() -> ArenaRequests.parseStart(json("{\"name\":\"v\",\"mode\":\"waves\",\"waves\":[{\"mobs\":{}}]}")), "bad_request", "an empty wave");
        rejects(() -> ArenaRequests.parseStart(json("{\"name\":\"v\",\"mode\":\"waves\",\"waves\":[{\"mobs\":{\"zombie\":1}}]}")), "bad_request", "mob id");
        rejects(() -> ArenaRequests.parseStart(json("{\"name\":\"v\",\"mode\":\"waves\",\"waves\":[{\"mobs\":{\"minecraft:zombie\":0}}]}")), "bad_request", "count 0");
        rejects(() -> ArenaRequests.parseStart(json("{\"name\":\"v\",\"mode\":\"waves\",\"waves\":[{\"mobs\":{\"minecraft:zombie\":999}}]}")), "bad_request", "count cap");
        rejects(() -> ArenaRequests.parseStart(json("{\"name\":\"v\",\"mode\":\"waves\",\"waves\":[{\"mobs\":{\"minecraft:zombie\":1},\"delay_ticks\":-1}]}")),
                "bad_request", "negative delay");
        rejects(() -> ArenaRequests.parseStart(json("{\"name\":\"v\",\"mode\":\"waves\",\"waves\":[{\"mobs\":{\"minecraft:zombie\":1},\"delay_ticks\":999999}]}")),
                "bad_request", "delay cap");
        rejects(() -> ArenaRequests.parseStart(json("{\"name\":\"v\",\"mode\":\"waves\",\"waves\":[{\"mobs\":{\"minecraft:ender_dragon\":1}}]}")),
                "not_allowed", "no dragon");
        rejects(() -> ArenaRequests.parseStart(json("{\"name\":\"v\",\"mode\":\"waves\",\"waves\":[{\"mobs\":{\"minecraft:zombie\":1},\"extra\":1}]}")),
                "bad_request", "wave fields");
        rejects(() -> ArenaRequests.parseStart(json("{\"name\":\"v\",\"mode\":\"waves\",\"waves\":[{\"mobs\":{\"minecraft:zombie\":1}}],\"participants\":[\"a b\"]}")),
                "bad_request", "player name chars");
        rejects(() -> ArenaRequests.parseStart(json("{\"name\":\"v\",\"mode\":\"waves\",\"waves\":[{\"mobs\":{\"minecraft:zombie\":1}}],\"participants\":[]}")),
                "bad_request", "empty participants");
        var many = new StringBuilder("{\"name\":\"v\",\"mode\":\"waves\",\"waves\":[");
        for (int i = 0; i < 51; i++) many.append(i == 0 ? "" : ",").append("{\"mobs\":{\"minecraft:zombie\":1}}");
        rejects(() -> ArenaRequests.parseStart(json(many + "]}")), "bad_request", "wave count cap");
    }
}
