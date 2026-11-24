package com.vulpine.mcb.body;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.vulpine.mcb.arena.ArenaFail;
import com.vulpine.mcb.arena.ArenaLogic;
import com.vulpine.mcb.arena.ArenaLogic.Seg;
import com.vulpine.mcb.arena.ArenaLogic.Stat;
import com.vulpine.mcb.arena.ArenaRecap;
import com.vulpine.mcb.arena.ArenaRequests;
import java.util.ArrayList;
import java.util.List;

/** The after-action table: Minecraft font widths, pixel padding, aligned columns, the recap request. */
public final class ArenaRecapTest {
    private static void check(boolean yes, String label) { if (!yes) throw new AssertionError(label); }

    private static JsonObject json(String text) { return JsonParser.parseString(text).getAsJsonObject(); }

    private static void rejects(Runnable run, String reason, String label) {
        try { run.run(); }
        catch (ArenaFail fail) { check(fail.reason.equals(reason), label + " -> " + fail.getMessage()); return; }
        throw new AssertionError("accepted: " + label);
    }

    public static void main(String[] args) {
        widths();
        padding();
        header();
        coop();
        teams();
        fits();
        request();
        System.out.println("ArenaRecapTest ok");
    }

    private static int width(List<Seg> segs) {
        int px = 0;
        for (var s : segs) px += ArenaRecap.width(s.text(), s.bold());
        return px;
    }

    private static String plain(List<Seg> line) {
        var out = new StringBuilder();
        for (var s : line) out.append(s.text());
        return out.toString();
    }

    /** Where each of the five number columns ends on a row, in pixels (its last five words, the star left out). */
    private static List<Integer> ends(List<Seg> row) {
        var out = new ArrayList<Integer>();
        int x = 0;
        for (var s : row) {
            x += ArenaRecap.width(s.text(), s.bold());
            if (!s.text().isBlank() && !s.text().contains("★")) out.add(x);
        }
        return out.subList(out.size() - 5, out.size());
    }

    private static void aligned(List<List<Seg>> rows, String label) {
        var first = ends(rows.get(0));
        for (var row : rows) {
            var at = ends(row);
            for (int c = 0; c < 5; c++)
                check(Math.abs(at.get(c) - first.get(c)) <= 1, label + ": column " + c + " ends at " + at + " not " + first + " in '" + plain(row) + "'");
        }
    }

    private static Seg cell(List<Seg> row, String text) {
        return row.stream().filter(s -> s.text().equals(text)).findFirst().orElseThrow(() -> new AssertionError("no '" + text + "' in " + plain(row)));
    }

    /** The default font's advances (glyph and the pixel after it); bold is a pixel wider. */
    private static void widths() {
        check(ArenaRecap.width("MCBot", false) == 28, "M C B o 6, t 4: " + ArenaRecap.width("MCBot", false));
        check(ArenaRecap.width("MCBot", true) == 33, "bold: a pixel more a character");
        check(ArenaRecap.width("Ill", false) == 10 && ArenaRecap.width("if", false) == 7, "the narrow letters");
        check(ArenaRecap.width(" ", false) == 4 && ArenaRecap.width(" ", true) == 5, "a space, a bold space");
        check(ArenaRecap.width("212", false) == 18 && ArenaRecap.width("FlamableChkn", false) == 65, "digits and a name");
    }

    /** Padding is spaces (4 px) and bold spaces (5 px): exact from 12 px up and at 4, 5, 8, 9, 10. */
    private static void padding() {
        check(ArenaRecap.pad(0).isEmpty(), "nothing for no room");
        for (int px : new int[] {4, 5, 8, 9, 10, 12, 13, 14, 15, 16, 17, 23, 40, 97})
            check(width(ArenaRecap.pad(px)) == px, "exactly " + px + ": " + width(ArenaRecap.pad(px)));
        check(Math.abs(width(ArenaRecap.pad(11)) - 11) == 1, "11 is a pixel off");
        check(ArenaRecap.pad(13).stream().allMatch(s -> s.text().isBlank()), "only spaces");
    }

    private static void header() {
        check(ArenaRecap.header("Red wins!", new int[] {2, 5}, 2040).equals("Match 2 of 5 · Red wins! · 1:42"), "a series match says which");
        check(ArenaRecap.header("Stopped", new int[] {1, 1}, 1380).equals("Stopped · 1:09"), "a lone match does not say 1 of 1");
        check(ArenaRecap.header("Victory!", null, 700).equals("Victory! · 0:35"), "no series");
    }

