package com.vulpine.mcb.body;

import com.google.gson.Gson;
import com.google.gson.JsonParser;
import java.util.List;
import java.util.Map;

/** Section scan ordering, unloaded chunks, resend bookkeeping and the bulk wire form. */
public final class SectionSchedulerTest {
    private static void check(boolean ok,String what) { if(!ok) throw new AssertionError(what); }

    public static void main(String[] args) {
        // Body at chunk (0,0), y=64 (section 4); radius 64 => chunks -4..4; vertical +-48 => sections 1..7.
        var s=new SectionScheduler();
        var plan=s.plan(8,64,8,64,48,-64,319,(x,z)->true,24,0);
        check(plan.fresh().size()==9*9*7,"expected 567 sections, got "+plan.fresh().size());
        check(plan.verify().isEmpty(),"nothing to verify before first send");
        long previous=-1;
        for(var k:plan.fresh()) {
            long d=(long)k.x()*k.x()+(long)(k.y()-4)*(k.y()-4)+(long)k.z()*k.z();
            check(d>=previous,"nearest first"); previous=d;
        }
        check(plan.fresh().get(0).equals(new SectionScheduler.Key(0,4,0)),"body section first");

        // Unloaded chunks are never candidates (and never assumed).
        var holes=s.plan(8,64,8,64,48,-64,319,(x,z)->x>=0,24,0);
        check(holes.fresh().stream().allMatch(k->k.x()>=0),"unloaded chunk scheduled");
        check(holes.unloadedChunks()==4*9,"unloaded count "+holes.unloadedChunks());

        // Sent sections are not resent unless the digest changes.
        var first=plan.fresh().get(0);
        check(s.changed(first,42),"new section is changed");
        s.sent(first,42,0);
        check(!s.changed(first,42) && s.changed(first,43),"digest governs resend");
        var next=s.plan(8,64,8,64,48,-64,319,(x,z)->true,24,5);
        check(next.fresh().size()==9*9*7-1 && next.verify().equals(List.of(first)),"sent leaves fresh list");
        // An undelivered batch is forgotten: the section is fresh (and nearest-first) again.
        s.forget(first);
        check(s.changed(first,42),"forgotten section is resent");
        check(s.plan(8,64,8,64,48,-64,319,(x,z)->true,24,6).fresh().get(0).equals(first),"forgotten is fresh");
        s.sent(first,42,6);

        // Moving the body only reorders: nothing is restarted or forgotten.
        var moved=s.plan(8+32,64,8,64,48,-64,319,(x,z)->true,24,10);
        check(moved.verify().contains(first) && moved.fresh().size()>=9*9*7-1-9*7,"movement keeps sent state");
        // Far away or unloaded sections are pruned (memory bound).
        s.plan(8+1000,64,8,64,48,-64,319,(x,z)->true,24,11);
        check(s.sentCount()==0,"pruned after leaving range");

        // Verification: nearer and staler first; budget caps the list.
        var v=new SectionScheduler();
        for(var k:plan.fresh()) v.sent(k,1,0);
        var ver=v.plan(8,64,8,64,48,-64,319,(x,z)->true,24,100);
        check(ver.verify().size()==24 && ver.fresh().isEmpty(),"verify budget");
        check(ver.verify().get(0).equals(new SectionScheduler.Key(0,4,0)),"nearest verified first");

        // Wire form: uniform + dense sections share one palette, sparse schema is untouched.
        var air=Map.<String,Object>of("block","minecraft:air","fluid","minecraft:empty","replaceable",true,
                "properties",Map.of(),"collision_boxes",List.of());
        var stone=Map.<String,Object>of("block","minecraft:stone","fluid","minecraft:empty","replaceable",false,
                "properties",Map.of(),"collision_boxes",List.of(List.of(0.0,0.0,0.0,1.0,1.0,1.0)));
        var ids=new int[4096]; ids[4095]=1;
        var batch=new TerrainBatch(List.of(),9,new AwarenessSensor.SectionBatch(List.of(
                new AwarenessSensor.SectionData(-1,2,3,0,null,9),
                new AwarenessSensor.SectionData(0,0,0,-1,ids,9)),Map.of(0,air,1,stone)));
        var data=JsonParser.parseString(new Gson().toJson(Map.of("t",batch))).getAsJsonObject().getAsJsonObject("t");
        check(data.get("bulk_cells").getAsInt()==8192 && data.getAsJsonArray("palette").size()==2,"bulk header");
        check(data.getAsJsonArray("uniform_sections").get(0).equals(
                JsonParser.parseString("{\"section\":[-1,2,3],\"state\":0,\"tick\":9}")),"uniform form");
        var dense=data.getAsJsonArray("dense_sections").get(0).getAsJsonObject().getAsJsonArray("states");
        check(dense.size()==4096 && dense.get(4095).getAsInt()==1 && dense.get(0).getAsInt()==0,"dense form");
        System.out.println("Section scanner: ordering, unloaded chunks, resend, pruning and bulk wire form verified");
    }
}
