package com.vulpine.mcb.lab.mixin;

import net.minecraft.world.entity.player.Player;
import net.minecraft.world.food.FoodData;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;

@Mixin(Player.class)
public interface PlayerStateAccessor {
    @Accessor("foodData")
    void mcblab$setFoodData(FoodData foodData);
}
