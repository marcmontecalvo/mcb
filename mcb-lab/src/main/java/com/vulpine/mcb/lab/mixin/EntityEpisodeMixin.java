package com.vulpine.mcb.lab.mixin;
import com.vulpine.mcb.lab.LabRuntime;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.Entity;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.*;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;
@Mixin(ServerLevel.class)
public abstract class EntityEpisodeMixin {
    @Inject(method="addFreshEntity",at=@At("RETURN"))
    private void added(Entity entity, CallbackInfoReturnable<Boolean> cir) {
        LabRuntime.courseDrop(entity,cir.getReturnValue());
    }
}
