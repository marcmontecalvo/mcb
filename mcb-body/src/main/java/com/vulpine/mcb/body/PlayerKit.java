package com.vulpine.mcb.body;

import com.google.gson.JsonObject;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.item.ItemStack;

/**
 * A kit for any player (the arena runtime's arena_kit op): the trial op's give, clear_inventory, heal and wear, the same
 * steps in the same order (BodyTrial), so a person in an arena match starts dressed like the bot. {@link #check} reads
 * and checks every id once; {@link #dress} applies it to one player.
 */
public final class PlayerKit {
    /** A kit refused: {@code reason} is the short code (bad_request, unknown_item, inventory_full, ...). */
    public static final class Refused extends RuntimeException {
        public final String reason;

        Refused(String reason) {
            super(reason);
            this.reason = reason;
        }
    }

    private final TrialLogic.Trial trial;
    private final List<ItemStack> stacks;

    private PlayerKit(TrialLogic.Trial trial, List<ItemStack> stacks) {
        this.trial = trial;
        this.stacks = stacks;
    }

    /** The kit read and its ids checked (against {@code anyone}'s registries) before any inventory changes. */
    public static PlayerKit check(ServerPlayer anyone, JsonObject kit) {
        try {
            var trial = TrialLogic.parse(kit);
            return new PlayerKit(trial, BodyTrial.arenaStacks(anyone, trial.give()));
        } catch (BodyActions.Fail fail) {
            throw new Refused(fail.getMessage());
        }
    }

    /** Clear (when asked), give, wear (when asked), heal (when asked); what it did. */
    public Map<String, Object> dress(ServerPlayer player) {
        var out = new LinkedHashMap<String, Object>();
        try {
            if (trial.clearInventory()) player.getInventory().clearContent();
            out.put("given", BodyTrial.give(player, stacks));
            if (trial.wear()) out.put("wear", BodyGear.wear(player));
            if (trial.heal()) BodyTrial.heal(player);
        } catch (BodyActions.Fail fail) {
            throw new Refused(fail.getMessage());
        }
        out.put("health", player.getHealth());
        return out;
    }
}
