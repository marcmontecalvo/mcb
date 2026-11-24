package com.vulpine.mcb.arena;

import com.vulpine.mcb.arena.ArenaLogic.Seg;
import com.vulpine.mcb.arena.ArenaLogic.Stat;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;

/**
 * The after-action table in chat (pure): a title, any lines under it (a series' score), the column titles, then a row a
 * player (by damage dealt) with kills, downs and health dealt, taken and healed; in a team game each team's row of
 * totals comes first, in its colour, with its players indented under it.
 *
 * <p>Chat is set in a proportional font, so the columns are kept straight by measuring every word in the default font's
 * advances and padding with spaces (4 px) and bold spaces (5 px) to the pixel. The numbers are whole (a tenth would be
 * noise at a glance), coloured by column, a zero dimmed; the exact figures are on each row's hover. The top damage
 * dealer's damage is gold, and the row ends in a star (outside the columns: the star is not an ASCII glyph, its width
 * not the font's). A player whose client forces the Unicode font sees the columns a little off; nothing else is.
 */
public final class ArenaRecap {
    private ArenaRecap() {}

    /** The default chat width, in font pixels. */
    public static final int CHAT_WIDTH = 320;
    static final int GAP = 8, INDENT = 8;
    static final List<String> COLUMNS = List.of("Kills", "Downs", "Dealt", "Taken", "Healed");
    static final String[] COLUMN_COLORS = {"white", "red", "yellow", "gray", "green"};

    /** The header: the match of the series (not for a lone match), how it ended, how long it took (m:ss). */
    public static String header(String end, int[] series, int ticks) {
        int seconds = Math.max(0, ticks) / 20;
        String time = (seconds / 60) + ":" + String.format(Locale.ROOT, "%02d", seconds % 60);
        boolean ofMany = series != null && series[1] > 1;
        return (ofMany ? "Match " + series[0] + " of " + series[1] + " · " : "") + end + " · " + time;
    }

    // ---- measuring and padding

    /** The width of {@code text} in the default font, in pixels: each glyph and the pixel after it, a pixel more bold. */
    public static int width(String text, boolean bold) {
        int px = 0;
        for (int i = 0; i < text.length(); ) {
            int c = text.codePointAt(i);
            i += Character.charCount(c);
            px += advance(c) + (bold ? 1 : 0);
        }
        return px;
    }

    private static int advance(int c) {
        if (c == ' ') return 4;
        if ("!',.:;|i".indexOf(c) >= 0) return 2;
        if ("`l".indexOf(c) >= 0) return 3;
        if ("\"I[]t".indexOf(c) >= 0) return 4;
        if ("()*<>{}fk".indexOf(c) >= 0) return 5;
        if ("@~".indexOf(c) >= 0) return 7;
        return 6;
    }

    /** Blank parts {@code px} wide: spaces and bold spaces, exact from 12 px (and at 4, 5, 8, 9, 10), else a pixel off. */
    public static List<Seg> pad(int px) {
        if (px <= 0) return List.of();
        int bestSpaces = 0, bestBold = 0, bestError = Integer.MAX_VALUE;
        for (int bold = 0; bold <= 3; bold++) {
            int rest = px - 5 * bold;
            if (rest < 0) break;
            int spaces = Math.round(rest / 4f);
            int error = Math.abs(4 * spaces + 5 * bold - px);
            if (error < bestError) { bestSpaces = spaces; bestBold = bold; bestError = error; }
        }
        var out = new ArrayList<Seg>(2);
        if (bestSpaces > 0) out.add(new Seg(" ".repeat(bestSpaces), "white", false));
        if (bestBold > 0) out.add(new Seg(" ".repeat(bestBold), "white", true));
        return out;
    }

    // ---- the table

    /** A row before layout: how far in it starts, its label (null for the titles), its five cells, its hover, a star. */
    private record Row(int indent, Seg label, List<Seg> cells, String hover, boolean star) {}

    /**
     * The table's chat lines: {@code title} (gold, bold), then {@code lines} as given, then the columns. {@code teams}
     * in order (empty in co-op: everyone in white, no team rows).
     */
    public static List<List<Seg>> table(String title, List<List<Seg>> lines, List<String> teams, List<Stat> stats) {
        var out = new ArrayList<List<Seg>>();
        out.add(List.of(new Seg(title, "gold", true)));
        out.addAll(lines);
        Stat top = null;
        for (var s : stats) if (s.dealt() > 0 && (top == null || s.dealt() > top.dealt())) top = s;
        var rows = new ArrayList<Row>();
        rows.add(new Row(0, null, COLUMNS.stream().map(c -> new Seg(c, "gray", false)).toList(), null, false));
        if (teams.isEmpty()) {
            for (var s : byDealt(stats)) rows.add(player(s, "white", 0, s == top));
        } else {
            for (int i = 0; i < teams.size(); i++) {
                String team = teams.get(i), color = ArenaLogic.teamColor(team, i);
                var members = byDealt(stats.stream().filter(s -> s.team().equals(team)).toList());
                rows.add(total(team, color, members));
                for (var s : members) rows.add(player(s, color, INDENT, s == top));
            }
            for (var s : byDealt(stats.stream().filter(s -> !teams.contains(s.team())).toList()))
                rows.add(player(s, "white", 0, s == top));
        }
        for (var row : layout(rows)) out.add(row);
        return out;
    }

