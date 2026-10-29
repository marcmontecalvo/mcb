package com.vulpine.mcb.lab;

import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.server.MinecraftServer;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.BlockStateProperties;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.Vec3;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Disposable, explicitly requested development hill. Never a qualification course. */
final class DevelopmentHill {
    private static final int X=1280, Y=80, Z=1280;
    private final MinecraftServer server;
    private final String kind;
    private final int variant;
    private final Map<BlockPos,BlockState> original=new LinkedHashMap<>();
    private final Map<BlockPos,BlockState> prepared=new LinkedHashMap<>();
    private final AABB area;
    private boolean lowAirInjected;

    DevelopmentHill(MinecraftServer server, JsonObject request, Vec3 start, Vec3 goal) {
        this.server=server;
        kind=request.get("development_hill").getAsString();
        variant=request.get("hill_variant").getAsInt();
        int height=Set.of("prefetch","drop","bridge","gap","water").contains(kind)?0:kind.equals("pillar")?3+variant%2:Set.of("mixed","staircase").contains(kind)?4:kind.equals("place")?2:3;
        int startHeight=kind.equals("drop")?1+variant%3:0;
        int goalZ=kind.equals("prefetch")?40+variant%4+17*(variant/4):kind.equals("staircase")?8+variant:kind.equals("pillar")?5+variant/2:kind.equals("gap")?7+variant/2+variant%2:kind.equals("water")?8+variant/2+variant%2:11;
        int goalX=kind.equals("prefetch")?variant%3:0;
        int maxZ=kind.equals("prefetch")?80:Set.of("staircase","pillar","water").contains(kind)?20:14;
        area=new AABB(X-6,Y-3,Z-3,X+7,Y+9,Z+maxZ+1);
        if(!Set.of("walk","cut","place","mixed","prefetch","slab","stairs","drop","bridge","staircase","pillar","gap","water").contains(kind) || variant<0 || variant>9
                || !start.equals(new Vec3(X+0.5,Y+startHeight,Z+0.5))
                || !goal.equals(new Vec3(X+goalX+0.5,Y+height,Z+goalZ+0.5))
                || request.get("mobs").getAsBoolean()
                || request.get("goal_radius").getAsDouble()!=1)
            throw new IllegalArgumentException("development_hill_bounds");
        if(server.getPlayerList().getPlayers().stream().anyMatch(p->p.getBoundingBox().intersects(area)))
            throw new IllegalStateException("player_in_development_hill");
        var level=server.overworld();
        for(int x=-6;x<=6;x++) for(int y=-3;y<=8;y++) for(int z=-3;z<=maxZ;z++) {
            var pos=new BlockPos(X+x,Y+y,Z+z);
            if(level.getBlockEntity(pos)!=null) throw new IllegalStateException("block_entity_in_development_hill");
            original.put(pos,level.getBlockState(pos));
        }
        try {
            for(var pos:original.keySet()) {
                int z=pos.getZ()-Z;
                double top=0;
                boolean bedrock=false;
                if(kind.equals("pillar")) {
                    if(Math.abs(pos.getX()-X)==1 || z==-3 || z==maxZ) { top=7; bedrock=true; }
                    else if(pos.getX()==X && z>=1+variant/2) { top=height; bedrock=true; }
                } else if(kind.equals("staircase")) {
                    if(Math.abs(pos.getX()-X)==1 || z==-3 || z==maxZ) { top=7; bedrock=true; }
                    else if(pos.getX()==X && z>=4+variant) { top=4; bedrock=true; }
                } else if(Set.of("bridge","gap","water").contains(kind)) {
                    if(Math.abs(pos.getX()-X)==1 || z==-3 || z==maxZ) { top=7; bedrock=true; }
                } else if(kind.equals("mixed")) {
                    if(Math.abs(pos.getX()-X)==1 || z==-3 || z==14) {
                        top=7; bedrock=true;
                    } else if(pos.getX()==X && z>=8+variant/4) {
                        top=4; bedrock=true;
                    } else if(pos.getX()==X && z>=2+variant%4) top=2;
                } else if(!kind.equals("prefetch"))
                if(Math.abs(pos.getX()-X)<=2+variant/4) {
                    int offset=variant%4;
                    if(kind.equals("drop")) top=z<4+offset?startHeight:0;
                    else if(kind.equals("slab")) top=Math.min(3,Math.max(0,(z-(2+offset)+1)*0.5));
                    else if(kind.equals("walk") || kind.equals("stairs")) top=z<2+offset?0:z<4+offset?1:z<6+offset?2:3;
                    else if(z>=4+offset) top=height;
                }
                boolean solid=pos.getY()<Y || ((Set.of("mixed","bridge","staircase","pillar","gap","water").contains(kind) || z<=12) && pos.getY()<Y+top);
                var state=solid?(bedrock?Blocks.BEDROCK:Blocks.STONE).defaultBlockState():Blocks.AIR.defaultBlockState();
                if(kind.equals("bridge") && pos.getX()==X && z>=2+variant%4
                        && z<2+variant%4+2+variant%3 && pos.getY()>=Y-2 && pos.getY()<Y)
                    state=Blocks.AIR.defaultBlockState();
                if(kind.equals("gap") && pos.getX()==X && z>=2+variant/2
                        && z<2+variant/2+1+variant%2 && pos.getY()>=Y-2 && pos.getY()<Y)
                    state=Blocks.AIR.defaultBlockState();
                if(kind.equals("water") && pos.getX()==X && z>=2+variant/2
                        && z<2+variant/2+3+variant%2 && pos.getY()>=Y-2 && pos.getY()<Y)
                    state=Blocks.WATER.defaultBlockState();
                if(kind.equals("slab") && top%1!=0 && z<=12 && pos.getY()==Y+Math.floor(top))
                    state=Blocks.STONE_SLAB.defaultBlockState();
                if(kind.equals("stairs") && top>0 && pos.getY()==Y+top-1 && z<=12
                        && (z==2+variant%4 || z==4+variant%4 || z==6+variant%4))
                    state=Blocks.STONE_BRICK_STAIRS.defaultBlockState()
                            .setValue(BlockStateProperties.HORIZONTAL_FACING,Direction.SOUTH);
                if(kind.equals("mixed") && pos.getX()==X && z>=0 && z<=2+variant%4 && pos.getY()==Y+2)
                    state=Blocks.STONE.defaultBlockState();
                level.setBlock(pos,state,2);
                prepared.put(pos,state);
            }
        } catch(RuntimeException error) { restore(); throw error; }
    }

