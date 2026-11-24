package com.vulpine.mcb.arena.mixin;

import com.vulpine.mcb.arena.ArenaGuard;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.item.context.BlockPlaceContext;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * No block item is placed in a registered arena, by a player, a fake player or a dispenser: the one place every block
 * placement passes through. It judges the cell placed into and the one above it (a door, a tall plant), so a block put
 * against an arena wall from outside is still fine.
 */
@Mixin(BlockItem.class)
public abstract class BlockPlaceMixin {
    @Inject(method = "place(Lnet/minecraft/world/item/context/BlockPlaceContext;)Lnet/minecraft/world/InteractionResult;",
            at = @At("HEAD"), cancellable = true)
    private void mcbarena$noPlacing(BlockPlaceContext context, CallbackInfoReturnable<InteractionResult> cir) {
        var level = context.getLevel();
        var pos = context.getClickedPos();
        if (ArenaGuard.protectedCell(level, pos) || ArenaGuard.protectedCell(level, pos.above())) cir.setReturnValue(InteractionResult.FAIL);
    }
}
