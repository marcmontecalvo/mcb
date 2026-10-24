package com.vulpine.mclab;

import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import java.util.*;

/** Native, synchronous development sensor fixtures; never run during an episode. */
public final class SensorVerification {
    private SensorVerification() {}

    public static Map<String,Object> check(ServerPlayer player, int tick) {
        var origin=player.blockPosition();
        var level=player.level();
        var saved=new LinkedHashMap<BlockPos,BlockState>();
        var checks=new LinkedHashMap<String,Boolean>();
        try {
            // Snapshot a bounded, loaded area and restore it before returning.
            for(int x=-4;x<=4;x++) for(int y=0;y<=4;y++) for(int z=0;z<=6;z++) {
                var pos=origin.offset(x,y,z);
                if(!level.getChunkSource().hasChunk(pos.getX()>>4,pos.getZ()>>4))
                    throw new IllegalStateException("sensor_fixture_chunk_missing");
                saved.put(pos,level.getBlockState(pos));
            }
            var target=origin.offset(0,1,4);
            var front=origin.offset(0,1,2);
            clear(player,saved.keySet());
            level.setBlock(target,Blocks.OAK_LOG.defaultBlockState(),2);
            var open=PolicySensor.visible(player,tick);
            checks.put("open_log_visible",contains(open,target,"minecraft:oak_log"));
            checks.put("floor_hides_buried_cells",open.stream().noneMatch(cell ->
                    ((List<?>)cell.get("position")).get(1).equals(origin.getY()-2)));
            for(int x=-3;x<=3;x++) for(int y=0;y<=4;y++)
                level.setBlock(origin.offset(x,y,2),Blocks.STONE.defaultBlockState(),2);
            var wall=PolicySensor.visible(player,tick);
            checks.put("wall_surface_visible",contains(wall,front,"minecraft:stone"));
            checks.put("wall_hides_log",!known(wall,target));
            checks.put("wall_hides_air",!known(wall,origin.offset(0,1,3)));
            clear(player,saved.keySet());
            var cornerTarget=origin.offset(3,1,3);
            level.setBlock(cornerTarget,Blocks.OAK_LOG.defaultBlockState(),2);
            for(int a=0;a<=4;a++) for(int y=0;y<=4;y++) {
                level.setBlock(origin.offset(2,y,a),Blocks.STONE.defaultBlockState(),2);
                level.setBlock(origin.offset(a,y,2),Blocks.STONE.defaultBlockState(),2);
            }
            checks.put("corner_hides_log",!known(PolicySensor.visible(player,tick),cornerTarget));
            clear(player,saved.keySet());
            level.setBlock(front,Blocks.OAK_LEAVES.defaultBlockState(),2);
            level.setBlock(target,Blocks.OAK_LOG.defaultBlockState(),2);
            var foliage=PolicySensor.visible(player,tick);
            checks.put("foliage_identity_visible",contains(foliage,front,"minecraft:oak_leaves"));
            checks.put("foliage_occludes_center",!known(foliage,target));
            clear(player,saved.keySet());
            level.setBlock(front,Blocks.WATER.defaultBlockState(),2);
            var fluid=PolicySensor.visible(player,tick);
            checks.put("fluid_identity_visible",contains(fluid,front,"minecraft:water"));
            checks.put("fluid_hides_air",!known(fluid,origin.offset(0,1,3)));
        } finally {
            saved.forEach((pos,state) -> level.setBlock(pos,state,2));
        }
        checks.put("fixture_restored",saved.entrySet().stream().allMatch(e -> level.getBlockState(e.getKey()).equals(e.getValue())));
        return Map.of("checks",Map.copyOf(checks),"passed",checks.values().stream().allMatch(Boolean::booleanValue),
                "sensor_profile","local-visibility-v1","reset_required",true);
    }

    private static void clear(ServerPlayer player,Set<BlockPos> positions) {
        for(var pos:positions) player.level().setBlock(pos,Blocks.AIR.defaultBlockState(),2);
    }
    private static boolean known(List<Map<String,Object>> cells,BlockPos pos) {
        return cells.stream().anyMatch(c -> c.get("position").equals(List.of(pos.getX(),pos.getY(),pos.getZ())));
    }
    private static boolean contains(List<Map<String,Object>> cells,BlockPos pos,String block) {
        return cells.stream().anyMatch(c -> c.get("position").equals(List.of(pos.getX(),pos.getY(),pos.getZ())) && c.get("block").equals(block));
    }
}