    Map<String,Object> audit() {
        int removed=0,added=0;
        var changes=new java.util.ArrayList<Map<String,Object>>();
        for(var entry:prepared.entrySet()) {
            var after=server.overworld().getBlockState(entry.getKey());
            if(after.equals(entry.getValue())) continue;
            if(entry.getValue().isAir() && !after.isAir()) added++;
            if(!entry.getValue().isAir() && after.isAir()) removed++;
            var p=entry.getKey();
            changes.add(Map.of("position",List.of(p.getX(),p.getY(),p.getZ()),
                    "before",entry.getValue().toString(),"after",after.toString()));
        }
        return Map.of("kind",kind,"variant",variant,"removed",removed,"added",added,"changes",changes,
                "low_air_injected",lowAirInjected);
    }

    void diagnosticTick(net.minecraft.server.level.ServerPlayer player) {
        if(kind.equals("water") && variant%2==1 && !lowAirInjected && player.isInWater()) {
            player.setAirSupply(90);
            lowAirInjected=true;
        }
    }

    void restore() {
        original.forEach((pos,state)->server.overworld().setBlock(pos,state,2));
        if(original.entrySet().stream().anyMatch(e->!server.overworld().getBlockState(e.getKey()).equals(e.getValue())))
            throw new IllegalStateException("development_hill_restore_mismatch");
        // Only drops within this disposable fixture, after the controlled run.
        server.overworld().getEntitiesOfClass(net.minecraft.world.entity.item.ItemEntity.class,area)
                .forEach(net.minecraft.world.entity.item.ItemEntity::discard);
    }
}
