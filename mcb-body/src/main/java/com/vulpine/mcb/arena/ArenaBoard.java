package com.vulpine.mcb.arena;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import net.minecraft.ChatFormatting;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.HoverEvent;
import net.minecraft.network.chat.MutableComponent;
import net.minecraft.network.chat.numbers.BlankFormat;
import net.minecraft.network.chat.numbers.FixedFormat;
import net.minecraft.server.MinecraftServer;
import net.minecraft.world.scores.DisplaySlot;
import net.minecraft.world.scores.Objective;
import net.minecraft.world.scores.ScoreHolder;
import net.minecraft.world.scores.criteria.ObjectiveCriteria;

/**
 * One sidebar objective drawn as lines (ArenaLogic.Line): each line's text is its score holder's display name and its
 * value a fixed number format, so the board reads like a table; the scores only order the lines (top line highest).
 * Only lines that changed are sent again. Server thread only.
 */
final class ArenaBoard {
    private final MinecraftServer server;
    private final String name;
    private Objective objective;
    private String title;
    private List<ArenaLogic.Line> shown = List.of();

    ArenaBoard(MinecraftServer server, String name) {
        this.server = server;
        this.name = name;
    }

    /** The board on the sidebar with these lines (at most 15), made afresh if it was taken down or never made. */
    void show(String title, List<ArenaLogic.Line> lines) {
        var scoreboard = server.getScoreboard();
        if (objective == null || scoreboard.getObjective(name) != objective) {
            var old = scoreboard.getObjective(name);
            if (old != null) scoreboard.removeObjective(old);
            objective = scoreboard.addObjective(name, ObjectiveCriteria.DUMMY, heading(title), ObjectiveCriteria.RenderType.INTEGER, false,
                    BlankFormat.INSTANCE);
            this.title = title;
            shown = List.of();
        }
        if (scoreboard.getDisplayObjective(DisplaySlot.SIDEBAR) != objective) scoreboard.setDisplayObjective(DisplaySlot.SIDEBAR, objective);
        retitle(title);
        var next = lines.size() > ArenaLogic.BOARD_LINES ? lines.subList(0, ArenaLogic.BOARD_LINES) : lines;
        boolean reorder = next.size() != shown.size();
        for (int i = 0; i < next.size(); i++) {
            var line = next.get(i);
            if (!reorder && line.equals(shown.get(i))) continue;
            var score = scoreboard.getOrCreatePlayerScore(holder(i), objective);
            score.set(next.size() - i);
            score.display(text(line.text(), line.color(), line.bold(), line.struck()));
            score.numberFormatOverride(line.value().isEmpty() ? BlankFormat.INSTANCE
                    : new FixedFormat(text(line.value(), line.color().equals("dark_gray") ? "dark_gray" : "white", false, false)));
        }
        for (int i = next.size(); i < shown.size(); i++) scoreboard.resetSinglePlayerScore(holder(i), objective);
        shown = new ArrayList<>(next);
    }

    void retitle(String title) {
        if (objective == null || title.equals(this.title)) return;
        objective.setDisplayName(heading(title));
        this.title = title;
    }

    void hide() {
        var scoreboard = server.getScoreboard();
        var existing = scoreboard.getObjective(name);
        if (existing != null) scoreboard.removeObjective(existing);
        objective = null;
        title = null;
        shown = List.of();
    }

    private static ScoreHolder holder(int i) { return ScoreHolder.forNameOnly(String.format(Locale.ROOT, "#line%02d", i)); }

    private static Component heading(String title) { return text(title, "gold", true, false); }

    static MutableComponent text(String text, String color, boolean bold, boolean struck) {
        var out = Component.literal(text).withStyle(ChatFormatting.valueOf(color.toUpperCase(Locale.ROOT)));
        if (bold) out = out.withStyle(ChatFormatting.BOLD);
        if (struck) out = out.withStyle(ChatFormatting.STRIKETHROUGH);
        return out;
    }

    /** One chat line of coloured parts, each with its hover text when it has one. */
    static Component line(List<ArenaLogic.Seg> parts) {
        var out = Component.empty();
        for (var part : parts) {
            var piece = text(part.text(), part.color(), part.bold(), false);
            if (part.hover() != null) {
                var tip = new HoverEvent.ShowText(Component.literal(part.hover()));
                piece = piece.withStyle(style -> style.withHoverEvent(tip));
            }
            out.append(piece);
        }
        return out;
    }
}
