package com.vulpine.mclab;

import com.google.gson.JsonObject;
import net.minecraft.core.BlockPos;
import net.minecraft.server.MinecraftServer;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
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

    DevelopmentHill(MinecraftServer server, JsonObject request, Vec3 start, Vec3 goal) {
        this.server=server;
        kind=request.get("development_hill").getAsString();
        variant=request.get("hill_variant").getAsInt();
        int height=kind.equals("prefetch")?0:kind.equals("mixed")?4:kind.equals("place")?2:3;
        int goalZ=kind.equals("prefetch")?40+variant%4+17*(variant/4):11;
        int goalX=kind.equals("prefetch")?variant%3:0;
        int maxZ=kind.equals("prefetch")?80:14;
        area=new AABB(X-6,Y-3,Z-3,X+7,Y+9,Z+maxZ+1);
        if(!Set.of("walk","cut","place","mixed","prefetch").contains(kind) || variant<0 || variant>9
                || !start.equals(new Vec3(X+0.5,Y,Z+0.5))
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
                int top=0;
                boolean bedrock=false;
                if(kind.equals("mixed")) {
                    if(Math.abs(pos.getX()-X)==1 || z==-3 || z==14) {
                        top=7; bedrock=true;
                    } else if(pos.getX()==X && z>=8+variant/4) {
                        top=4; bedrock=true;
                    } else if(pos.getX()==X && z>=2+variant%4) top=2;
                } else if(!kind.equals("prefetch"))
                if(Math.abs(pos.getX()-X)<=2+variant/4) {
                    int offset=variant%4;
                    if(kind.equals("walk")) top=z<2+offset?0:z<4+offset?1:z<6+offset?2:3;
                    else if(z>=4+offset) top=height;
                }
                boolean solid=pos.getY()<Y || ((kind.equals("mixed") || z<=12) && pos.getY()<Y+top);
                var state=solid?(bedrock?Blocks.BEDROCK:Blocks.STONE).defaultBlockState():Blocks.AIR.defaultBlockState();
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
        return Map.of("kind",kind,"variant",variant,"removed",removed,"added",added,"changes",changes);
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
