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
    private static Map<String,Object> water(String level) {
        return Map.of("block","minecraft:water","fluid","minecraft:water","properties",Map.of("level",level),"collision_boxes",List.of());
    }
    private static Map<String,Object> stone() {
        return Map.of("block","minecraft:stone","collision_boxes",List.of(List.of(0,0,0,1,1,1)),"fluid","minecraft:empty");
    }
    private static final RouteMotor.Policy SWIM=new RouteMotor.Policy(true,true,true,3,true,false);
    private static RouteMotor.Pose wet(double x,double y,double z) {
        return new RouteMotor.Pose(x,y,z,0,0,-90,false,20,true);
    }
    /** Pool at x=2..4, z=0: water at y=0 over a stone bed at y=-1; bank stone at (5,1,0). */
    private static Map<Key,Map<String,Object>> pool() {
        var cells=floor();
        for(int x=2;x<=4;x++) { cells.put(new Key(x,0,0),water("0")); cells.put(new Key(x,-1,0),stone()); }
        cells.put(new Key(5,1,0),stone());
        return cells;
    }
    private static void swimAndLandingChecks() {
        var g=geometry(pool());
        check(g.swimSurface(new RouteMotor.Point(3,1,0)),"known still water is a surface stance");
        check(!g.swimSurface(new RouteMotor.Point(1,1,0)),"land is not a surface stance");
        check(!g.standable(new RouteMotor.Point(3,1,0)),"water is never land support");
        Map<String,Object> bubble=Map.of("block","minecraft:bubble_column","fluid","minecraft:water","properties",Map.of("drag","false"),"collision_boxes",List.of());
        Map<String,Object> lava=Map.of("block","minecraft:lava","fluid","minecraft:lava","properties",Map.of("level","0"),"collision_boxes",List.of());
        for(var bad:List.of(water("1"),water("8"),bubble,lava)) {
            var variant=pool(); variant.put(new Key(3,0,0),bad);
            check(!geometry(variant).swimSurface(new RouteMotor.Point(3,1,0)),"flowing/bubble/lava never a stance");
        }
        var unknown=pool(); unknown.remove(new Key(3,0,0));
        check(!geometry(unknown).swimSurface(new RouteMotor.Point(3,1,0)),"unknown water is not support");
        var waterlogged=pool(); waterlogged.put(new Key(3,0,0),Map.of("block","minecraft:oak_stairs","fluid","minecraft:water","properties",Map.of("level","0"),"collision_boxes",List.of(List.of(0,0,0,1,.5,1))));
        check(!geometry(waterlogged).swimSurface(new RouteMotor.Point(3,1,0)),"waterlogged block is not still water");
        var roofed=pool(); roofed.put(new Key(3,2,0),stone());
        check(!geometry(roofed).swimSurface(new RouteMotor.Point(3,1,0)),"no headroom, no stance");
        // Enter from land, swim along the surface, exit one block up.
        var route=List.of(new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,0),new RouteMotor.Point(3,1,0),
                new RouteMotor.Point(4,1,0),new RouteMotor.Point(5,2,0));
        var motor=new RouteMotor("swim",route,SWIM);
        var enter=motor.tick(pose(1.5,1,.5,-90),g);
        check(motor.active() && enter.forward()>0 && !enter.jump(),"walks into the water without jumping");
        var surface=motor.tick(wet(2.5,.3,.5),g);
        check(motor.active() && surface.jump() && surface.forward()>0 && surface.sprint(),"holds jump and sprint-swims along the surface");
        check((int)motor.status().get("next_index")==2,"swim arrival advances");
        motor.tick(wet(3.5,.3,.5),g);
        var exit=motor.tick(wet(4.5,.3,.5),g);
        check(motor.active() && exit.jump() && !exit.sprint(),"jumps out onto a +1 bank without sprint");
        motor.tick(pose(5.5,2,.5,-90),g);
        check(motor.status().get("state").equals("done"),"exit onto land completes");
        var noSprint=new RouteMotor("s",route,new RouteMotor.Policy(true,false,true,3,true,false));
        noSprint.tick(pose(1.5,1,.5,-90),g);
        check(!noSprint.tick(wet(2.5,.3,.5),g).sprint(),"sprint policy off");
        var landOnly=new RouteMotor("l",route.subList(0,3),new RouteMotor.Policy(true,true,true,3));
        landOnly.tick(pose(1.5,1,.5,-90),g);
        check(!landOnly.active(),"water target refused without swim policy");
        reject(()->new RouteMotor("l",List.of(route.get(0),new RouteMotor.Point(2,-3,0)),new RouteMotor.Policy(true,true,true,3)),"deep drop refused without swim policy");
        var flowing=pool(); flowing.put(new Key(2,0,0),water("1"));
        var refused=new RouteMotor("f",route,SWIM); refused.tick(pose(1.5,1,.5,-90),geometry(flowing));
        check(!refused.active() && refused.status().get("reason").equals("route_target_unknown_or_changed"),"flowing target refused");
        // Water landings: a 4-block drop is legal only into still water over a known floor.
        var drop=List.of(new RouteMotor.Point(1,5,0),new RouteMotor.Point(2,1,0));
        var landing=new RouteMotor("d",drop,SWIM);
        var fall=landing.tick(pose(1.5,5,.5,-90),g);
        check(landing.active() && fall.forward()>0,"water landing admitted");
        check(g.waterLanding(new RouteMotor.Point(2,1,0)),"known floor under still water");
        var deep=pool(); deep.put(new Key(2,-1,0),water("0")); deep.put(new Key(2,-2,0),stone());
        check(geometry(deep).waterLanding(new RouteMotor.Point(2,1,0)),"deeper pool still lands");
        var noFloor=pool(); noFloor.remove(new Key(2,-1,0));
        check(!geometry(noFloor).waterLanding(new RouteMotor.Point(2,1,0)),"unknown floor refuses landing");
        var airFloor=pool(); airFloor.put(new Key(2,-1,0),Map.of("block","minecraft:air","collision_boxes",List.of(),"fluid","minecraft:empty"));
        check(!geometry(airFloor).waterLanding(new RouteMotor.Point(2,1,0)),"air floor refuses landing");
        var dry=new RouteMotor("d",drop,SWIM); dry.tick(pose(1.5,5,.5,-90),geometry(noFloor));
        check(!dry.active() && dry.status().get("reason").equals("route_drop_requires_water"),"unsafe landing refused");
        var ground=new RouteMotor("d",drop,SWIM); ground.tick(pose(1.5,5,.5,-90),geometry(floor()));
        check(!ground.active() && ground.status().get("reason").equals("route_drop_requires_water"),"a 4-block drop onto ground is still refused");
    }
    private static void sprintAscentChecks() {
        var cells=floor();
        cells.put(new Key(3,1,0),stone()); cells.put(new Key(4,1,0),stone());
        var g=geometry(cells);
        var route=List.of(new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,0),new RouteMotor.Point(3,2,0),new RouteMotor.Point(4,2,0));
        var fast=new RouteMotor.Pose(2.0,1,.5,.25,0,-90,true,20);
        var jump=motor(route).tick(fast,g);
        check(jump.jump() && jump.sprint() && jump.forward()==1,"straight traverse into an ascent keeps sprinting and jumps a stride early");
        var slow=motor(route).tick(new RouteMotor.Pose(2.0,1,.5,0,0,-90,true,20),g);
        check(!slow.jump(),"standing start keeps the short jump range");
        var run=motor(route).tick(new RouteMotor.Pose(1.2,1,.5,.25,0,-90,true,20),g);
        check(run.sprint() && !run.jump(),"sprint continues through the traverse before the ascent");
        var walking=new RouteMotor("w",route,new RouteMotor.Policy(true,false,true,3));
        check(!walking.tick(fast,g).sprint(),"sprint policy off");
        var bend=List.of(new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,0),new RouteMotor.Point(3,2,0),new RouteMotor.Point(3,2,1));
        check(!motor(bend).tick(fast,g).sprint(),"ascent that turns afterwards does not sprint");
        check(!motor(route).tick(new RouteMotor.Pose(2.0,1,.5,.25,0,-90,true,6),g).sprint(),"food gate on sprint ascent");
        var capped=floor(); capped.put(new Key(3,1,0),stone()); capped.put(new Key(4,1,0),stone()); capped.put(new Key(3,3,0),stone());
        var lowCeiling=motor(route).tick(fast,geometry(capped));
        check(!lowCeiling.sprint(),"low ceiling over the step cancels the sprint ascent");
    }
    private static void climbChecks() {
        var cells=floor();
        for(int y=1;y<=4;y++) cells.put(new Key(3,y,0),Map.of("block","minecraft:ladder","fluid","minecraft:empty",
                "properties",Map.of("facing","south"),"collision_boxes",List.of(List.of(0,0,0,1,1,.1875))));
        var g=geometry(cells);
        var policy=new RouteMotor.Policy(true,true,true,3,false,true);
        var up=List.of(new RouteMotor.Point(3,1,0),new RouteMotor.Point(3,2,0),new RouteMotor.Point(3,3,0));
        reject(()->new RouteMotor("c",up,new RouteMotor.Policy(true,true,true,3)),"vertical edge needs the climb policy");
        reject(()->new RouteMotor("c",List.of(up.get(0),up.get(2)),policy),"climb is one block per edge");
        check(g.climbable(up.get(1)),"ladder cell is climbable");
        check(!geometry(floor()).climbable(up.get(1)),"air is not climbable");
        var headless=floor(); headless.putAll(cells); headless.remove(new Key(3,3,0));
        check(!geometry(headless).climbable(up.get(1)),"unknown head cell is not climbable");
        var climber=new RouteMotor("c",up,policy);
        var climbing=climber.tick(new RouteMotor.Pose(3.5,1.2,.5,0,0,0,false,20),g);
        check(climber.active() && climbing.jump() && climbing.forward()==0 && !climbing.sprint(),"jump climbs");
        climber.tick(new RouteMotor.Pose(3.5,2.1,.5,0,0,0,false,20),g);
        check((int)climber.status().get("next_index")==2,"climb arrival advances");
        climber.tick(new RouteMotor.Pose(3.5,3.0,.5,0,0,0,false,20),g);
        check(climber.status().get("state").equals("done"),"top of the climb completes");
        var descender=new RouteMotor("c",List.of(up.get(2),up.get(1)),policy);
        check(!descender.tick(new RouteMotor.Pose(3.5,3.0,.5,0,0,0,false,20),g).jump(),"no jump descends");
        var lost=new RouteMotor("c",up,policy); lost.tick(new RouteMotor.Pose(3.5,1.2,.5,0,0,0,false,20),geometry(floor()));
        check(!lost.active(),"ladder removed mid-route refuses");
    }
    private static final double DIAGONAL_YAW=-45;
    private static void hole(Map<Key,Map<String,Object>> cells,int x,int z) {
        for(int y=-3;y<=0;y++) cells.put(new Key(x,y,z),Map.of("block","minecraft:air","collision_boxes",List.of(),"fluid","minecraft:empty"));
    }
    private static void wall(Map<Key,Map<String,Object>> cells,int x,int z) {
        cells.put(new Key(x,1,z),stone()); cells.put(new Key(x,2,z),stone());
    }
    /** Kinematic stand-in for the game: apply the yaw delta, then walk or sprint along the heading. */
    private static RouteMotor.Pose drive(RouteMotor.Pose p,RouteMotor.Input in) {
        double yaw=p.yaw()+in.yaw();
        double pace=(in.sprint()?RouteMotor.SPRINT_BLOCKS_PER_TICK:RouteMotor.WALK_BLOCKS_PER_TICK)*in.forward();
        double x=p.x()-Math.sin(Math.toRadians(yaw))*pace, z=p.z()+Math.cos(Math.toRadians(yaw))*pace;
        return new RouteMotor.Pose(x,p.y(),z,0,0,yaw,true,20);
    }
    private static boolean arrives(RouteMotor m,RouteGeometry g,RouteMotor.Pose start) {
        var p=start; var trace=new StringBuilder();
        for(int i=0;i<120&&m.active();i++) {
            var in=m.tick(p,g);
            p=drive(p,in);
            trace.append(String.format("%d x=%.3f z=%.3f yaw=%.1f f=%.2f; ",i,p.x(),p.z(),p.yaw(),in.forward()));
        }
        if(!m.status().get("state").equals("done")) throw new AssertionError(m.status()+" "+trace.substring(Math.max(0,trace.length()-900)));
        return m.status().get("state").equals("done");
    }
    private static void diagonalChecks() {
        var diagonal=List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,1));
        // Baritone MovementDiagonal cuts over a hole of any depth: both sides open, 4+ deep.
        var holes=floor(); hole(holes,1,0); hole(holes,0,1);
        var g=geometry(holes);
        var start=new RouteMotor.Pose(.5,1,.5,0,0,DIAGONAL_YAW,true,20);
        check(!g.safe(start,diagonal.get(1),1,1.0,false,3),"orthogonal rule: four corners over support, so a hole cut fails");
        check(g.safeDiagonal(start,diagonal.get(1),1,1.0,false,3),"diagonal rule: source and target corners carry the body");
        var cut=motor(diagonal);
        var move=cut.tick(new RouteMotor.Pose(.9,1,.9,0,0,DIAGONAL_YAW,true,20),g);
        check(cut.active() && move.forward()>0 && !move.sprint(),"diagonal over a 5-deep hole is accepted at walk pace");
        check(arrives(motor(diagonal),g,start),"drives across the corner cut and arrives");
        // Two diagonals in a row over four holes: walk, never sprint.
        var chain=List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,1),new RouteMotor.Point(2,1,2));
        var twin=floor(); hole(twin,1,0); hole(twin,0,1); hole(twin,2,1); hole(twin,1,2);
        var first=motor(chain).tick(start,geometry(twin));
        check(first.forward()>0 && !first.sprint(),"two diagonal holes accepted, walk only");
        check(arrives(motor(chain),geometry(twin),start),"two diagonal holes arrive");
        check(motor(chain).tick(start,geometry(floor())).sprint(),"solid sides keep the sprint through a diagonal run");
        // Edging past one stone side: bend round the blocked column.
        for(var blocked:List.of(new int[]{1,0},new int[]{0,1})) {
            var edge=floor(); wall(edge,blocked[0],blocked[1]);
            var bent=motor(diagonal);
            var turn=bent.tick(start,geometry(edge));
            check(bent.active() && turn.forward()==0 && turn.yaw()!=0 && !turn.sprint(),"edging turns toward the bend waypoint");
            check(arrives(motor(diagonal),geometry(edge),start),"edging follows the bend and arrives");
        }
        var both=floor(); wall(both,1,0); wall(both,0,1);
        var shut=motor(diagonal); shut.tick(start,geometry(both));
        check(!shut.active() && shut.status().get("reason").equals("route_swept_geometry_unknown_or_unsafe"),"both sides blocked is rejected");
        // Lava under a side rejects the cut.
        var lava=floor(); lava.put(new Key(1,0,0),Map.of("block","minecraft:lava","fluid","minecraft:lava","properties",Map.of("level","0"),"collision_boxes",List.of()));
        hole(lava,0,1);
        var burn=motor(diagonal); burn.tick(start,geometry(lava));
        check(!burn.active(),"lava under a side is rejected");
        // Orthogonal edges keep the four-corner rule: a strip of hole beside the target column.
        var strip=floor(); hole(strip,1,-1);
        var beside=new RouteMotor.Pose(.5,1,.05,0,0,-90,true,20);
        check(!geometry(strip).safe(beside,new RouteMotor.Point(1,1,0),1,1.0,false,3),"orthogonal sweep still needs all four corners");
        check(geometry(strip).safeDiagonal(beside,new RouteMotor.Point(1,1,0),1,1.0,false,3),"same sweep passes only under the diagonal rule");
        // Deep hole on a side: no sprint across the cut (cornerSprintOk), shallow is fine.
        check(!g.cornerSprintOk(diagonal.get(0),diagonal.get(1),3) && geometry(floor()).cornerSprintOk(diagonal.get(0),diagonal.get(1),3),"corner sprint only over supported sides");
    }
    private static void staircase(Map<Key,Map<String,Object>> cells,int x,int top) {
        for(int y=1;y<=top;y++) cells.put(new Key(x,y,0),stone());
    }
    private static void nativeSpeedChecks() {
        // 90 degree corner taken at speed: every tick moves, the heading flips in one tick.
        var g=geometry(floor());
        var corner=motor(List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,0),
                new RouteMotor.Point(2,1,0),new RouteMotor.Point(3,1,0),new RouteMotor.Point(3,1,1),new RouteMotor.Point(3,1,2),new RouteMotor.Point(3,1,3)));
        var p=new RouteMotor.Pose(.5,1,.5,0,0,-90,true,20); double widest=0; boolean stopped=false;
        for(int i=0;i<120&&corner.active();i++) {
            var in=corner.tick(p,g);
            if(corner.active() && in.forward()==0) stopped=true;
            widest=Math.max(widest,Math.abs(in.yaw()));
            p=drive(p,in);
        }
        check(corner.status().get("state").equals("done"),"corner route completes");
        check(!stopped && widest>=45,"a 90 degree corner (>45 in one tick) is taken without stopping");
        // Sprint stays on through straight and diagonal points.
        var diag=motor(List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,1),new RouteMotor.Point(2,1,2),new RouteMotor.Point(3,1,3)));
        check(diag.tick(new RouteMotor.Pose(1.3,1,1.3,.2,0,-45,true,20),g).sprint(),"sprint through a diagonal point");
        var line=motor(List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,0),new RouteMotor.Point(3,1,0),new RouteMotor.Point(4,1,0)));
        check(line.tick(new RouteMotor.Pose(2.9,1,.5,.25,0,-90,true,20),g).sprint(),"sprint through a straight point");
        // Staircase: consecutive ascents keep the sprint jump (Baritone sprintableAscend).
        var stairs=floor(); staircase(stairs,3,1); staircase(stairs,4,2); staircase(stairs,5,3); staircase(stairs,6,3);
        var climb=motor(List.of(new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,0),new RouteMotor.Point(3,2,0),
                new RouteMotor.Point(4,3,0),new RouteMotor.Point(5,4,0),new RouteMotor.Point(6,4,0)));
        climb.tick(new RouteMotor.Pose(2.3,1,.5,.25,0,-90,true,20),geometry(stairs));
        var second=climb.tick(new RouteMotor.Pose(3.4,2,.5,.25,0,-90,true,20),geometry(stairs));
        check(second.jump() && second.sprint() && second.forward()==1,"second stair of a staircase sprint-jumps");
        // Descend then ascend in the same direction.
        var dip=floor(); dip.put(new Key(0,1,0),stone()); dip.put(new Key(2,1,0),stone()); dip.put(new Key(3,1,0),stone());
        var valley=motor(List.of(new RouteMotor.Point(0,2,0),new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,2,0),new RouteMotor.Point(3,2,0)));
        var out=valley.tick(new RouteMotor.Pose(1.4,1,.5,.25,0,-90,true,20),geometry(dip));
        check(out.jump() && out.sprint(),"descend into an ascent in the same direction keeps the sprint jump");
        // The frozen profile keeps its 45 degree bound for Python-sent controls.
        BodyRuntime.Controls legacy=new BodyRuntime.Controls("a",0,1,2,1,1,0,90,0,false,false,false,false,false,false,false,0);
        try { BodyRuntime.validateControls(legacy); throw new AssertionError("legacy bound"); }
        catch(IllegalArgumentException expected) { checks++; }
        BodyRuntime.validateControls(new BodyRuntime.Controls("a",0,1,2,1,1,0,45,0,false,false,false,false,false,false,false,0)); checks++;
        // Water landing: a corner hanging over open air beside the landing column.
        var shore=pool();
        for(int x=1;x<=2;x++) { shore.put(new Key(x,0,-1),Map.of("block","minecraft:air","collision_boxes",List.of(),"fluid","minecraft:empty")); shore.put(new Key(x,-1,-1),stone()); }
        var drop=List.of(new RouteMotor.Point(1,5,0),new RouteMotor.Point(2,1,0));
        var edge=new RouteMotor.Pose(1.5,5,.2,0,0,-90,true,20);
        var fall=new RouteMotor("d",drop,SWIM); var walked=fall.tick(edge,geometry(shore));
        check(fall.active() && walked.forward()>0,"water-landing sweep accepts a corner over open air with a known floor");
        var bare=pool(); bare.putAll(shore); bare.remove(new Key(1,-1,-1)); bare.remove(new Key(2,-1,-1));
        var bad=new RouteMotor("d",drop,SWIM); bad.tick(edge,geometry(bare));
        check(!bad.active(),"unknown floor under the open column is refused");
        var lavaBed=pool(); lavaBed.putAll(shore);
        for(int x=1;x<=2;x++) lavaBed.put(new Key(x,-1,-1),Map.of("block","minecraft:lava","fluid","minecraft:lava","properties",Map.of("level","0"),"collision_boxes",List.of()));
        var burn=new RouteMotor("d",drop,SWIM); burn.tick(edge,geometry(lavaBed));
        check(!burn.active(),"lava under the open column is refused");
    }
    private static void resyncAndSwimChecks() {
        var g=geometry(pool());
        // Stale start: walked into a later cell during planning.
        var diagonal=new RouteMotor("r",List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,1),new RouteMotor.Point(2,1,1)),new RouteMotor.Policy(true,true,true,3));
        diagonal.tick(pose(1.9,1,1.9,-90),geometry(floor()));
        check(diagonal.active() && (int)diagonal.status().get("next_index")>=1,"grounded in a later cell re-syncs instead of route_start_displaced");
        // Dropped into water beside route[1]/route[2] while the plan was computed.
        var route=List.of(new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,0),new RouteMotor.Point(3,1,0),new RouteMotor.Point(4,1,0));
        var swimmer=new RouteMotor("w",route,SWIM);
        swimmer.tick(wet(3.5,.3,.5),g);
        check(swimmer.active() && (int)swimmer.status().get("next_index")>=2,"swimming in a later column re-syncs");
        // Airborne but within 0.6 of a later point's centre at its height.
        var near=new RouteMotor("n",List.of(new RouteMotor.Point(-2,1,0),new RouteMotor.Point(-1,1,0),new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,0)),new RouteMotor.Policy(true,true,true,3));
        near.tick(new RouteMotor.Pose(1.05,1,.5,0,0,-90,false,20),geometry(floor()));
        check(near.active() && (int)near.status().get("next_index")>=2,"within 0.6 of a later centre re-syncs");
        var lost=motor(List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,0)));
        lost.tick(pose(6.5,1,.5,-90),geometry(floor()));
        check(!lost.active() && lost.status().get("reason").equals("route_start_displaced"),"far from every early point is still displaced");
        // Swim: in the target column but below the stance, rise without steering.
        var up=new RouteMotor("u",route.subList(0,3),SWIM);
        up.tick(pose(1.5,1,.5,-90),g);
        up.tick(wet(2.5,.3,.5),g);
        var rise=up.tick(wet(3.5,-.2,.5),g);
        check(up.active() && rise.forward()==0 && rise.yaw()==0 && rise.jump(),"below the stance in the column: hold jump, no steering");
        up.tick(wet(3.5,.05,.5),g);
        check(up.status().get("state").equals("done"),"feet within 1.0 below the stance arrive");
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
        check(RouteMotor.parse(swimming).active(),"swim policy admitted");
        var climbing=swimming.deepCopy(); climbing.getAsJsonObject("movement_policy").addProperty("allow_climb",true);
        check(RouteMotor.parse(climbing).active(),"optional climb policy admitted");
        var stray=climbing.deepCopy(); stray.getAsJsonObject("movement_policy").addProperty("allow_fly",true);
        reject(()->RouteMotor.parse(stray),"unknown policy field refused");
        var badClimb=climbing.deepCopy(); badClimb.getAsJsonObject("movement_policy").addProperty("allow_climb",1);
        reject(()->RouteMotor.parse(badClimb),"climb coercion refused");
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
        check(wide.active() && spin.forward()>0 && Math.abs(spin.yaw()-(90-1e-3))<1e-6,"90 degree corner steers fully and moves in the same tick");
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
        check(intoBend.tick(new RouteMotor.Pose(.9,1,.5,.2,0,-90,true,20),geometry(cells)).sprint(),"sprint carries into a corner when the next heading is safe");
        var walledCorner=floor(); wall(walledCorner,1,1);
        check(!motor(List.of(new RouteMotor.Point(0,1,0),new RouteMotor.Point(1,1,0),new RouteMotor.Point(2,1,1)))
                .tick(new RouteMotor.Pose(.9,1,.5,.2,0,-90,true,20),geometry(walledCorner)).sprint(),"no sprint when the post-corner sweep clips");
        swimAndLandingChecks(); nativeSpeedChecks(); sprintAscentChecks(); climbChecks(); diagonalChecks(); resyncAndSwimChecks();
        System.out.println("RouteMotor: "+checks+" safety/state checks passed");
    }
}
