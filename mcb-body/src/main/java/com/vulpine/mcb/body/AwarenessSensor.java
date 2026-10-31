package com.vulpine.mcb.body;

import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.level.block.state.BlockState;
import java.util.ArrayList;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Local refresh plus bounded incremental acquisition of the larger requested box. */
final class AwarenessSensor {
    private final AwarenessConfig config;
    private BlockPos anchor;
    private int cursor, sweep;
    private Map<String,Object> coverage = Map.of();
    // Section scanner state (preset "sections"): what was sent, and the BlockState palette.
    static final int SECTION_CELLS=4096, UNIFORM_COST=64, VERIFY_PER_UPDATE=24;
    record SectionData(int x,int y,int z,int uniform,int[] ids,int tick) {}
    record SectionBatch(List<SectionData> sections,Map<Integer,Map<String,Object>> facts) {}
    private final SectionScheduler scheduler=new SectionScheduler();
    private final IdentityHashMap<BlockState,Integer> stateIds=new IdentityHashMap<>();
    private final List<Map<String,Object>> stateFacts=new ArrayList<>();
    private SectionBatch sections=new SectionBatch(List.of(),Map.of());

    AwarenessSensor(AwarenessConfig config) { this.config=config; }
    Map<String,Object> coverage() { return coverage; }
    /** Whole sections produced by the last sample(); empty unless the preset is "sections". */
    SectionBatch sections() { return sections; }
    /** Only the sections scanned by the last sample(), without carried ones. */
    SectionBatch scanned() { return scanned; }
    private SectionBatch scanned=new SectionBatch(List.of(),Map.of());

    /**
     * Sections are sent once and resent only when they change, so a batch replaced
     * before the controller read it would be lost for good. Cohort 14 R1: the first
     * ~10 batches (the start area, nearest first) went out before Python polled.
     * Unread sections ride along in the next batch, so the first read delivers all
     * of them at once (rescanning them instead gave the first plan only 21 sections).
     */
    void carry(SectionBatch unread) { carried=unread.sections(); }
    private List<SectionData> carried=List.of();
    /** Python's MAX_BULK_SECTIONS is 1040; overflow is rescanned rather than lost. */
    static final int MAX_BATCH_SECTIONS=1024;

    List<Map<String,Object>> sample(ServerPlayer player,int tick) {
        long started=System.nanoTime();
        var center=player.blockPosition();
        if(!config.sections() && (anchor==null || Math.abs(center.getX()-anchor.getX())>16
                || Math.abs(center.getY()-anchor.getY())>16 || Math.abs(center.getZ()-anchor.getZ())>16)) {
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
        if(config.sections()) {
            scanSections(player,tick,center,result.size(),started);
            return List.copyOf(result);
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

    private void scanSections(ServerPlayer player,int tick,BlockPos center,int nearCells,long started) {
        var level=player.level();
        var plan=scheduler.plan(center.getX(),center.getY(),center.getZ(),config.horizontalRadius(),
                config.verticalRadius(),level.getMinY(),level.getMaxY(),
                (cx,cz)->level.getChunkSource().getChunkNow(cx,cz)!=null,VERIFY_PER_UPDATE,tick);
        var out=new ArrayList<SectionData>();
        var used=new LinkedHashMap<Integer,Map<String,Object>>();
        long deadline=System.nanoTime()+config.extraMillis()*1_000_000L;
        int cost=0,inspected=0,freshSent=0;
        var order=new ArrayList<SectionScheduler.Key>(plan.verify());
        order.addAll(plan.fresh());
        for(var key:order) {
            if(cost>=config.extraCells() || System.nanoTime()>=deadline) break;
            var chunk=level.getChunkSource().getChunkNow(key.x(),key.z());
            if(chunk==null) continue; // unloaded since planning: never sent, never assumed
            inspected++;
            var data=read(player,chunk,key,tick);
            long digest=digest(data);
            if(!scheduler.changed(key,digest)) { scheduler.unchanged(key,tick); continue; }
            scheduler.sent(key,digest,tick);
            if(!plan.verify().contains(key)) freshSent++;
            out.add(data);
            cost+=data.ids()==null?UNIFORM_COST:SECTION_CELLS;
            if(data.ids()==null) used.computeIfAbsent(data.uniform(),stateFacts::get);
            else for(int id:data.ids()) used.computeIfAbsent(id,stateFacts::get);
        }
        scanned=new SectionBatch(List.copyOf(out),Map.copyOf(used));
        var fresh=new java.util.HashSet<SectionScheduler.Key>();
        for(var d:out) fresh.add(new SectionScheduler.Key(d.x(),d.y(),d.z()));
        for(var d:carried) {
            var key=new SectionScheduler.Key(d.x(),d.y(),d.z());
            if(fresh.contains(key)) continue; // rescanned this update: the newer copy wins
            if(out.size()>=MAX_BATCH_SECTIONS) { scheduler.forget(key); continue; }
            out.add(d);
            if(d.ids()==null) used.computeIfAbsent(d.uniform(),stateFacts::get);
            else for(int id:d.ids()) used.computeIfAbsent(id,stateFacts::get);
        }
        carried=List.of();
        sections=new SectionBatch(List.copyOf(out),Map.copyOf(used));
        coverage=Map.ofEntries(Map.entry("kind","section_scan"),Map.entry("unknown_is_air",false),
                Map.entry("sections_sent",out.size()),Map.entry("sections_inspected",inspected),
                Map.entry("sections_pending",plan.fresh().size()-freshSent),
                Map.entry("sections_loaded_in_range",plan.loadedInRange()),
                Map.entry("sections_tracked",scheduler.sentCount()),
                Map.entry("unloaded_chunks_in_range",plan.unloadedChunks()),
                Map.entry("scan_cost_cells",cost),Map.entry("returned_cells",nearCells),
                Map.entry("sample_tick",tick),Map.entry("sample_ms",(System.nanoTime()-started)/1_000_000.0),
                Map.entry("full_coverage_claimed",false));
    }

    private SectionData read(ServerPlayer player,net.minecraft.world.level.chunk.LevelChunk chunk,
                             SectionScheduler.Key key,int tick) {
        var section=chunk.getSection(chunk.getSectionIndex(key.y()*16));
        var ids=new int[SECTION_CELLS];
        boolean uniform=true;
        for(int x=0;x<16;x++) for(int y=0;y<16;y++) for(int z=0;z<16;z++) {
            var state=section.getBlockState(x,y,z);
            Integer id=stateIds.get(state);
            if(id==null) id=register(player,state,new BlockPos(key.x()*16+x,key.y()*16+y,key.z()*16+z),tick);
            int i=x<<8|y<<4|z;
            ids[i]=id;
            uniform&=id==ids[0];
        }
        return uniform?new SectionData(key.x(),key.y(),key.z(),ids[0],null,tick)
                :new SectionData(key.x(),key.y(),key.z(),-1,ids,tick);
    }

    // ponytail: facts are cached per BlockState; position-dependent collision offsets
    // (bamboo, dripstone, flowers) use the first position seen.
    private int register(ServerPlayer player,BlockState state,BlockPos pos,int tick) {
        var cell=PolicySensor.cell(player,tick,pos,false);
        var facts=new LinkedHashMap<>(cell);
        facts.remove("position"); facts.remove("observed_tick");
        int id=stateFacts.size();
        stateFacts.add(Map.copyOf(facts)); stateIds.put(state,id);
        return id;
    }

    static long digest(SectionData data) {
        if(data.ids()==null) return -1L-data.uniform();
        long h=1;
        for(int id:data.ids()) h=h*31+id;
        return h;
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