    private static List<List<Seg>> layout(List<Row> rows) {
        int nameEnd = 0;
        int[] widths = new int[COLUMNS.size()];
        for (var row : rows) {
            if (row.label() != null) nameEnd = Math.max(nameEnd, row.indent() + width(row.label()));
            for (int c = 0; c < widths.length; c++) widths[c] = Math.max(widths[c], width(row.cells().get(c)));
        }
        var out = new ArrayList<List<Seg>>();
        for (var row : rows) {
            var line = new ArrayList<Seg>();
            int x = 0;
            if (row.label() != null) {
                x = add(line, pad(row.indent()), x);
                x = add(line, List.of(row.label()), x);
            }
            int end = nameEnd;
            for (int c = 0; c < widths.length; c++) {
                end += GAP + widths[c];
                var cell = row.cells().get(c);
                x = add(line, pad(end - width(cell) - x), x);
                x = add(line, List.of(cell), x);
            }
            if (row.star()) {
                line.add(new Seg(" ", "white", false));
                line.add(new Seg("★", "gold", false));
            }
            out.add(row.hover() == null ? List.copyOf(line) : line.stream().map(s -> withHover(s, row.hover())).toList());
        }
        return out;
    }

    private static int add(List<Seg> line, List<Seg> parts, int x) {
        line.addAll(parts);
        for (var s : parts) x += width(s);
        return x;
    }

    private static int width(Seg s) { return width(s.text(), s.bold()); }

    private static Seg withHover(Seg s, String hover) { return new Seg(s.text(), s.color(), s.bold(), hover); }

    private static List<Stat> byDealt(List<Stat> stats) {
        var sorted = new ArrayList<>(stats);
        sorted.sort(Comparator.comparingDouble(Stat::dealt).reversed());
        return sorted;
    }

    private static Row player(Stat s, String color, int indent, boolean top) {
        var cells = cells(s.kills(), s.deaths(), s.dealt(), s.taken(), s.healing(), top);
        return new Row(indent, new Seg(s.name(), color, false), cells, hover(s.name(), s.mobKills(), s.playerKills(), s.deaths(),
                s.dealt(), s.taken(), s.healing()), top);
    }

    private static Row total(String team, String color, List<Stat> members) {
        int mobs = members.stream().mapToInt(Stat::mobKills).sum(), players = members.stream().mapToInt(Stat::playerKills).sum();
        int downs = members.stream().mapToInt(Stat::deaths).sum();
        double dealt = members.stream().mapToDouble(Stat::dealt).sum(), taken = members.stream().mapToDouble(Stat::taken).sum();
        double healed = members.stream().mapToDouble(Stat::healing).sum();
        String name = ArenaLogic.title(team);
        return new Row(0, new Seg(name, color, true), cells(mobs + players, downs, dealt, taken, healed, false),
                hover(name + " (team)", mobs, players, downs, dealt, taken, healed), false);
    }

    private static List<Seg> cells(int kills, int downs, double dealt, double taken, double healed, boolean top) {
        long[] values = {kills, downs, Math.round(dealt), Math.round(taken), Math.round(healed)};
        var out = new ArrayList<Seg>(values.length);
        for (int c = 0; c < values.length; c++) {
            String text = Long.toString(values[c]);
            if (values[c] == 0) out.add(new Seg(text, "dark_gray", false));
            else if (c == 2 && top) out.add(new Seg(text, "gold", true));
            else out.add(new Seg(text, COLUMN_COLORS[c], false));
        }
        return out;
    }

    /** The exact figures, shown when a row is hovered. */
    private static String hover(String who, int mobs, int players, int downs, double dealt, double taken, double healed) {
        return who + "\n" + plural(mobs + players, "kill") + ": " + plural(mobs, "mob") + ", " + plural(players, "player") + "\n"
                + plural(downs, "down") + "\n"
                + "Dealt " + ArenaLogic.fmt(dealt) + " · Took " + ArenaLogic.fmt(taken) + " · Healed " + ArenaLogic.fmt(healed) + "\n"
                + "(health points: 2 make a heart)";
    }

    private static String plural(long n, String what) { return n + " " + what + (n == 1 ? "" : "s"); }
}
