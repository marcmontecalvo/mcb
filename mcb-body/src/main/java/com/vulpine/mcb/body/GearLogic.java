package com.vulpine.mcb.body;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * What the body wears (pure; BodyGear reads the game's items into Pieces): the best armor it
 * carries in each armor slot, and a shield in the off hand. A worn piece at least as good stays
 * on, and an off hand holding something that is not gear (a totem, a torch) is left alone.
 * Docs: COMPANION_COMBAT_PLAN_2026-11-23.md section 15.
 */
final class GearLogic {
    private GearLogic() {}

    enum Slot { HEAD, CHEST, LEGS, FEET, OFFHAND }

    /** An inventory index (worn pieces: any), its slot, and what makes it better: armor, toughness, protection levels, uses left. */
    record Piece(int index, String item, Slot slot, double armor, double toughness, int protection, int left) {}

    /** Put the piece at inventory index `from` on in `to` (what was there goes back in its place). */
    record Move(int from, Slot to) {}

    private static final Comparator<Piece> RANK = Comparator.comparingDouble(Piece::armor)
            .thenComparingDouble(Piece::toughness).thenComparingInt(Piece::protection).thenComparingInt(Piece::left);

    /** Whether `a` is worth wearing over `b`. */
    static boolean better(Piece a, Piece b) { return RANK.compare(a, b) > 0; }

    /** A piece that belongs in its slot: armor with armor points, or a shield in the off hand. */
    private static boolean wearable(Piece p) { return p.slot() == Slot.OFFHAND || p.armor() > 0; }

    /**
     * The moves that dress the body: for each slot, the best carried piece when it beats what is
     * worn there. `otherHeld`: slots holding something that is not gear, never emptied.
     */
    static List<Move> plan(List<Piece> carried, Map<Slot, Piece> worn, Set<Slot> otherHeld) {
        var moves = new ArrayList<Move>();
        for (var slot : Slot.values()) {
            if (otherHeld.contains(slot)) continue;
            Piece best = null;
            for (var p : carried)
                if (p.slot() == slot && wearable(p) && (best == null || better(p, best))) best = p;
            if (best == null) continue;
            var on = worn.get(slot);
            if (on == null || better(best, on)) moves.add(new Move(best.index(), slot));
        }
        return moves;
    }

    static String slotName(Slot slot) { return slot.name().toLowerCase(java.util.Locale.ROOT); }
}