    /** Co-op: a title, the column titles, then everyone by damage dealt, the numbers right-aligned in columns. */
    private static void coop() {
        var stats = List.of(
                new Stat("Osadar932", "", 1, 0, 0, 47.8, 21, 3.8),
                new Stat("MCBot", "", 9, 0, 1, 212.2, 39.2, 26.9),
                new Stat("FlamableChkn", "", 0, 0, 1, 30.8, 23.5, 3.5));
        var lines = ArenaRecap.table("Stopped · 1:09", List.of(), List.of(), stats);
        check(plain(lines.get(0)).equals("Stopped · 1:09") && lines.get(0).get(0).bold() && lines.get(0).get(0).color().equals("gold"), "the title");
        var titles = lines.get(1);
        check(plain(titles).replaceAll(" +", " ").trim().equals("Kills Downs Dealt Taken Healed"), "column titles: " + plain(titles));
        check(titles.stream().filter(s -> !s.text().isBlank()).allMatch(s -> s.color().equals("gray")), "titles in gray");
        var rows = lines.subList(1, lines.size());
        check(rows.size() == 4, "titles and three players");
        aligned(rows, "co-op");
        var bot = lines.get(2);
        check(plain(bot).startsWith("MCBot"), "the top damage dealer first: " + plain(bot));
        check(plain(lines.get(3)).startsWith("Osadar932") && plain(lines.get(4)).startsWith("FlamableChkn"), "then by damage dealt");
        check(plain(bot).replaceAll(" +", " ").trim().equals("MCBot 9 1 212 39 27 ★"), "whole numbers, the star last: " + plain(bot));
        check(cell(bot, "212").color().equals("gold") && cell(bot, "212").bold(), "the top damage in gold");
        check(cell(bot, "★").color().equals("gold") && !cell(bot, "★").bold(), "a plain gold star, outside the columns");
        check(cell(bot, "9").color().equals("white") && cell(bot, "1").color().equals("red") && cell(bot, "27").color().equals("green"),
                "kills white, downs red, healed green");
        var osa = lines.get(3);
        check(cell(osa, "0").color().equals("dark_gray"), "a zero is dimmed");
        check(cell(osa, "48").color().equals("yellow") && !cell(osa, "48").bold(), "damage dealt in yellow");
        check(cell(osa, "21").color().equals("gray"), "damage taken in gray");
        check(cell(osa, "Osadar932").color().equals("white"), "co-op names in white");
        String hover = cell(bot, "MCBot").hover();
        check(hover != null && hover.contains("212.2") && hover.contains("39.2") && hover.contains("26.9") && hover.contains("9 mobs"),
                "the exact numbers on hover: " + hover);
        check(bot.stream().filter(s -> !s.text().isBlank()).allMatch(s -> hover.equals(s.hover())), "the whole row hovers alike");
        check(lines.get(4).stream().noneMatch(s -> s.text().contains("★")), "one star");
        var idle = ArenaRecap.table("Victory!", List.of(), List.of(), List.of(new Stat("Ann", "", 5, 0, 0, 0, 0, 0)));
        check(idle.stream().noneMatch(l -> plain(l).contains("★")), "no star for no damage");
    }

    /** Teams: each team's row (its colour, its totals in the same columns), then its players indented. */
    private static void teams() {
        var stats = List.of(
                new Stat("Ann", "red", 0, 1, 1, 34.5, 28, 12),
                new Stat("Cy", "red", 2, 0, 0, 10, 4, 0),
                new Stat("Bot", "blue", 1, 1, 1, 40.25, 44.5, 20));
        var extra = List.<List<Seg>>of(List.of(new Seg("Red 1", "red", true), new Seg(" · ", "gray", false), new Seg("Blue 0", "blue", true)));
        var lines = ArenaRecap.table("Match 1 of 3 · Red wins! · 0:50", extra, List.of("red", "blue"), stats);
        check(plain(lines.get(1)).equals("Red 1 · Blue 0"), "the extra lines after the title");
        var rows = lines.subList(2, lines.size());
        check(rows.size() == 6, "titles, two team rows, three players: " + rows.size());
        aligned(rows, "teams");
        var red = lines.get(3);
        check(plain(red).replaceAll(" +", " ").trim().equals("Red 3 1 45 32 12"), "the team's totals: " + plain(red));
        check(cell(red, "Red").color().equals("red") && cell(red, "Red").bold(), "the team in its colour, bold");
        check(plain(lines.get(4)).trim().startsWith("Ann") && plain(lines.get(5)).trim().startsWith("Cy"), "its players by damage dealt");
        check(plain(lines.get(4)).startsWith(" ") && cell(lines.get(4), "Ann").color().equals("red"), "indented, in the team colour");
        check(plain(lines.get(6)).trim().startsWith("Blue") && plain(lines.get(7)).contains("★"), "the other team; its top dealer starred");
        check(cell(lines.get(7), "Bot").hover().contains("1 mob") && cell(lines.get(7), "Bot").hover().contains("1 player"),
                "kills split on hover: " + cell(lines.get(7), "Bot").hover());
        var empty = ArenaRecap.table("Draw", List.of(), List.of("red", "blue"), List.of(new Stat("Ann", "red", 0, 0, 0, 0, 0, 0)));
        check(empty.stream().anyMatch(l -> plain(l).trim().startsWith("Blue")), "a team with nobody left still shows");
    }

