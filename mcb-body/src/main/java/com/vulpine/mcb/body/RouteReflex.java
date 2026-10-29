package com.vulpine.mcb.body;

import java.util.List;
import java.util.Map;
import java.util.Set;

/** Local retreat objective from admitted entity observations; never waits for Python. */
final class RouteReflex {
    private static final Set<String> HOSTILES = Set.of("minecraft:zombie","minecraft:husk","minecraft:drowned",
            "minecraft:skeleton","minecraft:stray","minecraft:bogged","minecraft:spider","minecraft:cave_spider",
            "minecraft:creeper","minecraft:witch","minecraft:pillager","minecraft:vindicator","minecraft:ravager");
    private RouteReflex() { }
    static List<RouteMotor.Point> retreat(RouteMotor.Pose pose,List<Map<String,Object>> entities,RouteMotor.Geometry geometry) {
        if(!pose.grounded()) return List.of();
        List<?> threat=null; double nearest=Double.POSITIVE_INFINITY;
        for(var entity:entities) {
            if(!HOSTILES.contains(entity.get("type")) || !(entity.get("position") instanceof List<?> point) || point.size()!=3) continue;
            double x=((Number)point.get(0)).doubleValue(),y=((Number)point.get(1)).doubleValue(),z=((Number)point.get(2)).doubleValue();
            double distance=Math.sqrt(Math.pow(x-pose.x(),2)+Math.pow(y-pose.y(),2)+Math.pow(z-pose.z(),2));
            double trigger="minecraft:creeper".equals(entity.get("type"))?4:2.8;
            if(distance<trigger && distance<nearest) { nearest=distance; threat=point; }
        }
        if(threat==null) return List.of();
        var source=new RouteMotor.Point(Math.floor(pose.x()),Math.rint(pose.y()*2)/2,Math.floor(pose.z()));
        if(!geometry.standable(source)) return List.of();
        RouteMotor.Point best=null; double score=nearest;
        double tx=((Number)threat.get(0)).doubleValue(),tz=((Number)threat.get(2)).doubleValue();
        for(int[] direction:new int[][]{{1,0},{-1,0},{0,1},{0,-1}}) {
            var target=new RouteMotor.Point(source.x()+direction[0],source.y(),source.z()+direction[1]);
            double distance=Math.hypot(target.x()+.5-tx,target.z()+.5-tz);
            double yaw=Math.toDegrees(Math.atan2(-(target.x()+.5-pose.x()),target.z()+.5-pose.z()));
            var projected=new RouteMotor.Pose(pose.x(),pose.y(),pose.z(),pose.vx(),pose.vz(),yaw,true,pose.food());
            if(distance>score && geometry.standable(target)
                    && geometry.safe(projected,target,source.y(),.6,false,0)) { score=distance; best=target; }
        }
        return best==null?List.of():List.of(source,best);
    }
}
