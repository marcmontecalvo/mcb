package com.vulpine.mclab.mixin;
import com.vulpine.mclab.BridgeRuntime;
import net.minecraft.core.BlockPos;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.state.BlockState;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.*;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;
@Mixin(Level.class)
public abstract class WorldEpisodeMixin {
    @Inject(method="setBlock(Lnet/minecraft/core/BlockPos;Lnet/minecraft/world/level/block/state/BlockState;II)Z",
            at=@At("HEAD"),cancellable=true)
    private void mutate(BlockPos pos,BlockState state,int flags,int recursion,CallbackInfoReturnable<Boolean> cir) {
        if (!BridgeRuntime.permitCourseMutation((Level)(Object)this,pos,state)) cir.setReturnValue(false);
    }
}