    /** The default chat is 320 px wide: eight-a-side with long names and big numbers stays inside it. */
    private static void fits() {
        var stats = new ArrayList<Stat>();
        for (int i = 0; i < 8; i++) {
            stats.add(new Stat("Wwwwwwwwwwwwww" + i + "m", "red", 99, 9, 9, 9999.9, 999, 999));
            stats.add(new Stat("Mmmmmmmmmmmmmm" + i + "w", "blue", 9, 99, 9, 999, 9999.9, 99));
        }
        var lines = ArenaRecap.table("Match 3 of 5 · Blue wins! · 12:41", List.of(), List.of("red", "blue"), stats);
        for (var line : lines.subList(1, lines.size()))
            check(width(line) <= ArenaRecap.CHAT_WIDTH, "fits the chat: " + width(line) + " for '" + plain(line) + "'");
        aligned(lines.subList(1, lines.size()), "eight a side");
    }

    /** arena_recap: Python's series totals, drawn by the same table. */
    private static void request() {
        var r = ArenaRequests.parseRecap(json("{\"name\":\"duel\",\"title\":\"duel · final standings\","
                + "\"lines\":[[{\"text\":\"Red 2\",\"color\":\"red\",\"bold\":true}]],\"teams\":[\"red\",\"blue\"],"
                + "\"players\":[{\"name\":\"Ann\",\"team\":\"red\",\"mob_kills\":1,\"player_kills\":2,\"deaths\":1,"
                + "\"dealt\":70.5,\"taken\":32,\"healing\":18},{\"name\":\"MCBot\",\"team\":\"blue\"}]}"));
        check(r.name().equals("duel") && r.title().equals("duel · final standings") && r.teams().equals(List.of("red", "blue")), "the fields");
        check(r.lines().get(0).get(0).equals(new Seg("Red 2", "red", true)), "the extra lines");
        check(r.players().get(0).equals(new Stat("Ann", "red", 1, 2, 1, 70.5, 32, 18)), "a player's totals");
        check(r.players().get(1).equals(new Stat("MCBot", "blue", 0, 0, 0, 0, 0, 0)), "missing numbers are zero");
        var coop = ArenaRequests.parseRecap(json("{\"name\":\"vault\",\"title\":\"t\",\"players\":[{\"name\":\"Ann\"}]}"));
        check(coop.teams().isEmpty() && coop.lines().isEmpty() && coop.players().get(0).team().isEmpty(), "co-op: no teams, no extra lines");
        String ok = "\"name\":\"duel\",\"title\":\"t\"";
        for (String bad : new String[] {
                "{" + ok + "}",
                "{" + ok + ",\"players\":[]}",
                "{" + ok + ",\"players\":[{\"name\":\"bad name\"}]}",
                "{" + ok + ",\"players\":[{\"name\":\"Ann\",\"team\":\"green\"}],\"teams\":[\"red\"]}",
                "{" + ok + ",\"players\":[{\"name\":\"Ann\",\"team\":\"red\"}]}",
                "{" + ok + ",\"players\":[{\"name\":\"Ann\",\"dealt\":-1}]}",
                "{" + ok + ",\"players\":[{\"name\":\"Ann\",\"deaths\":1.5}]}",
                "{" + ok + ",\"players\":[{\"name\":\"Ann\",\"kills\":1}]}",
                "{" + ok + ",\"players\":[{\"name\":\"Ann\"}],\"extra\":1}",
                "{" + ok + ",\"players\":[{\"name\":\"Ann\"}],\"teams\":[\"Red Team\"]}",
                "{\"name\":\"duel\",\"title\":\"" + "x".repeat(65) + "\",\"players\":[{\"name\":\"Ann\"}]}",
        }) rejects(() -> ArenaRequests.parseRecap(json(bad)), "bad_request", "recap " + bad);
        check(ArenaLogic.isOperationName("arena_recap") && !ArenaLogic.isOperationName("arena_say"), "arena_recap replaces arena_say");
    }
}
