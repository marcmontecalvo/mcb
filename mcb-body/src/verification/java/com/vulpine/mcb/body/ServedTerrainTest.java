package com.vulpine.mcb.body;

import java.util.Map;

public final class ServedTerrainTest {
    public static void main(String[] args) {
        var served=new ServedTerrain();
        var first=new Object();
        var snapshot=Map.<String,Object>of("terrain_sections",first);
        if(!served.unread(first)) throw new AssertionError("unserved batch must be carried");
        served.serve(()->snapshot);
        if(served.unread(first)) throw new AssertionError("served batch was carried and would be resent");
        if(!served.unread(new Object())) throw new AssertionError("newer unserved batch must be carried");
        // A batch replaced while its encode/send is in flight: marked at read time, so the scan
        // that runs during the send sees it as served.
        var second=new Object();
        served.serve(()->Map.of("terrain_sections",second));
        if(served.unread(second)) throw new AssertionError("in-flight batch carried into next scan");
    }
}
