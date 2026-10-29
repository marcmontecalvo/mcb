package com.vulpine.mcb.body;

import java.util.List;
import java.util.Map;

public final class RouteReflexTest {
    private static int checks;
    private static void check(boolean yes,String label) { checks++; if(!yes) throw new AssertionError(label); }
    public static void main(String[] args) {
        RouteMotor.Geometry safe=new RouteMotor.Geometry() {
            public boolean standable(RouteMotor.Point p) { return true; }
            public boolean safe(RouteMotor.Pose p,RouteMotor.Point t,double y,double d,boolean j,int drop) { return true; }
        };
        var pose=new RouteMotor.Pose(.5,1,.5,0,0,0,true,20);
        var zombie=List.<Map<String,Object>>of(Map.of("type","minecraft:zombie","position",List.of(2,1,.5)));
        var route=RouteReflex.retreat(pose,zombie,safe);
        check(route.size()==2 && route.get(1).x()==-1,"retreat away from nearby zombie");
        check(RouteReflex.retreat(pose,List.of(Map.of("type","minecraft:player","position",List.of(1,1,.5))),safe).isEmpty(),"no retreat from household player");
        check(RouteReflex.retreat(pose,List.of(Map.of("type","minecraft:zombie","position",List.of(10,1,.5))),safe).isEmpty(),"distant mob no reflex");
        RouteMotor.Geometry unknown=new RouteMotor.Geometry() {
            public boolean standable(RouteMotor.Point p) { return false; }
            public boolean safe(RouteMotor.Pose p,RouteMotor.Point t,double y,double d,boolean j,int drop) { return false; }
        };
        check(RouteReflex.retreat(pose,zombie,unknown).isEmpty(),"never retreat into unknown");
        check(RouteReflex.retreat(new RouteMotor.Pose(.5,1,.5,0,0,0,false,20),zombie,safe).isEmpty(),"do not interrupt jump in flight");
        System.out.println("RouteReflex: "+checks+" local retreat checks passed");
    }
}
