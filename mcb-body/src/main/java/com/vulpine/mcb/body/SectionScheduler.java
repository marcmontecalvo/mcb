package com.vulpine.mcb.body;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.function.BiPredicate;

/**
 * Pure bookkeeping for the section scanner: which 16x16x16 sections to look at next.
 * Unloaded chunks are never candidates. Body movement only changes the ordering.
 */
final class SectionScheduler {
    record Key(int x,int y,int z) {}
    record Plan(List<Key> fresh,List<Key> verify,int unloadedChunks,int loadedInRange) {}
    private static final class Entry { long digest; long checked; }
    /** A verification costs one read; priority = staleness discounted by distance. */
    static final int DISTANCE_WEIGHT=4;
    private static final int RETAIN_MARGIN_CHUNKS=2;
    private final Map<Key,Entry> sent=new HashMap<>();

    int sentCount() { return sent.size(); }
    boolean knows(Key key) { return sent.containsKey(key); }

    /** Block bounds are inclusive; {@code loaded} takes chunk x,z. */
    Plan plan(int bx,int by,int bz,int radius,int verticalRadius,int minY,int maxY,
              BiPredicate<Integer,Integer> loaded,int verifyCount,long tick) {
        int cx0=Math.floorDiv(bx-radius,16),cx1=Math.floorDiv(bx+radius,16);
        int cz0=Math.floorDiv(bz-radius,16),cz1=Math.floorDiv(bz+radius,16);
        int lowY=verticalRadius==-1?minY:Math.max(minY,by-verticalRadius);
        int highY=verticalRadius==-1?maxY:Math.min(maxY,by+verticalRadius);
        int sy0=Math.floorDiv(lowY,16),sy1=Math.floorDiv(highY,16);
        int bcx=Math.floorDiv(bx,16),bsy=Math.floorDiv(by,16),bcz=Math.floorDiv(bz,16);
        var fresh=new ArrayList<Key>();
        var verify=new ArrayList<Key>();
        int unloaded=0,inRange=0;
        for(int cx=cx0;cx<=cx1;cx++) for(int cz=cz0;cz<=cz1;cz++) {
            if(!loaded.test(cx,cz)) { unloaded++; continue; }
            for(int sy=sy0;sy<=sy1;sy++) {
                var key=new Key(cx,sy,cz);
                inRange++;
                (sent.containsKey(key)?verify:fresh).add(key);
            }
        }
        int keep=radius/16+1+RETAIN_MARGIN_CHUNKS;
        sent.keySet().removeIf(k->Math.abs(k.x-bcx)>keep || Math.abs(k.z-bcz)>keep || !loaded.test(k.x,k.z));
        fresh.sort(Comparator.comparingLong((Key k)->distance(k,bcx,bsy,bcz)).thenComparing(Key::x).thenComparing(Key::y).thenComparing(Key::z));
        verify.sort(Comparator.comparingLong((Key k)->sent.get(k).checked+(long)DISTANCE_WEIGHT*chebyshev(k,bcx,bsy,bcz))
                .thenComparing(Key::x).thenComparing(Key::y).thenComparing(Key::z));
        return new Plan(fresh,verify.subList(0,Math.min(verifyCount,verify.size())),unloaded,inRange);
    }

    /** Record an inspection; true when the section is new or differs from what was last sent. */
    boolean changed(Key key,long digest) {
        var e=sent.get(key);
        return e==null || e.digest!=digest;
    }

    void sent(Key key,long digest,long tick) {
        var e=sent.computeIfAbsent(key,k->new Entry());
        e.digest=digest; e.checked=tick;
    }

    /** The batch carrying this section was never delivered: treat it as never sent. */
    void forget(Key key) { sent.remove(key); }

    void unchanged(Key key,long tick) {
        var e=sent.get(key);
        if(e!=null) e.checked=tick;
    }

    private static long distance(Key k,int bcx,int bsy,int bcz) {
        long dx=k.x-bcx,dy=k.y-bsy,dz=k.z-bcz;
        return dx*dx+dy*dy+dz*dz;
    }

    private static int chebyshev(Key k,int bcx,int bsy,int bcz) {
        return Math.max(Math.abs(k.x-bcx),Math.max(Math.abs(k.y-bsy),Math.abs(k.z-bcz)));
    }
}
