package com.vulpine.mclab;

import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerPlayer;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/** Local refresh plus bounded incremental acquisition of the larger requested box. */
final class AwarenessSensor {
    private final AwarenessConfig config;
    private BlockPos anchor;
    private int cursor, sweep;
    private Map<String,Object> coverage = Map.of();

    AwarenessSensor(AwarenessConfig config) { this.config=config; }
    Map<String,Object> coverage() { return coverage; }

    List<Map<String,Object>> sample(ServerPlayer player,int tick) {
        long started=System.nanoTime();
        var center=player.blockPosition();
        if(anchor==null || Math.abs(center.getX()-anchor.getX())>16
                || Math.abs(center.getY()-anchor.getY())>16 || Math.abs(center.getZ()-anchor.getZ())>16) {
            anchor=center; cursor=0; sweep++;
        }
        var result=new ArrayList<Map<String,Object>>();
        int nearX=Math.min(8,config.horizontalRadius());
        int nearY=config.verticalRadius()==-1?4:Math.min(4,config.verticalRadius());
        int unavailable=0;
        // Refresh immediate support/clearance every update, independent of distant scanning.
        for(int x=-nearX;x<=nearX;x++) for(int y=-nearY;y<=nearY;y++) for(int z=-nearX;z<=nearX;z++) {
            var pos=center.offset(x,y,z);
            if(!player.level().isInsideBuildHeight(pos)) continue;
            var cell=PolicySensor.cell(player,tick,pos,config.occluded());
            if(cell!=null) result.add(cell);
        }
        // A narrow vertical spine covers jump-placement shafts and their
        // adjacent exits without expanding the requested awareness envelope.
        int spineX=Math.min(2,config.horizontalRadius());
        int spineY=config.verticalRadius()==-1?8:Math.min(8,config.verticalRadius());
        for(int x=-spineX;x<=spineX;x++) for(int y=-spineY;y<=spineY;y++) for(int z=-spineX;z<=spineX;z++) {
            if(Math.abs(y)<=nearY) continue;
            var pos=center.offset(x,y,z);
            if(!player.level().isInsideBuildHeight(pos)) continue;
            var cell=PolicySensor.cell(player,tick,pos,config.occluded());
            if(cell!=null) result.add(cell);
        }
        int radius=config.horizontalRadius();
        int low=config.verticalRadius()==-1?player.level().getMinY():Math.max(player.level().getMinY(),anchor.getY()-config.verticalRadius());
        int high=config.verticalRadius()==-1?player.level().getMaxY():Math.min(player.level().getMaxY(),anchor.getY()+config.verticalRadius());
        var volume=new AwarenessVolume(radius,low-anchor.getY(),high-anchor.getY());
        int total=volume.size();
        int inspected=0;
        long extraStarted=System.nanoTime();
        while(cursor<total && inspected<config.extraCells()
                && System.nanoTime()-extraStarted<config.extraMillis()*1_000_000L) {
            var offset=volume.at(cursor++); inspected++;
            var pos=anchor.offset(offset.x(),offset.y(),offset.z());
            // The anchor survives ordinary movement, but live acquisition never exceeds current reach.
            if(!inRange(config,center,pos)) continue;
            if(inNearRefresh(config,center,pos)) continue;
            if(player.level().getChunkSource().getChunkNow(pos.getX()>>4,pos.getZ()>>4)==null) {
                unavailable++; continue;
            }
            var cell=PolicySensor.cell(player,tick,pos,config.occluded());
            if(cell!=null) result.add(cell);
        }
        boolean complete=cursor>=total;
        coverage=Map.ofEntries(Map.entry("kind","incremental_observations"),Map.entry("unknown_is_air",false),
                Map.entry("sweep",sweep),Map.entry("sweep_cursor",cursor),Map.entry("sweep_volume",total),
                Map.entry("sweep_finished",complete),Map.entry("inspected_extra_cells",inspected),
                Map.entry("unavailable_extra_cells",unavailable),Map.entry("returned_cells",result.size()),
                Map.entry("sample_tick",tick),Map.entry("sample_ms",(System.nanoTime()-started)/1_000_000.0),
                Map.entry("anchor",List.of(anchor.getX(),anchor.getY(),anchor.getZ())),
                Map.entry("full_coverage_claimed",false));
        if(complete) { cursor=0; anchor=center; sweep++; }
        return List.copyOf(result);
    }

    static boolean inRange(AwarenessConfig config,BlockPos center,BlockPos pos) {
        return Math.abs((long)pos.getX()-center.getX())<=config.horizontalRadius()
                && Math.abs((long)pos.getZ()-center.getZ())<=config.horizontalRadius()
                && (config.verticalRadius()==-1 || Math.abs((long)pos.getY()-center.getY())<=config.verticalRadius());
    }

    static boolean inNearRefresh(AwarenessConfig config,BlockPos center,BlockPos pos) {
        return AwarenessVolume.nearRefresh(config,(long)pos.getX()-center.getX(),
                (long)pos.getY()-center.getY(),(long)pos.getZ()-center.getZ());
    }
}
