package com.vulpine.mcb.body;

import com.google.gson.JsonParser;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** Native routing safety and state-machine tests; no server or external client. */
public final class RouteMotorTest {
    private record Key(int x, int y, int z) { }
    private static int checks;
    private static void check(boolean yes, String label) {
        checks++; if (!yes) throw new AssertionError(label);
    }
    private static Map<Key, Map<String,Object>> floor() {
        var cells = new HashMap<Key,Map<String,Object>>();
        for (int x=-3;x<=8;x++) for(int z=-3;z<=8;z++) for(int y=-3;y<=6;y++)
            cells.put(new Key(x,y,z),Map.of("block",y==0?"minecraft:stone":"minecraft:air",
                    "collision_boxes",y==0?List.of(List.of(0,0,0,1,1,1)):List.of(),"fluid","minecraft:empty"));
        return cells;
    }
    private static RouteMotor motor(List<RouteMotor.Point> points) {
        return new RouteMotor("test",points,new RouteMotor.Policy(true,true,true,3));
    }
    private static RouteMotor.Pose pose(double x,double y,double z,double yaw) {
        return new RouteMotor.Pose(x,y,z,0,0,yaw,true,20);
    }
    private static RouteGeometry geometry(Map<Key,Map<String,Object>> cells) {
        return new RouteGeometry((x,y,z)->cells.get(new Key(x,y,z)));
    }
    private static void reject(Runnable action,String label) {
        try { action.run(); throw new AssertionError(label); }
        catch (IllegalArgumentException expected) { checks++; }
    }
    public static void main(String[] args) {
        var route=List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,0),new RouteMotor.Point(3,1,0));
        var cells=floor(); var motor=motor(route);
        var command=motor.tick(pose(.5,1,.5,-90),geometry(cells));
        check(command.forward()>0 && command.sprint(),"known straight sprint");
        check(command.yaw()==0 && !command.jump(),"straight no turn/jump");
        motor.tick(pose(1.5,1,.5,-90),geometry(cells));
        motor.tick(pose(2.5,1,.5,-90),geometry(cells));
        check(motor.tick(pose(3.5,1,.5,-90),geometry(cells)).forward()==0,"completion releases");
        check(motor.status().get("state").equals("done"),"completed destination");
        motor=motor(route); motor.stop("explicit");
        check(motor.tick(pose(.5,1,.5,-90),geometry(cells)).forward()==0,"cancel cannot move");
        check(motor.status().get("reason").equals("explicit"),"cancel reason retained");
        motor=motor(route); motor.tick(pose(6.5,1,.5,-90),geometry(cells));
        check(!motor.active(),"displaced start rejected");
        var unavailable=motor(route); var missing=floor(); missing.remove(new Key(1,1,0));
        unavailable.tick(pose(.5,1,.5,-90),geometry(missing));
        check(!unavailable.active(),"unknown headroom refuses");
        var changed=motor(route); var hole=floor(); hole.put(new Key(1,0,0),Map.of("block","minecraft:air","collision_boxes",List.of()));
        changed.tick(pose(.5,1,.5,-90),geometry(hole));
        check(!changed.active(),"missing destination support refuses");
        var hazard=floor(); hazard.put(new Key(1,1,0),Map.of("block","minecraft:wither_rose","collision_boxes",List.of()));
        check(!geometry(hazard).standable(route.get(1)),"hazard clearance refuses");
        var wet=floor(); wet.put(new Key(1,1,0),Map.of("block","minecraft:air","fluid","minecraft:water","collision_boxes",List.of()));
        check(!geometry(wet).standable(route.get(1)),"fluid refuses");
        reject(()->motor(List.of(route.get(0),new RouteMotor.Point(2,1,0))),"ordinary route cannot imply gap");
        reject(()->new RouteMotor("x",List.of(route.get(0),new RouteMotor.Point(1,1,1)),new RouteMotor.Policy(false,false,false,1)),"diagonal permission");
        reject(()->new RouteMotor("x",List.of(route.get(0),new RouteMotor.Point(1,0,0)),new RouteMotor.Policy(false,false,false,0)),"drop permission");
        reject(()->motor(List.of(route.get(0),new RouteMotor.Point(Double.NaN,1,0))),"nonfinite route");
        reject(()->motor(List.of(route.get(0),new RouteMotor.Point(.5,1,0))),"nonintegral column");
        var json=JsonParser.parseString("{\"route_id\":\"r\",\"route\":[[0,1,0],[1,1,0]],\"movement_policy\":{\"allow_diagonals\":true,\"allow_sprint\":true,\"allow_shapes\":true,\"max_drop\":3,\"max_gap\":2,\"allow_swim\":false}}").getAsJsonObject();
        check(RouteMotor.parse(json).active(),"wire roundtrip");
        var bad=json.deepCopy(); bad.getAsJsonObject("movement_policy").addProperty("max_drop",true);
        reject(()->RouteMotor.parse(bad),"coercion rejected");
        var swimming=json.deepCopy(); swimming.getAsJsonObject("movement_policy").addProperty("allow_swim",true);
        reject(()->RouteMotor.parse(swimming),"land route cannot imply swimming");
        var badGap=json.deepCopy(); badGap.getAsJsonObject("movement_policy").addProperty("max_gap",-1);
        reject(()->RouteMotor.parse(badGap),"invalid gap policy");
        var badId=json.deepCopy(); badId.addProperty("route_id",true);
        reject(()->RouteMotor.parse(badId),"route ID type");
        // Known safe near waypoint must not oscillate toward an unsafe farther shortcut.
        var edge=floor();
        for(int z=1;z<=5;z++) edge.put(new Key(1,0,z),Map.of("block","minecraft:air","collision_boxes",List.of()));
        var turning=motor(List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(0,1,1),new RouteMotor.Point(0,1,2),new RouteMotor.Point(0,1,3)));
        var turn=turning.tick(pose(.801506,1,.288646,5.363637),geometry(edge));
        // Heading follows the near waypoint (yaw ~8.6), not the shortcut (~5.3); a
        // small safe turn may move in the same tick.
        check(turn.yaw()>8,"near waypoint owns heading");
        var next=turning.tick(pose(.801506,1,.288646,5.363637+turn.yaw()),geometry(edge));
        check(next.forward()>0,"safe near waypoint moves after turn");
        var hungry=new RouteMotor("r",route,new RouteMotor.Policy(true,true,true,3));
        check(!hungry.tick(new RouteMotor.Pose(.5,1,.5,0,0,-90,true,6),geometry(cells)).sprint(),"food gate");
        // Step-down: a ledge at x=0 (stand y=2), then level floor (stand y=1).
        var ledge=floor();
        ledge.put(new Key(0,1,0),Map.of("block","minecraft:stone","collision_boxes",List.of(List.of(0,0,0,1,1,1)),"fluid","minecraft:empty"));
        var down=List.of(new RouteMotor.Point(0,2,0),new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,0),new RouteMotor.Point(3,1,0));
        var descent=motor(down);
        // Falling, just short of the landing centre: the old motor eased to ~0.3.
        var falling=new RouteMotor.Pose(1.4,1.6,.5,.2,0,-90,false,20);
        check(descent.tick(falling,geometry(ledge)).forward()==1 && descent.active(),"no braking into a straight-through drop");
        // Momentum carried the body past the landing column onto the next point.
        var landed=descent.tick(pose(2.3,1,.5,-90),geometry(ledge));
        check((int)descent.status().get("next_index")==3,"overshoot onto a later point skips ahead");
        check(landed.forward()>0 && Math.abs(landed.yaw())<=8,"overshoot keeps heading instead of reversing");
        // A drop the route turns at still brakes near its landing centre.
        var corner=motor(List.of(new RouteMotor.Point(0,2,0),new RouteMotor.Point(1,1,0),new RouteMotor.Point(1,1,1)));
        var braking=corner.tick(falling,geometry(ledge));
        check(braking.forward()>0 && braking.forward()<.5 && corner.active(),"corner drop brakes");
        // Cliff edge: no floor for x >= 2. The body at x=1.82 overhangs the void.
        var cliff=floor();
        for(int x=2;x<=8;x++) for(int z=-3;z<=8;z++)
            cliff.put(new Key(x,0,z),Map.of("block","minecraft:air","collision_boxes",List.of(),"fluid","minecraft:empty"));
        var overhang=new RouteMotor.Pose(1.82,1,.5,0,0,90,true,20);
        check(geometry(cliff).safe(overhang,new RouteMotor.Point(0,1,0),1,.6,false,3),"may step back from an edge it overhangs");
        var towardVoid=new RouteMotor.Pose(1.82,1,.5,0,0,-90,true,20);
        check(!geometry(cliff).safe(towardVoid,new RouteMotor.Point(1,1,0),1,.6,false,3),"overhang may not grow");
        var onGround=new RouteMotor.Pose(1.5,1,.5,0,0,-90,true,20);
        check(!geometry(cliff).safe(onGround,new RouteMotor.Point(1,1,0),1,.6,false,3),"fully supported body may not start overhanging");
        // Off-centre beside a wall: the direct step would clip (1,1,-1); centre first.
        var walled=floor();
        walled.put(new Key(1,1,-1),Map.of("block","minecraft:stone","collision_boxes",List.of(List.of(0,0,0,1,1,1)),"fluid","minecraft:empty"));
        var squeeze=motor(List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,0)));
        // Facing the target from z=.05: the hitbox's -z side would clip the wall.
        var centre=squeeze.tick(pose(.6,1,.05,Math.toDegrees(Math.atan2(-(1.5-.6),.5-.05))),geometry(walled));
        check(squeeze.active() && centre.forward()==0 && centre.yaw()>8,"clipping step turns to recentre instead of failing");
        check(squeeze.tick(pose(.5,1,.45,-90),geometry(walled)).forward()==0 && squeeze.active(),"recentred");
        check(squeeze.tick(pose(.5,1,.45,-90),geometry(walled)).forward()>0,"step proceeds from the centre");
        // Corner: steer and keep moving when one tick's turn covers it and the new
        // heading is safe; a turn wider than 45 degrees, or an unsafe one, stops first.
        var bend=motor(List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,1)));
        var atBend=new RouteMotor.Pose(1.5,1,.5,.15,0,-90,true,20);
        var steer=bend.tick(atBend,geometry(cells));
        check(bend.active() && steer.forward()>0 && Math.abs(steer.yaw()-45)<1e-9,"corner steers while moving");
        var wide=motor(List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,0),new RouteMotor.Point(1,1,1)));
        var spin=wide.tick(new RouteMotor.Pose(1.5,1,.5,.15,0,-90+1e-3,true,20),geometry(cells));
        check(wide.active() && spin.forward()==0,"90 degree corner turns in place first");
        // A wall at x=2 beside the turn: the new heading would clip it, so turn in place.
        var walledBend=floor();
        for(int y=1;y<=2;y++) walledBend.put(new Key(2,y,1),Map.of("block","minecraft:stone","collision_boxes",List.of(List.of(0,0,0,1,1,1)),"fluid","minecraft:empty"));
        var tight=motor(List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,0),new RouteMotor.Point(1,1,1)));
        double heading=Math.toDegrees(Math.atan2(-(1.5-1.9),1.5-.5));
        var careful=tight.tick(new RouteMotor.Pose(1.9,1,.5,.15,0,heading-30,true,20),geometry(walledBend));
        check(tight.active() && careful.forward()==0 && Math.abs(careful.yaw()-30)<1e-9,"unsafe heading never moves while turning");
        // Sprint holds into a straight-through step-down and while airborne over it.
        var holdDown=motor(down);
        check(holdDown.tick(new RouteMotor.Pose(.5,2,.5,.2,0,-90,true,20),geometry(ledge)).sprint(),"sprint into a through drop");
        check(holdDown.tick(new RouteMotor.Pose(1.2,1.6,.5,.2,0,-90,false,20),geometry(ledge)).sprint(),"sprint kept while airborne");
        // Near a point the route turns at, sprint still drops.
        var intoBend=motor(List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,1)));
        check(!intoBend.tick(new RouteMotor.Pose(.9,1,.5,.2,0,-90,true,20),geometry(cells)).sprint(),"no sprint into a corner");
        System.out.println("RouteMotor: "+checks+" safety/state checks passed");
    }
}
